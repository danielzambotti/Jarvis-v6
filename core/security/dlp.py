"""
core/security/dlp.py — Data Loss Prevention (DLP) Engine
==========================================================
Classifies and redacts sensitive data before it crosses trust boundaries:
  - LLM output → workspace files
  - Sandbox stdout/stderr → logs
  - Factory final output → user-facing response

CLASSIFICATION TIERS
---------------------
  REDACTED_PII     — Personal Identifiable Information (GDPR / LGPD)
                     Email addresses, IPv4, credit card numbers

  REDACTED_SECRET  — Credentials and key material (PCI-DSS / SOC 2)
                     AWS access keys, GCP API keys, generic bearer tokens,
                     PEM private keys, generic high-entropy secret patterns

DESIGN NOTES
-------------
- Patterns are compiled once at import time (zero per-call regex overhead).
- Each pattern carries a `label` for audit logging — you can see exactly
  which rule fired without exposing the matched value.
- `sanitize_text()` is the single public entry point; it is idempotent
  (running it twice produces the same result).
- The engine is intentionally stateless: no caching, no side effects.
  Thread-safe by default.

Usage:
    from core.security.dlp import get_dlp_engine

    dlp = get_dlp_engine()
    safe_output = dlp.sanitize_text(raw_llm_output)
    findings = dlp.scan(raw_llm_output)  # audit without redacting
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ── Pattern Definitions ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class DLPPattern:
    label: str            # Human-readable name for audit logs
    regex: str            # Raw regex string
    replacement: str      # What to substitute on match
    tier: str             # "PII" or "SECRET"


# Ordered from most-specific to least-specific to avoid partial shadowing.
_RAW_PATTERNS: list[DLPPattern] = [

    # ── Secrets / Credentials ─────────────────────────────────────────────────

    DLPPattern(
        label="PEM_PRIVATE_KEY",
        regex=r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----[\s\S]+?-----END (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----",
        replacement="[REDACTED_SECRET:PEM_PRIVATE_KEY]",
        tier="SECRET",
    ),
    DLPPattern(
        label="AWS_ACCESS_KEY_ID",
        # AWS access key IDs are always 20 uppercase alphanumeric chars starting with AKIA/ASIA/AROA/AIDA/ANPA/ANVA/AIPA
        regex=r"\b(AKIA|ASIA|AROA|AIDA|ANPA|ANVA|AIPA)[A-Z0-9]{16}\b",
        replacement="[REDACTED_SECRET:AWS_ACCESS_KEY_ID]",
        tier="SECRET",
    ),
    DLPPattern(
        label="AWS_SECRET_ACCESS_KEY",
        # 40-char base64 string preceded by common assignment patterns
        regex=r"(?i)(?:aws[_\-\s]?secret[_\-\s]?(?:access[_\-\s]?)?key|SecretAccessKey)[\"'\s:=]+([A-Za-z0-9/+]{40})",
        replacement="[REDACTED_SECRET:AWS_SECRET_ACCESS_KEY]",
        tier="SECRET",
    ),
    DLPPattern(
        label="GCP_API_KEY",
        # GCP API keys start with AIza and are 39 chars total
        regex=r"\bAIza[A-Za-z0-9_\-]{35}\b",
        replacement="[REDACTED_SECRET:GCP_API_KEY]",
        tier="SECRET",
    ),
    DLPPattern(
        label="GENERIC_BEARER_TOKEN",
        # Authorization header or variable assignment with Bearer token
        regex=r"(?i)(?:bearer\s+|Authorization[\"'\s:=]+Bearer\s+)([A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_=]+\.?[A-Za-z0-9\-_.+/=]*)",
        replacement="[REDACTED_SECRET:BEARER_TOKEN]",
        tier="SECRET",
    ),
    DLPPattern(
        label="GENERIC_SECRET_ASSIGNMENT",
        # Catches: SECRET_KEY="abc123", api_token: 'xyz', password = "hunter2"
        regex=r"(?i)(?:secret[_\-]?key|api[_\-]?(?:key|token|secret)|access[_\-]?token|auth[_\-]?token|private[_\-]?key|password|passwd|pwd)[\"'\s]*[:=][\"'\s]*([A-Za-z0-9!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?`~]{8,})",
        replacement="[REDACTED_SECRET:CREDENTIAL_ASSIGNMENT]",
        tier="SECRET",
    ),

    # ── PII ───────────────────────────────────────────────────────────────────

    DLPPattern(
        label="CREDIT_CARD",
        # Luhn-checkable 13–19 digit sequences (Visa, MC, Amex, Discover, etc.)
        # Separators: spaces or hyphens between groups
        regex=r"\b(?:4[0-9]{12}(?:[0-9]{3,6})?|5[1-5][0-9]{14}|2[2-7][0-9]{14}|3[47][0-9]{13}|3(?:0[0-5]|[68][0-9])[0-9]{11}|6(?:011|5[0-9]{2})[0-9]{12,15}|(?:2131|1800|35\d{3})\d{11})(?:[\s\-]?\d{4})?\b",
        replacement="[REDACTED_PII:CREDIT_CARD]",
        tier="PII",
    ),
    DLPPattern(
        label="EMAIL_ADDRESS",
        regex=r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
        replacement="[REDACTED_PII:EMAIL]",
        tier="PII",
    ),
    DLPPattern(
        label="IPV4_ADDRESS",
        # Excludes loopback (127.x.x.x) and link-local (169.254.x.x) — those
        # are infrastructure addresses, not PII.
        regex=r"\b(?!127\.)(?!169\.254\.)(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b",
        replacement="[REDACTED_PII:IPV4]",
        tier="PII",
    ),

    # ── LGPD-specific PII (Brazil) ────────────────────────────────────────────

    DLPPattern(
        label="BRAZIL_CPF",
        # Format: 000.000.000-00  (with separators — bare 11-digit runs excluded
        # to avoid false-positives on phone numbers and version strings)
        regex=r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b",
        replacement="[REDACTED_PII:CPF]",
        tier="PII",
    ),
    DLPPattern(
        label="BRAZIL_PHONE",
        # Covers: +55 (11) 91234-5678  |  +55 11 912345678  |  (11) 9876-5432
        # Handles landline (8-digit local) and mobile (9-digit local) numbers.
        regex=r"(?:\+55\s?)?(?:\(\d{2}\)|\d{2})[\s\-]?\d{4,5}[\s\-]?\d{4}\b",
        replacement="[REDACTED_PII:PHONE_BR]",
        tier="PII",
    ),
]


# ── Compiled Pattern Registry ─────────────────────────────────────────────────

@dataclass
class CompiledPattern:
    label: str
    pattern: re.Pattern
    replacement: str
    tier: str


def _compile_patterns(raw: list[DLPPattern]) -> list[CompiledPattern]:
    compiled = []
    for p in raw:
        try:
            compiled.append(CompiledPattern(
                label=p.label,
                pattern=re.compile(p.regex, re.MULTILINE),
                replacement=p.replacement,
                tier=p.tier,
            ))
        except re.error as exc:
            logger.error("[DLP] Failed to compile pattern '%s': %s", p.label, exc)
    return compiled


# ── Finding ───────────────────────────────────────────────────────────────────

@dataclass
class DLPFinding:
    label: str
    tier: str
    count: int
    # NOTE: matched values are intentionally NOT stored to prevent logging them


# ── DataLossPrevention Engine ─────────────────────────────────────────────────

class DataLossPrevention:
    """
    Stateless DLP engine. Compile once, call sanitize_text() everywhere.

    Thread-safe: re.Pattern.sub() is thread-safe in CPython.
    """

    def __init__(self, patterns: Optional[list[DLPPattern]] = None) -> None:
        src = patterns if patterns is not None else _RAW_PATTERNS
        self._patterns: list[CompiledPattern] = _compile_patterns(src)
        logger.info("[DLP] Engine initialized with %d patterns.", len(self._patterns))

    def sanitize_text(self, text: str) -> tuple[str, list[DLPFinding]]:
        """
        Redact all matching sensitive data in `text`.

        Returns:
            (sanitized_text, findings)

            sanitized_text: text with sensitive values replaced by labels.
            findings:       list of DLPFinding (what fired, how many times).
                            Values are NEVER stored — only counts and labels.

        This method is idempotent: sanitize_text(sanitize_text(x)) == sanitize_text(x).
        """
        if not text:
            return text, []

        result = text
        findings: list[DLPFinding] = []

        for cp in self._patterns:
            matches = cp.pattern.findall(result)
            if matches:
                count = len(matches)
                result = cp.pattern.sub(cp.replacement, result)
                findings.append(DLPFinding(label=cp.label, tier=cp.tier, count=count))
                logger.warning(
                    "[DLP] REDACTED %d occurrence(s) of %s (%s)",
                    count, cp.label, cp.tier,
                )

        if findings:
            total = sum(f.count for f in findings)
            logger.warning(
                "[DLP] Sanitization complete: %d sensitive item(s) redacted across %d pattern(s).",
                total, len(findings),
            )

        return result, findings

    def scan(self, text: str) -> list[DLPFinding]:
        """
        Audit-only scan: returns findings without modifying text.
        Use for compliance reporting or pre-flight checks.
        """
        _, findings = self.sanitize_text(text)
        return findings

    def is_clean(self, text: str) -> bool:
        """Returns True if no sensitive data was detected."""
        return len(self.scan(text)) == 0


# ── Module-Level Singleton ────────────────────────────────────────────────────

_instance: Optional[DataLossPrevention] = None


def get_dlp_engine() -> DataLossPrevention:
    """Returns the shared DLP engine singleton."""
    global _instance
    if _instance is None:
        _instance = DataLossPrevention()
    return _instance
