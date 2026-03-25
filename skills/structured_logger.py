"""
skills/structured_logger.py — Interaction Metadata Logger
==========================================================
Logs every Jarvis interaction as a JSONL record containing:
  - timestamp   : ISO-8601 UTC
  - skill       : which skill handled the request
  - input_hash  : SHA-256 truncated to 16 hex chars (privacy-safe)
  - input_len   : character length of user input
  - output_len  : character length of response
  - latency_ms  : elapsed milliseconds from request to response

This module lives in skills/ so main.py can import it as:
    from skills.structured_logger import StructuredLogger
"""

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path


# Log directory: C:\\Jarvis\\logs\\  (created automatically on first use)
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


class StructuredLogger:
    """Zero-dependency, thread-safe, append-only interaction logger."""

    def __init__(self):
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._log_file = _LOG_DIR / "interactions.jsonl"

    def start(self) -> float:
        """Record start time. Call before executing a skill."""
        return time.monotonic()

    def log(self, start_token: float, skill: str,
            user_input: str, result: str) -> None:
        """
        Append one JSON record to logs/interactions.jsonl.
        Safe to call from a background thread — never raises.
        """
        latency_ms = round((time.monotonic() - start_token) * 1000)
        # Hash first 50 chars only — enough for dedup, not enough to reconstruct
        digest = hashlib.sha256(
            user_input[:50].encode("utf-8", errors="replace")
        ).hexdigest()[:16]

        record = {
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "skill":      skill,
            "input_hash": digest,
            "input_len":  len(user_input),
            "output_len": len(result),
            "latency_ms": latency_ms,
        }
        try:
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            # Never crash the bot over a logging failure
            print(f"[STRUCTURED_LOGGER] Write error: {e}")

    def tail(self, n: int = 20) -> list:
        """Return the last n log records as a list of dicts."""
        if not self._log_file.exists():
            return []
        try:
            lines = self._log_file.read_text(encoding="utf-8").splitlines()
            return [json.loads(l) for l in lines[-n:] if l.strip()]
        except Exception:
            return []
