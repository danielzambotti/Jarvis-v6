"""
skills/jarvis_health.py — Jarvis Infrastructure Health Diagnostic
=================================================================
Checks Jarvis's own service dependencies (Ollama, Redis, PostgreSQL)
and workspace integrity. Distinct from SYSTEM_STATUS which reports
host OS metrics (CPU/RAM/disk).
"""
import logging
import os
import shutil
import socket

import requests

logger = logging.getLogger(__name__)


def _check_ollama() -> tuple[str, bool]:
    from config import OLLAMA_URL
    url = OLLAMA_URL.replace("/api/generate", "")
    try:
        r = requests.get(url, timeout=5)
        return f"🟢 **Ollama** — reachable at `{url}` (HTTP {r.status_code})", True
    except Exception as e:
        return f"🔴 **Ollama** — unreachable at `{url}`: {e}", False


def _check_redis() -> tuple[str, bool]:
    redis_url = os.environ.get("REDIS_URL", "")
    if not redis_url:
        return "⚪ **Redis** — `REDIS_URL` not configured", True
    try:
        # Parse redis://host:port/db
        parts = redis_url.replace("redis://", "").split("/")[0].split(":")
        host, port = parts[0], int(parts[1]) if len(parts) > 1 else 6379
        with socket.create_connection((host, port), timeout=3):
            pass
        return f"🟢 **Redis** — reachable at `{host}:{port}`", True
    except Exception as e:
        return f"🔴 **Redis** — unreachable ({redis_url}): {e}", False


def _check_postgres() -> tuple[str, bool]:
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return "⚪ **PostgreSQL** — `DATABASE_URL` not configured", True
    try:
        # Parse postgresql://user:pass@host:port/db
        netloc = db_url.split("@")[-1].split("/")[0]
        parts = netloc.split(":")
        host, port = parts[0], int(parts[1]) if len(parts) > 1 else 5432
        with socket.create_connection((host, port), timeout=3):
            pass
        return f"🟢 **PostgreSQL** — reachable at `{host}:{port}`", True
    except Exception as e:
        return f"🔴 **PostgreSQL** — unreachable ({db_url.split('@')[-1]}): {e}", False


def _check_workspace() -> tuple[str, bool]:
    from security import is_safe_path
    workspace = os.environ.get("WORKSPACE_PATH", "/app/workspace")
    test = os.path.join(workspace, ".sre_test")
    try:
        ok = is_safe_path(test)
        if ok:
            return f"🟢 **Workspace** — path resolution OK (`{workspace}`)", True
        return f"🔴 **Workspace** — `is_safe_path` returned False for `{workspace}`", False
    except Exception as e:
        return f"🔴 **Workspace** — path check threw: {e}", False


def _check_tools() -> list[str]:
    lines = []
    for tool, skills in [("git", "REFACTOR/JAVA_GITOPS"), ("docker", "FACTORY/SYSTEM_STATUS")]:
        if shutil.which(tool):
            lines.append(f"🟢 **{tool}** — found in PATH ({skills} operational)")
        else:
            lines.append(f"🔴 **{tool}** — not in PATH ({skills} will fail)")
    return lines


def execute(user_input: str = "") -> str:
    """Return a full Jarvis infrastructure health report."""
    lines = ["## 🏥 Jarvis Infrastructure Health\n"]

    checks = [_check_ollama, _check_redis, _check_postgres, _check_workspace]
    all_ok = True
    for check in checks:
        msg, ok = check()
        lines.append(msg)
        if not ok:
            all_ok = False

    lines.append("")
    lines.extend(_check_tools())

    lines.append("")
    if all_ok:
        lines.append("**Overall: ✅ All systems operational**")
    else:
        lines.append("**Overall: ⚠️ One or more systems degraded — check above**")

    return "\n".join(lines)
