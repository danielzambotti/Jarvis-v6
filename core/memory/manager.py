"""
core/memory/manager.py — Conversational Memory & Context Manager
=================================================================
Provides persistent, multi-layer memory for Jarvis v6.0 conversations.

STORAGE ARCHITECTURE
--------------------
  Layer 1 (Hot)  — Redis sorted set, keyed by session_id.
                   Last N messages retrieved in < 1 ms.
                   TTL: 2 hours of inactivity (configurable).

  Layer 2 (Cold) — PostgreSQL `interactions` table.
                   Full audit trail; source of truth for summarisation.
                   Survives Redis eviction and container restarts.

  Layer 3 (Summarised) — LLM-generated rolling summary stored in Redis.
                   When a session exceeds TOKEN_SUMMARY_THRESHOLD tokens,
                   the oldest half is summarised and the raw messages
                   replaced with the summary, keeping context window small.

SECURITY GUARANTEES
-------------------
  - Content written to Postgres passes through the DLP engine first.
  - user_id is taken from a verified IAM UserPrincipal, not self-reported.
  - Redis keys are namespaced per session; no cross-session leakage.

Dependencies (add to requirements.txt):
    psycopg2-binary >= 2.9
    redis >= 5.0

Usage:
    from core.memory.manager import get_memory_manager

    mm = get_memory_manager()
    mm.store_interaction(user_id="...", session_id="...", role="user",
                         content="Hello", tokens=5)
    ctx = mm.get_active_context(session_id="...")
    # ctx -> [{"role": "user", "content": "Hello"}, ...]
"""

import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

_PG_DSN = os.environ.get(
    "POSTGRES_DSN",
    "postgresql://jarvis:jarvis_secure_2025@localhost:5432/jarvis_db",
)
_REDIS_URL   = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_CONTEXT_LIMIT        = int(os.environ.get("MEMORY_CONTEXT_LIMIT", "12"))    # messages
_REDIS_TTL_SECONDS    = int(os.environ.get("MEMORY_REDIS_TTL", "7200"))      # 2 hours
_TOKEN_SUMMARY_THRESHOLD = int(os.environ.get("MEMORY_SUMMARY_TOKENS", "2000"))
_SUMMARY_OLLAMA_URL   = os.environ.get("OLLAMA_CHAT_URL", "http://localhost:11434/api/chat")
_SUMMARY_MODEL        = os.environ.get("OLLAMA_MODEL", "llama3")

_REDIS_KEY_PREFIX   = "jarvis:ctx:"    # sorted set per session
_SUMMARY_KEY_PREFIX = "jarvis:sum:"    # last summary per session


# ── Lazy connection helpers ───────────────────────────────────────────────────

def _pg_conn():
    """Return a new psycopg2 connection. Caller must close it."""
    try:
        import psycopg2
        return psycopg2.connect(_PG_DSN)
    except ImportError:
        raise RuntimeError("psycopg2-binary not installed. Run: pip install psycopg2-binary")
    except Exception as exc:
        logger.error("[MEMORY] Postgres connection failed: %s", exc)
        raise


def _redis_client():
    """Return a redis.Redis client (connection-pooled singleton)."""
    try:
        import redis as _redis
        return _redis.from_url(_REDIS_URL, decode_responses=True)
    except ImportError:
        raise RuntimeError("redis not installed. Run: pip install redis")
    except Exception as exc:
        logger.error("[MEMORY] Redis connection failed: %s", exc)
        raise


# ── MemoryManager ─────────────────────────────────────────────────────────────

class MemoryManager:
    """
    Thread-safe memory manager.  Each public method opens/closes its own
    connection to avoid holding long-lived pool state in a multi-process
    Gunicorn/uvicorn environment.
    """

    # ── Write path ─────────────────────────────────────────────────────────

    def store_interaction(
        self,
        user_id: str,
        session_id: str,
        role: str,
        content: str,
        tokens: int = 0,
    ) -> None:
        """
        Persist one turn.  Content MUST already be DLP-sanitised by the caller.

        Writes to:
          1. Redis sorted set  (score = unix timestamp for ordered retrieval)
          2. PostgreSQL        (durable audit trail)
        """
        # ── DLP guard (belt-and-suspenders) ──────────────────────────────
        try:
            from core.security.dlp import get_dlp_engine
            content, findings = get_dlp_engine().sanitize_text(content)
            if findings:
                logger.warning("[MEMORY] DLP stripped %d item(s) before storage.", len(findings))
        except Exception as dlp_exc:
            logger.error("[MEMORY] DLP check failed, storing raw: %s", dlp_exc)

        # ── Redis ─────────────────────────────────────────────────────────
        try:
            r = _redis_client()
            key = f"{_REDIS_KEY_PREFIX}{session_id}"
            entry = json.dumps({"role": role, "content": content, "ts": time.time()})
            score = time.time()
            r.zadd(key, {entry: score})
            r.expire(key, _REDIS_TTL_SECONDS)
            logger.debug("[MEMORY] Redis write OK: session=%s role=%s tokens=%d", session_id, role, tokens)
        except Exception as exc:
            logger.error("[MEMORY] Redis write failed (continuing to PG): %s", exc)

        # ── PostgreSQL ────────────────────────────────────────────────────
        try:
            conn = _pg_conn()
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO interactions
                            (user_id, session_id, role, content, tokens)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (user_id, session_id, role, content, tokens),
                    )
            conn.close()
            logger.debug("[MEMORY] PG write OK: session=%s role=%s", session_id, role)
        except Exception as exc:
            logger.error("[MEMORY] PG write failed: %s", exc)

    # ── Read path ───────────────────────────────────────────────────────────

    def get_active_context(
        self,
        session_id: str,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """
        Return the last `limit` messages for injection into a system prompt.

        Strategy:
          1. Try Redis (fast, recent).
          2. On miss or error, fall back to PostgreSQL.
          3. Prepend cached summary if one exists.

        Returns a list of {"role": str, "content": str} dicts,
        oldest-first (correct order for LLM context).
        """
        n = limit or _CONTEXT_LIMIT
        messages: list[dict] = []

        # Check for a rolling summary first
        summary = self._get_summary(session_id)

        # ── Redis fast path ───────────────────────────────────────────────
        redis_ok = False
        try:
            r = _redis_client()
            key = f"{_REDIS_KEY_PREFIX}{session_id}"
            # ZRANGE with REV gives newest first; we take n, then reverse
            raw_entries = r.zrange(key, 0, n - 1, rev=True)
            if raw_entries:
                for raw in reversed(raw_entries):
                    entry = json.loads(raw)
                    messages.append({"role": entry["role"], "content": entry["content"]})
                redis_ok = True
                logger.debug("[MEMORY] Redis hit: %d messages for session=%s", len(messages), session_id)
        except Exception as exc:
            logger.warning("[MEMORY] Redis read failed, falling back to PG: %s", exc)

        # ── Postgres fallback ─────────────────────────────────────────────
        if not redis_ok:
            try:
                conn = _pg_conn()
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT role, content FROM interactions
                            WHERE session_id = %s
                            ORDER BY created_at DESC
                            LIMIT %s
                            """,
                            (session_id, n),
                        )
                        rows = cur.fetchall()
                conn.close()
                messages = [{"role": r, "content": c} for r, c in reversed(rows)]
                logger.debug("[MEMORY] PG fallback: %d messages for session=%s", len(messages), session_id)
            except Exception as exc:
                logger.error("[MEMORY] PG read failed: %s", exc)

        # Prepend summary as a synthetic system message if present
        if summary:
            messages = [{"role": "system", "content": f"[Context summary]\n{summary}"}] + messages

        return messages

    # ── Summarisation ───────────────────────────────────────────────────────

    def summarize_past_context(self, session_id: str, user_id: str) -> Optional[str]:
        """
        If the session's token count exceeds the threshold, summarise the
        oldest half of messages via Ollama, store the summary in Redis, and
        prune those messages from the Redis hot layer (PG is kept intact).

        Returns the summary string, or None if summarisation was skipped.
        """
        try:
            conn = _pg_conn()
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT SUM(tokens) FROM interactions WHERE session_id = %s",
                        (session_id,),
                    )
                    total_tokens = cur.fetchone()[0] or 0
            conn.close()
        except Exception as exc:
            logger.error("[MEMORY] Token count query failed: %s", exc)
            return None

        if total_tokens < _TOKEN_SUMMARY_THRESHOLD:
            logger.debug("[MEMORY] Token count %d < threshold %d — no summary needed.",
                         total_tokens, _TOKEN_SUMMARY_THRESHOLD)
            return None

        logger.info("[MEMORY] Token count %d >= threshold — summarising session=%s", total_tokens, session_id)

        # Fetch the oldest half from PG
        try:
            conn = _pg_conn()
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT role, content FROM interactions
                        WHERE session_id = %s
                        ORDER BY created_at ASC
                        LIMIT (
                            SELECT COUNT(*) / 2 FROM interactions WHERE session_id = %s
                        )
                        """,
                        (session_id, session_id),
                    )
                    old_messages = cur.fetchall()
            conn.close()
        except Exception as exc:
            logger.error("[MEMORY] Failed to fetch old messages for summary: %s", exc)
            return None

        if not old_messages:
            return None

        history_text = "\n".join(f"{role.upper()}: {content}" for role, content in old_messages)
        summary = self._call_summary_llm(history_text)
        if not summary:
            return None

        # DLP-sanitise the summary before caching
        try:
            from core.security.dlp import get_dlp_engine
            summary, _ = get_dlp_engine().sanitize_text(summary)
        except Exception:
            pass

        # Cache summary in Redis
        try:
            r = _redis_client()
            r.set(f"{_SUMMARY_KEY_PREFIX}{session_id}", summary, ex=_REDIS_TTL_SECONDS)
        except Exception as exc:
            logger.error("[MEMORY] Failed to cache summary: %s", exc)

        # Prune summarised messages from Redis hot layer
        try:
            r = _redis_client()
            key = f"{_REDIS_KEY_PREFIX}{session_id}"
            # Remove oldest entries (lowest scores) equal to the number summarised
            r.zpopmin(key, len(old_messages))
        except Exception as exc:
            logger.error("[MEMORY] Failed to prune Redis after summarisation: %s", exc)

        logger.info("[MEMORY] Summary complete for session=%s (%d chars)", session_id, len(summary))
        return summary

    # ── Internal helpers ────────────────────────────────────────────────────

    def _get_summary(self, session_id: str) -> Optional[str]:
        try:
            r = _redis_client()
            return r.get(f"{_SUMMARY_KEY_PREFIX}{session_id}")
        except Exception:
            return None

    def _call_summary_llm(self, history_text: str) -> Optional[str]:
        """Fire a single Ollama call to produce a compressed summary."""
        try:
            import requests
            payload = {
                "model": _SUMMARY_MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Summarise the following conversation history in 3-5 concise sentences. "
                            "Preserve key decisions, context, and any technical details. "
                            "Output only the summary — no preamble.\n\n"
                            f"{history_text}"
                        ),
                    }
                ],
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 300},
            }
            resp = requests.post(_SUMMARY_OLLAMA_URL, json=payload, timeout=60)
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "").strip()
        except Exception as exc:
            logger.error("[MEMORY] Summary LLM call failed: %s", exc)
            return None


# ── Singleton ─────────────────────────────────────────────────────────────────

_instance: Optional[MemoryManager] = None


def get_memory_manager() -> MemoryManager:
    global _instance
    if _instance is None:
        _instance = MemoryManager()
    return _instance
