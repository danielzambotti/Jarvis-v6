"""
core/security/threat_modeler.py — Proactive STRIDE Threat Modeling Engine
==========================================================================
Shifts AppSec to Stage 0 of the Software Factory: threats are identified and
risk-scored BEFORE a single line of code is written.  The resulting threat
model is injected into the Developer and Test Master prompts so:
  - The Developer writes code that actively mitigates identified threats.
  - The Test Master writes tests that verify each mitigation.

STRIDE CATEGORIES
-----------------
  S — Spoofing       (identity forgery, impersonation)
  T — Tampering      (unauthorized data/code modification)
  R — Repudiation    (deny performing an action, audit gaps)
  I — Info Disclosure (data leakage, overly verbose errors)
  D — Denial of Service (resource exhaustion, availability)
  E — Elevation of Privilege (bypass authorization, privilege escalation)

PIPELINE POSITION
-----------------
  User request
      │
      ▼  Stage 0  ThreatModelEngine.generate_model() ← THIS MODULE
      │             STRIDE analysis → attack surface → risk scores
      │             Threat model injected into Developer + Test Master prompts
      │
      ▼  Stage 1  Architect
      ▼  Stage 2  Developer  (receives threat context → writes mitigations)
      ▼  Stage 3  Test Master (receives threat context → writes security tests)
      ▼  Stage 3.5 Sandbox
      ▼  Stage 3.7 SDLC gate
      ▼  Stage 4  Guardian
      ▼  Stage 5  GitOps PR

Governed by: ADR-011-Threat-Modeling-Engine
Depends on:  ADR-010-Secure-SDLC-Automation, ADR-005-AI-Security-Guardrails
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_CHAT_URL = _OLLAMA_HOST + "/api/chat"
_DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "llama3")
_LLM_TIMEOUT = 120  # seconds — threat model is concise, should be fast

# ── STRIDE analysis prompt ─────────────────────────────────────────────────────
#
# This is the core of the engine.  The LLM is instructed to act as a senior
# AppSec architect performing a STRIDE threat model.  The output schema is
# strictly defined in the prompt so it can be parsed without an LLM call.
#
STRIDE_SYSTEM_PROMPT = """\
You are a Principal Application Security Architect performing a proactive \
STRIDE threat model. Your output is consumed by downstream code-generation \
agents, so it MUST follow the exact schema below — no deviations.

STRIDE CATEGORIES:
  S = Spoofing        (identity forgery, auth bypass, session hijack)
  T = Tampering       (unauthorized input modification, injection, MITM)
  R = Repudiation     (missing audit logs, deniable actions)
  I = Info Disclosure (data leakage, verbose errors, insecure storage)
  D = Denial of Service (resource exhaustion, ReDoS, unbounded loops)
  E = Elevation of Privilege (IDOR, missing authz checks, unsafe deserialization)

RISK SCORE FORMULA: HIGH = likely AND high-impact | MEDIUM = either | LOW = unlikely AND low-impact

OUTPUT SCHEMA (use these exact section headers):
## STRIDE Analysis
| Category | Threat | Attack Vector | Risk |
|---|---|---|---|
| S | <threat> | <vector> | HIGH/MEDIUM/LOW |
... (one row per identified threat)

## Attack Surface
- **Endpoints**: <list of exposed HTTP/API/CLI endpoints>
- **Data Stores**: <databases, files, caches touched by this feature>
- **Trust Boundaries**: <where data crosses privilege zones>
- **External Dependencies**: <third-party services or libraries>

## Top Risks (ordered by severity)
1. [HIGH] <concise risk statement + recommended mitigation>
2. [MEDIUM] ...
3. [LOW] ...

## Developer Security Directives
> Concise, actionable bullet points for the Developer agent.
> Each bullet = one concrete coding requirement.
- [ ] <directive 1>
- [ ] <directive 2>
...

## Test Master Security Directives
> Concise, actionable bullet points for the Test Master agent.
> Each bullet = one concrete test case requirement.
- [ ] <test requirement 1>
- [ ] <test requirement 2>
...
"""

STRIDE_USER_TEMPLATE = """\
Perform a STRIDE threat model for the following software feature request.
Be concise — the report will be injected into LLM code-generation prompts,
so every line must be signal, not noise.

FEATURE REQUEST:
{user_input}
"""


# ── Result types ───────────────────────────────────────────────────────────────

@dataclass
class ThreatFinding:
    """One row from the STRIDE analysis table."""
    category: str       # S / T / R / I / D / E
    threat: str
    vector: str
    risk: str           # HIGH / MEDIUM / LOW


@dataclass
class ThreatModel:
    """
    Full output of ThreatModelEngine.generate_model().
    Contains both the raw LLM report and structured extracts.
    """
    markdown_report: str
    findings: list[ThreatFinding] = field(default_factory=list)
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    attack_surface: str = ""
    top_risks: str = ""
    dev_directives: str = ""       # injected into Developer system prompt
    test_directives: str = ""      # injected into Test Master system prompt
    error: Optional[str] = None    # set if LLM call failed

    @property
    def ok(self) -> bool:
        return self.error is None

    def risk_banner(self) -> str:
        parts = []
        if self.high_count:
            parts.append(f"{self.high_count} HIGH")
        if self.medium_count:
            parts.append(f"{self.medium_count} MEDIUM")
        if self.low_count:
            parts.append(f"{self.low_count} LOW")
        return ", ".join(parts) if parts else "No threats identified"

    def dev_context_block(self) -> str:
        """
        Compact block injected into the Developer agent's user message.
        Keeps the injection under ~600 tokens so it doesn't crowd out code space.
        """
        return (
            "=== THREAT MODEL (read before writing code) ===\n"
            f"{self.top_risks}\n\n"
            "--- SECURITY DIRECTIVES (you MUST implement all of these) ---\n"
            f"{self.dev_directives}\n"
            "=== END THREAT MODEL ===\n"
        )

    def test_context_block(self) -> str:
        """Compact block injected into the Test Master agent's user message."""
        return (
            "=== THREAT MODEL — TEST REQUIREMENTS ===\n"
            f"Risk summary: {self.risk_banner()}\n\n"
            "--- SECURITY TEST DIRECTIVES (write a test for EACH of these) ---\n"
            f"{self.test_directives}\n"
            "=== END THREAT MODEL ===\n"
        )


# ── Parser ─────────────────────────────────────────────────────────────────────

def _parse_report(report: str) -> ThreatModel:
    """
    Extract structured data from the LLM's Markdown output.
    Falls back gracefully — partial parses are still useful.
    """
    findings: list[ThreatFinding] = []

    # Parse STRIDE table rows: | S | threat | vector | HIGH |
    table_re = re.compile(
        r"\|\s*([STRIДЕ]{1,2})\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(HIGH|MEDIUM|LOW)\s*\|",
        re.IGNORECASE,
    )
    for m in table_re.finditer(report):
        findings.append(ThreatFinding(
            category=m.group(1).strip().upper(),
            threat=m.group(2).strip(),
            vector=m.group(3).strip(),
            risk=m.group(4).strip().upper(),
        ))

    high   = sum(1 for f in findings if f.risk == "HIGH")
    medium = sum(1 for f in findings if f.risk == "MEDIUM")
    low    = sum(1 for f in findings if f.risk == "LOW")

    def _extract_section(header: str) -> str:
        """Extract text under a ## header until the next ## header."""
        pattern = rf"##\s+{re.escape(header)}\s*\n(.*?)(?=\n##\s|\Z)"
        m = re.search(pattern, report, re.DOTALL | re.IGNORECASE)
        return m.group(1).strip() if m else ""

    attack_surface = _extract_section("Attack Surface")
    top_risks      = _extract_section("Top Risks")
    dev_dir        = _extract_section("Developer Security Directives")
    test_dir       = _extract_section("Test Master Security Directives")

    return ThreatModel(
        markdown_report=report,
        findings=findings,
        high_count=high,
        medium_count=medium,
        low_count=low,
        attack_surface=attack_surface,
        top_risks=top_risks,
        dev_directives=dev_dir,
        test_directives=test_dir,
    )


# ── Engine ─────────────────────────────────────────────────────────────────────

class ThreatModelEngine:
    """
    Proactive STRIDE threat modeling engine.
    Call generate_model(user_input) at Stage 0 of the Software Factory.
    """

    def __init__(self, model: str = _DEFAULT_MODEL) -> None:
        self._model = model

    def generate_model(self, user_input: str) -> ThreatModel:
        """
        Run a STRIDE threat model against `user_input`.

        Returns a ThreatModel.  On LLM failure, returns a degraded model
        with error set — callers receive a warning but the pipeline continues
        (threat modeling failure is a WARN, not a pipeline BLOCK, to preserve
        availability when Ollama is unreachable).
        """
        logger.info("[THREAT] Starting STRIDE analysis for: %.80s...", user_input)

        prompt = STRIDE_USER_TEMPLATE.format(user_input=user_input.strip())
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "system": STRIDE_SYSTEM_PROMPT,
            "stream": False,
            "options": {
                "temperature": 0.1,   # deterministic — security analysis, not creativity
                "num_predict": 1024,  # concise report — directives must fit in agent context
            },
        }

        try:
            resp = requests.post(_OLLAMA_CHAT_URL, json=payload, timeout=_LLM_TIMEOUT)
            resp.raise_for_status()
            report = resp.json().get("message", {}).get("content", "").strip()
        except requests.exceptions.ConnectionError:
            msg = "Ollama unreachable — threat model skipped (pipeline continues)."
            logger.warning("[THREAT] %s", msg)
            return ThreatModel(
                markdown_report=f"*{msg}*",
                error=msg,
            )
        except Exception as exc:
            msg = f"Threat model LLM call failed: {exc}"
            logger.error("[THREAT] %s", msg)
            return ThreatModel(
                markdown_report=f"*{msg}*",
                error=msg,
            )

        if not report:
            msg = "Threat model returned empty response."
            logger.warning("[THREAT] %s", msg)
            return ThreatModel(markdown_report="*(empty)*", error=msg)

        model_result = _parse_report(report)
        logger.info(
            "[THREAT] STRIDE complete — %s findings (%s)",
            len(model_result.findings),
            model_result.risk_banner(),
        )
        return model_result


# ── Singleton ──────────────────────────────────────────────────────────────────

_instance: Optional[ThreatModelEngine] = None


def get_threat_modeler(model: str = _DEFAULT_MODEL) -> ThreatModelEngine:
    global _instance
    if _instance is None:
        _instance = ThreatModelEngine(model=model)
    return _instance
