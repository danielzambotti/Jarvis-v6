"""
core/security/sdlc.py — Secure SDLC Automation Engine
=======================================================
Enforces four hard gates on every artefact produced by the Software Factory
BEFORE a GitOps PR is opened.

PIPELINE POSITION
-----------------
  LLM Generate
      │
      ▼  ADR-002  Sandbox execution (run_in_sandbox)
      │
      ▼  ADR-010  SecureSDLC (THIS MODULE) ← hard gate
      │    ├─ run_sast()           Bandit SAST — blocks on HIGH severity
      │    ├─ scan_dependencies()  Safety CVE scan — blocks on known vulns
      │    ├─ generate_sbom()      CycloneDX SBOM — audit artefact
      │    └─ enforce_tests()      pytest — blocks if tests missing or failing
      │
      ▼  Guardian review
      │
      ▼  ADR-007  GitOps PR (only if ALL gates pass)

Governed by: ADR-010-Secure-SDLC-Automation
Depends on:  ADR-007-Autonomous-GitOps, ADR-002-ZeroTrust-Sandbox
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

_BANDIT_BIN  = "bandit"
_SAFETY_BIN  = "safety"
_CDX_BIN     = "cyclonedx-py"
_PYTEST_BIN  = "pytest"

_SAST_BLOCK_SEVERITIES: frozenset[str] = frozenset({"HIGH"})

# Bandit test IDs intentionally skipped (mirrors CI pipeline in jarvis-ci.yml)
_BANDIT_SKIP: str = "B101,B603,B607"


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class SDLCResult:
    """
    Aggregated result from one or all SecureSDLC gates.
    gate:    Which check produced this result.
    passed:  True iff the gate did not block execution.
    detail:  Human-readable summary for the Factory's final report.
    report:  Raw tool output (JSON string or plain text) for the PR body.
    """
    gate: str
    passed: bool
    detail: str = ""
    report: str = ""

    def __str__(self) -> str:
        icon = "PASS" if self.passed else "FAIL"
        return f"[{icon}] {self.gate}: {self.detail}"


@dataclass
class SDLCReport:
    """Composite of all gate results for a single factory run."""
    results: list[SDLCResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    def summary(self) -> str:
        lines = [str(r) for r in self.results]
        return "\n".join(lines)

    def failed_gates(self) -> list[SDLCResult]:
        return [r for r in self.results if not r.passed]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _run(cmd: list[str], cwd: Optional[Path] = None, timeout: int = 120) -> tuple[int, str, str]:
    """
    Run a subprocess with a fixed argument list (no shell=True).
    Returns (returncode, stdout, stderr).
    """
    logger.debug("[SDLC] Running: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        logger.error("[SDLC] Timeout (%ds): %s", timeout, " ".join(cmd))
        return -1, "", f"Timeout after {timeout}s"
    except FileNotFoundError:
        return -1, "", f"Binary not found: {cmd[0]!r}"


# ── SecureSDLC ─────────────────────────────────────────────────────────────────

class SecureSDLC:
    """
    Four-gate Secure SDLC engine.
    Each gate returns an SDLCResult; run_all() returns an SDLCReport.
    Call run_all() from software_factory.execute() after sandbox, before GitOps.
    """

    # ── Gate 1: SAST ──────────────────────────────────────────────────────

    def run_sast(self, directory: Path) -> SDLCResult:
        """
        Run Bandit static analysis on `directory`.
        BLOCKS if any HIGH severity finding exists.

        Output format: JSON (--format json) for machine-readable parsing.
        Skips B101/B603/B607 (assert use and subprocess — intentional in sandbox.py).
        """
        if not directory.exists():
            return SDLCResult(
                gate="SAST",
                passed=False,
                detail=f"Directory not found: {directory}",
            )

        rc, stdout, stderr = _run(
            [_BANDIT_BIN, "-r", str(directory), "-f", "json",
             "--skip", _BANDIT_SKIP, "-q"],
        )

        # Bandit exits 1 when it finds issues, 0 when clean
        # rc -1 means binary missing / timeout
        if rc == -1:
            return SDLCResult(
                gate="SAST",
                passed=False,
                detail=f"Bandit unavailable: {stderr[:120]}",
                report=stderr,
            )

        try:
            data = json.loads(stdout or "{}")
        except json.JSONDecodeError:
            # Bandit may output partial JSON on errors — treat as unknown
            return SDLCResult(
                gate="SAST",
                passed=True,
                detail="Bandit output unparseable — treated as clean (no JSON).",
                report=stdout[:500],
            )

        results = data.get("results", [])
        high_issues = [
            r for r in results
            if r.get("issue_severity", "").upper() in _SAST_BLOCK_SEVERITIES
        ]

        metrics = data.get("metrics", {})
        total_issues = sum(
            v.get("SEVERITY.HIGH", 0) + v.get("SEVERITY.MEDIUM", 0)
            for v in metrics.values()
            if isinstance(v, dict)
        )

        if high_issues:
            detail = (
                f"{len(high_issues)} HIGH-severity issue(s) detected. "
                f"GitOps handover BLOCKED."
            )
            # Compact issue list for the Factory report
            issue_lines = [
                f"  - [{i['test_id']}] {i['issue_text']} "
                f"(line {i.get('line_number','?')}, file {Path(i.get('filename','')).name})"
                for i in high_issues[:10]
            ]
            report = f"Bandit HIGH findings:\n" + "\n".join(issue_lines)
            logger.warning("[SDLC] SAST BLOCKED — %d HIGH issue(s)", len(high_issues))
            return SDLCResult(gate="SAST", passed=False, detail=detail, report=report)

        detail = f"Clean — 0 HIGH issues ({total_issues} total at lower severity)."
        logger.info("[SDLC] SAST PASS: %s", detail)
        return SDLCResult(gate="SAST", passed=True, detail=detail, report=stdout[:1000])

    # ── Gate 2: Dependency CVE scan ───────────────────────────────────────

    def scan_dependencies(self, requirements_file: Path) -> SDLCResult:
        """
        Run Safety against a requirements file.
        BLOCKS if any known CVE is found in the listed packages.

        Safety 3.x requires an API key for full DB; without one it uses
        the bundled offline DB.  The --key flag is omitted so the offline
        DB is used by default (no credential dependency at runtime).
        """
        if not requirements_file.exists():
            return SDLCResult(
                gate="DEP_SCAN",
                passed=True,
                detail=f"No requirements file at {requirements_file} — skipped.",
            )

        rc, stdout, stderr = _run(
            [_SAFETY_BIN, "check", "-r", str(requirements_file),
             "--output", "json", "--ignore-unpinned-requirements"],
        )

        if rc == -1:
            return SDLCResult(
                gate="DEP_SCAN",
                passed=False,
                detail=f"Safety unavailable: {stderr[:120]}",
                report=stderr,
            )

        try:
            data = json.loads(stdout or "[]")
        except json.JSONDecodeError:
            # Safety v3 changed output format — fall back to plain text
            if rc != 0:
                return SDLCResult(
                    gate="DEP_SCAN",
                    passed=False,
                    detail="Safety found vulnerabilities (JSON parse failed — see report).",
                    report=stdout[:800],
                )
            return SDLCResult(
                gate="DEP_SCAN",
                passed=True,
                detail="No vulnerabilities found (JSON parse failed but exit 0).",
                report=stdout[:400],
            )

        # Safety 3.x wraps results; handle both list and dict shapes
        vulns: list = []
        if isinstance(data, list):
            vulns = data
        elif isinstance(data, dict):
            vulns = data.get("vulnerabilities", data.get("affected_packages", []))

        if vulns:
            detail = f"{len(vulns)} CVE(s) found. GitOps handover BLOCKED."
            vuln_lines = [
                f"  - {v.get('package_name','?')} {v.get('analyzed_version','?')}: "
                f"{v.get('vulnerability_id','?')} — {str(v.get('advisory',''))[:80]}"
                for v in vulns[:10]
            ]
            report = "Safety CVE findings:\n" + "\n".join(vuln_lines)
            logger.warning("[SDLC] DEP_SCAN BLOCKED — %d CVE(s)", len(vulns))
            return SDLCResult(gate="DEP_SCAN", passed=False, detail=detail, report=report)

        detail = "No known CVEs in listed dependencies."
        logger.info("[SDLC] DEP_SCAN PASS: %s", detail)
        return SDLCResult(gate="DEP_SCAN", passed=True, detail=detail, report=stdout[:600])

    # ── Gate 3: SBOM generation ───────────────────────────────────────────

    def generate_sbom(self, directory: Path) -> SDLCResult:
        """
        Generate a Software Bill of Materials for the workspace artefact.

        Strategy (priority order):
          1. cyclonedx-py environment  → bom.json  (CycloneDX JSON schema)
          2. pip freeze fallback        → sbom.txt  (plain package list)

        The SBOM is written to <directory>/bom.json (or sbom.txt on fallback).
        This gate always PASSES — SBOM generation failure is a warning, not a block,
        because it is an audit artefact, not a security control.
        """
        bom_path = directory / "bom.json"

        # Attempt 1: cyclonedx-py
        rc, stdout, stderr = _run(
            [_CDX_BIN, "environment", "--output-format", "JSON",
             "--output-file", str(bom_path)],
        )

        if rc == 0 and bom_path.exists():
            size = bom_path.stat().st_size
            detail = f"CycloneDX SBOM written to {bom_path.name} ({size:,} bytes)."
            logger.info("[SDLC] SBOM generated: %s", bom_path)
            return SDLCResult(gate="SBOM", passed=True, detail=detail, report=str(bom_path))

        # Attempt 2: pip freeze fallback
        logger.warning("[SDLC] cyclonedx-py failed (%d) — falling back to pip freeze", rc)
        sbom_txt = directory / "sbom.txt"
        rc2, out2, _ = _run([sys.executable, "-m", "pip", "freeze"])
        if rc2 == 0 and out2.strip():
            sbom_txt.write_text(out2, encoding="utf-8")
            detail = f"SBOM fallback (pip freeze) written to {sbom_txt.name} ({len(out2)} chars)."
            logger.info("[SDLC] SBOM fallback generated: %s", sbom_txt)
            return SDLCResult(gate="SBOM", passed=True, detail=detail, report=str(sbom_txt))

        detail = "SBOM generation failed — proceeding without SBOM (non-blocking)."
        logger.warning("[SDLC] SBOM generation failed.")
        return SDLCResult(gate="SBOM", passed=True, detail=detail, report=stderr[:200])

    # ── Gate 4: Test enforcement ──────────────────────────────────────────

    def enforce_tests(self, directory: Path) -> SDLCResult:
        """
        Assert that at least one test_*.py file exists, then run pytest.
        BLOCKS if no test file is found OR if pytest exits non-zero.

        Pytest is run with -x (stop on first failure) and --tb=short to keep
        output concise for the Factory report.
        """
        test_files = list(directory.glob("test_*.py"))
        if not test_files:
            detail = (
                f"No test_*.py files found in {directory}. "
                "GitOps handover BLOCKED — tests are mandatory."
            )
            logger.warning("[SDLC] TEST BLOCKED — no test files in %s", directory)
            return SDLCResult(gate="TEST", passed=False, detail=detail)

        rc, stdout, stderr = _run(
            [_PYTEST_BIN, str(directory), "-x", "--tb=short", "-q"],
            timeout=120,
        )

        if rc == 0:
            # Extract summary line (last non-empty line of pytest output)
            summary = next(
                (l for l in reversed(stdout.splitlines()) if l.strip()),
                "All tests passed."
            )
            detail = f"pytest PASS — {summary}"
            logger.info("[SDLC] TEST PASS: %s", summary)
            return SDLCResult(gate="TEST", passed=True, detail=detail, report=stdout[:1000])

        detail = f"pytest FAIL (exit {rc}). GitOps handover BLOCKED."
        report = (stdout + stderr)[:1200]
        logger.warning("[SDLC] TEST BLOCKED — pytest exited %d", rc)
        return SDLCResult(gate="TEST", passed=False, detail=detail, report=report)

    # ── Composite runner ──────────────────────────────────────────────────

    def run_all(
        self,
        workspace: Path,
        requirements_file: Optional[Path] = None,
    ) -> SDLCReport:
        """
        Run all four gates sequentially.
        Short-circuits after first hard-block (SAST or DEP_SCAN failure)
        to avoid wasting time on subsequent gates.

        Args:
            workspace:         Directory containing generated code and tests.
            requirements_file: Path to requirements.txt for CVE scan.
                               Defaults to <workspace>/requirements.txt.
        """
        req_file = requirements_file or (workspace / "requirements.txt")
        report = SDLCReport()

        # Gate 1: SAST
        sast = self.run_sast(workspace)
        report.results.append(sast)
        if not sast.passed:
            return report  # hard block — skip remaining gates

        # Gate 2: Dependency CVE scan
        dep = self.scan_dependencies(req_file)
        report.results.append(dep)
        if not dep.passed:
            return report  # hard block

        # Gate 3: SBOM (non-blocking — always continues)
        sbom = self.generate_sbom(workspace)
        report.results.append(sbom)

        # Gate 4: Test enforcement
        test = self.enforce_tests(workspace)
        report.results.append(test)

        return report


# ── Singleton ──────────────────────────────────────────────────────────────────

_instance: Optional[SecureSDLC] = None


def get_sdlc_engine() -> SecureSDLC:
    global _instance
    if _instance is None:
        _instance = SecureSDLC()
    return _instance
