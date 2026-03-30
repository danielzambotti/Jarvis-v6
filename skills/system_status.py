"""
skills/system_status.py — Real-Time System Diagnostic Skill
============================================================
Executes immediately using psutil + docker CLI.
No LLM involved — returns verified, real data.
"""
import logging
import subprocess
import time

import psutil
from core.metrics import jarvis_skill_failures_total

logger = logging.getLogger(__name__)


def execute(user_input: str = "") -> str:
    """Return a formatted system status report with real metrics."""
    lines = ["## 🖥️ Jarvis System Status\n"]

    # ── CPU ──────────────────────────────────────────────────────────────
    try:
        cpu_pct = psutil.cpu_percent(interval=0.5)
        cpu_count = psutil.cpu_count(logical=True)
        lines.append(f"**CPU:** {cpu_pct:.1f}% utilization ({cpu_count} logical cores)")
    except Exception as e:
        lines.append(f"**CPU:** unavailable ({e})")

    # ── RAM ──────────────────────────────────────────────────────────────
    try:
        ram = psutil.virtual_memory()
        lines.append(
            f"**RAM:** {ram.percent:.1f}% used — "
            f"{ram.used / 1e9:.1f} GB / {ram.total / 1e9:.1f} GB"
        )
    except Exception as e:
        lines.append(f"**RAM:** unavailable ({e})")

    # ── Disk ─────────────────────────────────────────────────────────────
    try:
        disk = psutil.disk_usage("/")
        lines.append(
            f"**Disk (/):** {disk.percent:.1f}% used — "
            f"{disk.used / 1e9:.1f} GB / {disk.total / 1e9:.1f} GB free: {disk.free / 1e9:.1f} GB"
        )
    except Exception as e:
        lines.append(f"**Disk:** unavailable ({e})")

    # ── Top Processes ────────────────────────────────────────────────────
    try:
        procs = sorted(
            psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]),
            key=lambda p: p.info.get("cpu_percent") or 0,
            reverse=True,
        )[:5]
        if procs:
            lines.append("\n**Top Processes (by CPU):**")
            for p in procs:
                lines.append(
                    f"  - `{p.info['name']}` (PID {p.info['pid']}) — "
                    f"CPU {p.info.get('cpu_percent', 0):.1f}% | "
                    f"RAM {p.info.get('memory_percent', 0):.1f}%"
                )
    except Exception as e:
        logger.warning("[SYSTEM_STATUS] process list error: %s", e)

    # ── Docker containers ─────────────────────────────────────────────────
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            lines.append("\n**Docker Containers:**")
            for row in result.stdout.strip().splitlines():
                parts  = row.split("\t")
                name   = parts[0] if len(parts) > 0 else "?"
                status = parts[1] if len(parts) > 1 else "?"
                image  = parts[2] if len(parts) > 2 else "?"
                icon   = "🟢" if "Up" in status else "🔴"
                lines.append(f"  {icon} `{name}` — {status} ({image})")
        elif result.returncode != 0:
            stderr_lower = (result.stderr or "").lower()
            if any(kw in stderr_lower for kw in ("socket", "daemon", "connect", "unix://")):
                lines.append(
                    "\n**Docker:** Monitoramento Docker indisponível (Socket não montado)."
                )
            else:
                lines.append(f"\n**Docker:** erro — {result.stderr.strip()[:200]}")
            jarvis_skill_failures_total.labels(skill="SYSTEM_STATUS").inc()
        else:
            lines.append("\n**Docker:** no containers running")
    except FileNotFoundError:
        lines.append("\n**Docker:** CLI not found in PATH")
        jarvis_skill_failures_total.labels(skill="SYSTEM_STATUS").inc()
    except subprocess.TimeoutExpired:
        lines.append("\n**Docker:** timeout consultando containers (>5s)")
        jarvis_skill_failures_total.labels(skill="SYSTEM_STATUS").inc()
    except Exception as e:
        lines.append(f"\n**Docker:** error — {e}")
        jarvis_skill_failures_total.labels(skill="SYSTEM_STATUS").inc()

    lines.append(f"\n*Snapshot at {time.strftime('%H:%M:%S')} UTC*")
    return "\n".join(lines)
