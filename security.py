"""
security.py — Jarvis Security Gate (v3 — Phase 1.1)
=====================================================
UPGRADE: Token Bucket Rate Limiting + hardened Path Traversal guard.

LAYERS:
  1. is_authorized()           — Hard whitelist by chat_id
  2. sanitize_input()          — Strip control chars + length limit
  3. detect_prompt_injection() — 18 adversarial injection patterns
  4. is_safe_command()         — Destructive OS command blacklist
  5. check_rate_limit()        — Token Bucket per chat_id (NEW)
  6. is_safe_path()            — Absolute path traversal guard (NEW)

TOKEN BUCKET ALGORITHM:
  Each chat_id gets a bucket of MAX_TOKENS tokens.
  Every request consumes 1 token.
  Tokens refill at REFILL_RATE per second (continuous).
  If bucket is empty → request is rate-limited.
  This smoothly handles bursts while preventing sustained abuse.
"""

import os
import re
import time
import logging
import threading
from pathlib import Path
from config import ALLOWED_CHAT_ID

logger = logging.getLogger(__name__)

_MAX_INPUT_LENGTH = 1000

# ── Layer 5: Token Bucket Rate Limiter ───────────────────────────────────────
_MAX_TOKENS:   float = 10.0   # max burst: 10 requests
_REFILL_RATE:  float = 0.5    # refill 1 token every 2 seconds
_buckets:      dict  = {}     # {chat_id: {"tokens": float, "last_refill": float}}
_bucket_lock           = threading.Lock()


def check_rate_limit(chat_id: int) -> bool:
    """
    Layer 5: Token Bucket rate limiting per chat_id.

    Returns True if request is ALLOWED, False if rate-limited.
    Thread-safe via a shared lock.

    Algorithm:
      1. Calculate elapsed time since last request
      2. Refill tokens: min(max, current + elapsed * rate)
      3. If tokens >= 1: consume 1 and allow
      4. Else: deny
    """
    key = str(chat_id)
    now = time.monotonic()

    with _bucket_lock:
        if key not in _buckets:
            _buckets[key] = {"tokens": _MAX_TOKENS - 1.0, "last_refill": now}
            return True  # first request always allowed

        bucket = _buckets[key]
        elapsed = now - bucket["last_refill"]
        bucket["tokens"] = min(_MAX_TOKENS, bucket["tokens"] + elapsed * _REFILL_RATE)
        bucket["last_refill"] = now

        if bucket["tokens"] >= 1.0:
            bucket["tokens"] -= 1.0
            return True
        else:
            logger.warning("[SECURITY] Rate limit hit for chat_id=%d (%.2f tokens)",
                           chat_id, bucket["tokens"])
            return False


# ── Layer 6: Path Traversal Guard ────────────────────────────────────────────
_WORKSPACE = Path(os.environ.get("WORKSPACE_PATH", "/app/workspace")).resolve()

def is_safe_path(user_path: str) -> bool:
    """
    Layer 6: Block path traversal outside C:\\Jarvis\\workspace.

    Uses os.path.abspath to resolve ALL relative refs (../, symlinks)
    before comparing. A path is safe only if it resolves to a location
    strictly inside the workspace directory.

    Args:
        user_path: Raw path string from user or AI-generated content.
    Returns:
        True if the resolved path is inside workspace, False otherwise.
    """
    try:
        resolved = Path(os.path.abspath(user_path)).resolve()
        # resolved.relative_to() raises ValueError if not a subpath
        resolved.relative_to(_WORKSPACE)
        return True
    except ValueError:
        logger.error("[SECURITY] Path traversal blocked: %s → %s",
                     user_path, os.path.abspath(user_path))
        return False
    except Exception as e:
        logger.error("[SECURITY] Path check error: %s", e)
        return False


# ── Layer 4: Destructive OS command blacklist ──────────────────────────────────
_DANGEROUS_PATTERNS: list[str] = [
    r"format\s+[a-zA-Z]:",
    r"rmdir\s+/s",
    r"rd\s+/s",
    r"del\s+/s",
    r"del\s+/f\s+/s",
    r"remove-item\s+-recurse\s+-force",
    r"rm\s+-rf",
    r"shutdown\s+/[rfs]",
    r"reg\s+delete",
    r"bcdedit",
    r"diskpart",
    r"cipher\s+/w",
    r"sfc\s+/scannow",
    r"net\s+user\s+.+\s+/delete",
]
_COMPILED_DANGEROUS = [re.compile(p, re.IGNORECASE) for p in _DANGEROUS_PATTERNS]

# ── Layer 3: Prompt injection patterns ───────────────────────────────────────
_INJECTION_PATTERNS: list[str] = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions",
    r"disregard\s+(all\s+)?previous",
    r"forget\s+(all\s+)?previous\s+instructions",
    r"act\s+as\s+(dan|jailbreak|evil|unrestricted|devel?oper\s+mode)",
    r"you\s+are\s+now\s+(dan|jailbreak|a\s+different)",
    r"jailbreak\s+mode",
    r"developer\s+mode\s+(enabled|activated|on)",
    r"pretend\s+you\s+(have\s+no\s+limits|are\s+not\s+an\s+ai)",
    r"system\s+prompt[:\s]+reveal",
    r"print\s+(your\s+)?(system|initial)\s+prompt",
    r"reveal\s+(your\s+)?(instructions|system\s+prompt|config)",
    r"what\s+(are\s+)?your\s+(hidden\s+)?instructions",
    r"override\s+(safety|security|ethics|guidelines)",
    r"\[system\]",
    r"<\|im_start\|>",
    r"<\|system\|>",
    r"###\s*instruction",
]
_COMPILED_INJECTION = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]


def is_authorized(chat_id: int) -> bool:
    """Layer 1: Hard whitelist — only configured owner can interact."""
    authorized = (chat_id == ALLOWED_CHAT_ID)
    if not authorized:
        logger.warning("[SECURITY] Unauthorized: chat_id=%d", chat_id)
    return authorized


def sanitize_input(text: str) -> str:
    """Layer 2: Strip control chars and enforce length limit."""
    if not text:
        return ""
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    if len(cleaned) > _MAX_INPUT_LENGTH:
        logger.warning("[SECURITY] Truncated %d->%d", len(cleaned), _MAX_INPUT_LENGTH)
        cleaned = cleaned[:_MAX_INPUT_LENGTH] + "... [truncated]"
    return cleaned.strip()


def detect_prompt_injection(text: str) -> tuple[bool, str]:
    """Layer 3: Detect jailbreak / injection patterns. Returns (is_safe, reason)."""
    for pattern in _COMPILED_INJECTION:
        if pattern.search(text):
            reason = f"Injection pattern: '{pattern.pattern[:60]}'"
            logger.error("[SECURITY] INJECTION BLOCKED: %s", reason)
            return False, reason
    return True, "OK"


def is_safe_command(command: str) -> bool:
    """Layer 4: Blacklist catastrophically destructive OS commands."""
    for pattern in _COMPILED_DANGEROUS:
        if pattern.search(command):
            logger.error("[SECURITY] BLOCKED: '%s' in: %s",
                         pattern.pattern, command[:200])
            return False
    return True
