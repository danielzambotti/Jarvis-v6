"""
skills/perceptor.py — Continuous Background Perception Engine (Phase 2.2/2.3)
==============================================================================
Runs background threads monitoring the system and pushing anomaly alerts
via WebSocket to the dashboard AND Telegram.

MONITORS:
  - CPU spikes (>85% sustained for 10s)
  - RAM pressure (>90%)
  - Docker container health (CLI-based, no SDK required)
  - Critical log file errors (C:\\Jarvis\\logs\\)

ALERTING:
  - WebSocket broadcast to dashboard (web_ui.app.broadcast)
  - Telegram message to owner (via stored bot + chat_id)

DESIGN:
  - Single background thread per monitor (lightweight)
  - asyncio.run_coroutine_threadsafe() bridges sync threads to async event loop
  - Hard thresholds prevent alert storms (cooldown per alert type)
"""

import asyncio
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

import psutil

logger = logging.getLogger(__name__)

# ── Alert cooldown (seconds between same alert type) ─────────────────────────
_COOLDOWNS:   dict  = {}
_COOLDOWN_S          = 120   # 2 min between same alert
_loop:  asyncio.AbstractEventLoop | None = None
_bot_ref              = None  # set by start()
_chat_id_ref: str    = ""


def _can_alert(key: str) -> bool:
    """Return True if cooldown has elapsed for this alert type."""
    now = time.time()
    if now - _COOLDOWNS.get(key, 0) > _COOLDOWN_S:
        _COOLDOWNS[key] = now
        return True
    return False


def _fire_alert(alert_type: str, message: str) -> None:
    """
    Push alert to WebSocket dashboard AND Telegram.
    Called from sync threads — bridges to async event loop safely.
    """
    logger.warning("[PERCEPTOR] ALERT %s: %s", alert_type, message)

    if _loop is None:
        return

    async def _push():
        try:
            from web_ui.app import broadcast
            await broadcast("alert", {"alert_type": alert_type, "message": message})
        except Exception as e:
            logger.error("[PERCEPTOR] WebSocket broadcast failed: %s", e)

        if _bot_ref and _chat_id_ref:
            try:
                await _bot_ref.send_message(
                    chat_id=_chat_id_ref,
                    text=f"ALERTA JARVIS [{alert_type}]\n{message}"
                )
            except Exception as e:
                logger.error("[PERCEPTOR] Telegram alert failed: %s", e)

    asyncio.run_coroutine_threadsafe(_push(), _loop)


# ── Monitor 1: CPU + RAM ──────────────────────────────────────────────────────
def _monitor_cpu_ram() -> None:
    cpu_high_since = 0.0
    while True:
        try:
            cpu = psutil.cpu_percent(interval=2)
            ram = psutil.virtual_memory()

            if cpu > 85:
                if cpu_high_since == 0:
                    cpu_high_since = time.time()
                elif time.time() - cpu_high_since > 10:
                    if _can_alert("cpu_high"):
                        _fire_alert("CPU_HIGH",
                                    f"CPU em {cpu:.0f}% por mais de 10s")
                    cpu_high_since = 0
            else:
                cpu_high_since = 0

            if ram.percent > 90 and _can_alert("ram_high"):
                used_gb = ram.used / 1e9
                _fire_alert("RAM_HIGH",
                            f"RAM em {ram.percent:.0f}% ({used_gb:.1f}GB usados)")
        except Exception as e:
            logger.error("[PERCEPTOR] CPU/RAM monitor error: %s", e)
        time.sleep(5)


# ── Monitor 2: Docker container health ───────────────────────────────────────
def _monitor_docker() -> None:
    while True:
        try:
            result = subprocess.run(
                ["docker", "ps", "-a", "--format",
                 "{{.Names}}\t{{.Status}}"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    if "\t" not in line:
                        continue
                    name, status = line.split("\t", 1)
                    if "Exited" in status or "unhealthy" in status.lower():
                        key = f"docker_{name}"
                        if _can_alert(key):
                            _fire_alert("DOCKER_DOWN",
                                        f"Container '{name}' status: {status}")
        except FileNotFoundError:
            pass  # Docker not installed
        except Exception as e:
            logger.error("[PERCEPTOR] Docker monitor error: %s", e)
        time.sleep(30)


# ── Monitor 3: Critical log file errors ──────────────────────────────────────
_LOG_DIR = Path(__file__).parent.parent / "logs"
_ERROR_PATTERNS = ["ERROR", "CRITICAL", "FATAL", "Traceback"]
_log_positions: dict = {}  # {path: last_read_pos}

def _monitor_logs() -> None:
    while True:
        try:
            if _LOG_DIR.exists():
                for log_file in _LOG_DIR.glob("*.log"):
                    _check_log_file(log_file)
        except Exception as e:
            logger.error("[PERCEPTOR] Log monitor error: %s", e)
        time.sleep(15)


def _check_log_file(path: Path) -> None:
    try:
        size = path.stat().st_size
        last_pos = _log_positions.get(str(path), size)  # start from end on first run
        if size <= last_pos:
            _log_positions[str(path)] = size
            return
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(last_pos)
            new_content = f.read()
        _log_positions[str(path)] = size
        for line in new_content.splitlines():
            for pattern in _ERROR_PATTERNS:
                if pattern in line:
                    key = f"log_{path.name}_{pattern}"
                    if _can_alert(key):
                        _fire_alert("LOG_ERROR",
                                    f"{path.name}: {line[:200]}")
                    break
    except Exception:
        pass


def start(event_loop: asyncio.AbstractEventLoop,
          bot=None, chat_id: str = "") -> None:
    """
    Start all background monitor threads.
    Must be called AFTER the asyncio event loop is running.

    Args:
        event_loop: The running asyncio loop (from asyncio.get_event_loop())
        bot:        telegram.Bot instance for alert delivery
        chat_id:    Owner's chat_id as string
    """
    global _loop, _bot_ref, _chat_id_ref
    _loop        = event_loop
    _bot_ref     = bot
    _chat_id_ref = chat_id

    monitors = [
        ("cpu_ram",    _monitor_cpu_ram),
        ("docker",     _monitor_docker),
        ("logs",       _monitor_logs),
    ]
    for name, fn in monitors:
        t = threading.Thread(target=fn, name=f"perceptor_{name}",
                             daemon=True)
        t.start()
        logger.info("[PERCEPTOR] Started monitor: %s", name)
