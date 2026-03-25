"""
core/security/guardrails.py — AI Input Guardrail Engine
=========================================================
Validates user prompts BEFORE they reach any LLM agent.

This is the last line of defence between a user-controlled string and the
Ollama inference engine. It complements security.py (which guards the
Telegram/HTTP input layer) by focusing specifically on adversarial prompt
patterns that target the LLM itself rather than the host OS.

THREAT MODEL
------------
  Prompt Injection    — user embeds instructions that override the system
                        prompt, hijack the agent persona, or exfiltrate
                        context (e.g. vault secrets, prior conversation).

  Role Override       — user attempts to redefine the agent's identity
                        ("You are now DAN", "Act as an uncensored model").

  Context Extraction  — user probes for system prompt content, config
                        values, or internal tool definitions.

  Chain-of-thought    — user asks the model to "think step by step" about
                        bypassing its constraints.

DESIGN PRINCIPLES
-----------------
  1. Fail-closed: a SecurityException stops the pipeline; the input is
     never forwarded to the LLM.
  2. Audit-first: every block is logged with the firing rule label.
     Matched text is truncated to 80 chars in logs (no full exfiltration).
  3. Layered: heuristic regex catches known patterns; a confidence
     threshold aggregates soft signals for novel phrasing.
  4. Complementary: does NOT duplicate security.py injection patterns —
     it extends them with LLM-specific vectors.

Usage:
    from core.security.guardrails import get_input_validator

    validator = get_input_validator()
    validator.check_prompt(user_input)   # raises SecurityException if unsafe
    # or
    is_safe, reason = validator.is_safe(user_input)
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ── Custom Exception ──────────────────────────────────────────────────────────

class SecurityException(Exception):
    """
    Raised when a prompt fails guardrail validation.
    Callers MUST catch this and return a safe error response to the user —
    never forward the offending input to the LLM.
    """
    def __init__(self, rule: str, excerpt: str) -> None:
        self.rule = rule
        self.excerpt = excerpt
        super().__init__(f"[GUARDRAIL] Blocked by rule '{rule}': ...{excerpt}...")


# ── Rule Definitions ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GuardrailRule:
    label: str          # Audit-log identifier
    regex: str          # Pattern to match (case-insensitive)
    weight: float       # Contribution to soft-block score (0.0–1.0)
    hard_block: bool    # True = immediate SecurityException on match


_RULES: list[GuardrailRule] = [

    # ── Hard blocks: unambiguous injection intent ─────────────────────────────

    GuardrailRule(
        label="IGNORE_INSTRUCTIONS",
        regex=r"ignore\s+(all\s+)?(previous|prior|above|earlier|preceding)\s+(instructions?|prompts?|context|rules?)",
        weight=1.0, hard_block=True,
    ),
    GuardrailRule(
        label="DISREGARD_INSTRUCTIONS",
        regex=r"disregard\s+(all\s+)?(previous|prior|above|your)\s+(instructions?|prompts?|rules?|constraints?)",
        weight=1.0, hard_block=True,
    ),
    GuardrailRule(
        label="NEW_INSTRUCTIONS_OVERRIDE",
        regex=r"(your\s+new\s+instructions?|from\s+now\s+on\s+you\s+(are|will|must|should)|new\s+system\s+prompt)",
        weight=1.0, hard_block=True,
    ),
    GuardrailRule(
        label="ROLE_OVERRIDE_DAN",
        regex=r"\b(you\s+are\s+now|act\s+as|pretend\s+(to\s+be|you\s+are)|roleplay\s+as)\s+(dan|jailbreak|evil\s+(ai|bot|model)|uncensored|unfiltered|devel?oper\s+mode)",
        weight=1.0, hard_block=True,
    ),
    GuardrailRule(
        label="BYPASS_SAFETY",
        regex=r"(bypass|circumvent|disable|override|remove|ignore)\s+(safety|security|ethics?|guidelines?|restrictions?|filters?|guardrails?|constraints?|limitations?)",
        weight=1.0, hard_block=True,
    ),
    GuardrailRule(
        label="SYSTEM_PROMPT_EXFIL",
        regex=r"(print|show|reveal|output|repeat|tell\s+me|what\s+(is|are))\s+(your\s+)?(system\s+prompt|initial\s+prompt|hidden\s+instructions?|config(uration)?|secret\s+key)",
        weight=1.0, hard_block=True,
    ),
    GuardrailRule(
        label="PROMPT_DELIMITER_INJECTION",
        # Attempts to close the current message and inject a new system turn
        regex=r"(\]\]\]|\[INST\]|<\|im_start\|>|<\|system\|>|###\s*system|<system>|\[system\])",
        weight=1.0, hard_block=True,
    ),
    GuardrailRule(
        label="CONTEXT_WINDOW_DUMP",
        regex=r"(dump|print|output|list|show)\s+(all\s+)?(your\s+)?(context|memory|conversation\s+history|previous\s+messages?|tools?|functions?)",
        weight=1.0, hard_block=True,
    ),

    # ── Soft signals: suspicious but potentially legitimate ───────────────────
    # These accumulate weight; if total >= SOFT_BLOCK_THRESHOLD → block.

    GuardrailRule(
        label="YOU_ARE_NOW",
        regex=r"\byou\s+are\s+now\b",
        weight=0.5, hard_block=False,
    ),
    GuardrailRule(
        label="SIMULATE_UNRESTRICTED",
        regex=r"(simulat|imagin|hypotheticall|theoretically|for\s+(fun|educational\s+purposes?|a\s+story))\w*\s+.{0,40}(no\s+(rules?|restrictions?|limits?)|unrestricted|uncensored)",
        weight=0.6, hard_block=False,
    ),
    GuardrailRule(
        label="JAILBREAK_KEYWORD",
        regex=r"\b(jailbreak|jail\s*break|do\s+anything\s+now|dan\s+mode|dev\s+mode|god\s+mode)\b",
        weight=0.7, hard_block=False,
    ),
    GuardrailRule(
        label="SUDO_ESCALATION",
        regex=r"\b(sudo|root\s+access|admin\s+mode|superuser|as\s+root)\b",
        weight=0.4, hard_block=False,
    ),
    GuardrailRule(
        label="TOKEN_SMUGGLING",
        # Unicode lookalike characters used to evade regex (e.g. "ıgnore")
        regex=r"[\u0131\u0456\u04cf\u1d0b\u1e2c\uff49]",   # dotless-i and i lookalikes
        weight=0.5, hard_block=False,
    ),
]

# Sum of all soft weights that triggers a block
_SOFT_BLOCK_THRESHOLD: float = 0.9


# ── Compiled Rule Registry ────────────────────────────────────────────────────

@dataclass
class _CompiledRule:
    label: str
    pattern: re.Pattern
    weight: float
    hard_block: bool


def _compile(rules: list[GuardrailRule]) -> list[_CompiledRule]:
    out = []
    for r in rules:
        try:
            out.append(_CompiledRule(
                label=r.label,
                pattern=re.compile(r.regex, re.IGNORECASE | re.UNICODE),
                weight=r.weight,
                hard_block=r.hard_block,
            ))
        except re.error as exc:
            logger.error("[GUARDRAIL] Failed to compile rule '%s': %s", r.label, exc)
    return out


# ── InputValidator ────────────────────────────────────────────────────────────

class InputValidator:
    """
    Validates prompts before they reach an LLM agent.
    Thread-safe (stateless after __init__).
    """

    def __init__(self, rules: Optional[list[GuardrailRule]] = None) -> None:
        src = rules if rules is not None else _RULES
        self._rules = _compile(src)
        self._hard = [r for r in self._rules if r.hard_block]
        self._soft = [r for r in self._rules if not r.hard_block]
        logger.info(
            "[GUARDRAIL] Initialized: %d hard rules, %d soft rules (threshold=%.1f).",
            len(self._hard), len(self._soft), _SOFT_BLOCK_THRESHOLD,
        )

    def check_prompt(self, text: str) -> None:
        """
        Validate `text`. Raises SecurityException immediately on any violation.

        Call this before forwarding user input to any LLM agent.
        """
        if not text or not text.strip():
            return

        # Pass 1: hard blocks — immediate raise on first match
        for rule in self._hard:
            m = rule.pattern.search(text)
            if m:
                excerpt = text[max(0, m.start() - 20): m.end() + 20][:80]
                logger.error(
                    "[GUARDRAIL] HARD BLOCK — rule=%s excerpt=%r",
                    rule.label, excerpt,
                )
                raise SecurityException(rule=rule.label, excerpt=excerpt)

        # Pass 2: soft signals — accumulate score
        score: float = 0.0
        fired: list[str] = []
        for rule in self._soft:
            if rule.pattern.search(text):
                score += rule.weight
                fired.append(rule.label)
                if score >= _SOFT_BLOCK_THRESHOLD:
                    excerpt = text[:80]
                    logger.error(
                        "[GUARDRAIL] SOFT BLOCK — score=%.2f rules=%s excerpt=%r",
                        score, fired, excerpt,
                    )
                    raise SecurityException(
                        rule=f"SOFT_AGGREGATE({','.join(fired)})",
                        excerpt=excerpt,
                    )

        if fired:
            logger.warning(
                "[GUARDRAIL] Soft signals detected (score=%.2f < threshold=%.2f): %s — ALLOWED",
                score, _SOFT_BLOCK_THRESHOLD, fired,
            )

    def is_safe(self, text: str) -> tuple[bool, str]:
        """
        Non-raising variant. Returns (True, "OK") or (False, reason).
        Useful for pre-flight checks without try/except boilerplate.
        """
        try:
            self.check_prompt(text)
            return True, "OK"
        except SecurityException as exc:
            return False, str(exc)


# ── Singleton ─────────────────────────────────────────────────────────────────

_instance: Optional[InputValidator] = None


def get_input_validator() -> InputValidator:
    global _instance
    if _instance is None:
        _instance = InputValidator()
    return _instance
