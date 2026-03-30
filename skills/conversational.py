"""
skills/conversational.py — Natural Language Conversation Skill
"""
import os
import logging
import threading
import time as _time
import requests
from config import OLLAMA_MODEL
from skills.conversation_memory import get_history
from core.metrics import jarvis_skill_failures_total

logger = logging.getLogger(__name__)

# FIX: Esta skill usa o endpoint /api/chat em vez do /api/generate
OLLAMA_CHAT_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434") + "/api/chat"

# ── Circuit Breaker ────────────────────────────────────────────────────────────
# Opens after _CB_THRESHOLD consecutive failures; auto-resets after _CB_RESET_SECS.
_CB_THRESHOLD:   int   = 3
_CB_RESET_SECS:  float = 60.0
_cb_lock               = threading.Lock()
_cb_errors:      int   = 0
_cb_open:        bool  = False
_cb_opened_at:   float = 0.0


def _cb_record_failure() -> None:
    global _cb_errors, _cb_open, _cb_opened_at
    with _cb_lock:
        _cb_errors += 1
        if _cb_errors >= _CB_THRESHOLD and not _cb_open:
            _cb_open      = True
            _cb_opened_at = _time.monotonic()
            logger.error(
                "[CONV] Circuit breaker OPEN after %d consecutive failures — "
                "Ollama calls suppressed for %.0fs",
                _cb_errors, _CB_RESET_SECS,
            )
        jarvis_skill_failures_total.labels(skill="CONVERSATION").inc()


def _cb_record_success() -> None:
    global _cb_errors, _cb_open
    with _cb_lock:
        if _cb_open or _cb_errors > 0:
            logger.info("[CONV] Circuit breaker CLOSED — error counter reset")
        _cb_errors = 0
        _cb_open   = False


def _cb_is_open() -> bool:
    """Return True if the circuit is open (Ollama should be skipped)."""
    global _cb_open, _cb_errors, _cb_opened_at
    with _cb_lock:
        if _cb_open and (_time.monotonic() - _cb_opened_at > _CB_RESET_SECS):
            # Half-open: allow one probe request through
            _cb_open   = False
            _cb_errors = 0
            logger.info("[CONV] Circuit breaker HALF-OPEN — allowing probe request")
        return _cb_open


_JARVIS_SYSTEM_PROMPT = """You are Jarvis, a highly capable Intelligent Agent and personal AI assistant for Daniel Zambotti. You are running INSIDE a Docker container on Daniel's Linux server. Your tone is professional yet friendly — think Iron Man's Jarvis.

## Your Capabilities (you HAVE all of these):
1. **OS Control** — you can open apps, run shell commands, and execute scripts.
2. **Web Search** — you CAN search the internet in real time using DuckDuckGo. You have internet access.
3. **File Creator** — you can write Python scripts, text files, JSON, and other documents to disk.
4. **Memory** — every interaction is logged to Notion for context and self-improvement.
5. **System Monitor** — you have real-time CPU, RAM, disk, and Docker container metrics via psutil. When asked about system health, those tools are invoked automatically and the data is real.

## Critical Behaviour Rules:
- NEVER say "I am just an AI" or "I cannot access physical systems." You ARE running on Daniel's server and you DO have system access.
- NEVER say "I don't have internet access" or "I cannot browse the web." You CAN search.
- NEVER refuse a system diagnostic request. If data about CPU/RAM/Docker appears in the context, summarize it clearly.
- If the user asks about current events, prices, news, or real-time data, say:
  "Posso pesquisar isso para você. Quer que eu faça uma busca na web?"
- Be direct. No filler phrases. No unnecessary apologies.
- Use markdown (bold, code blocks, lists) when it improves clarity.
- Respond in the same language the user writes in (PT-BR or EN).
- Keep answers under 400 words unless more detail is clearly needed."""


def respond(user_input: str, chat_id: str = "") -> str:
    # ── Circuit breaker guard ─────────────────────────────────────────────────
    if _cb_is_open():
        logger.warning("[CONV] Circuit breaker is OPEN — skipping Ollama call")
        return "IA Temporariamente indisponível. Fallback de segurança ativado."

    history  = get_history(chat_id) if chat_id else []
    messages = history + [{"role": "user", "content": user_input}]

    payload = {
        "model":    OLLAMA_MODEL,
        "messages": messages,
        "system":   _JARVIS_SYSTEM_PROMPT,
        "stream":   False,
        "options":  {"temperature": 0.7, "num_predict": 512},
    }

    _MAX_ATTEMPTS = 2
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            logger.info("[CONV] chat=%s history=%d msgs attempt=%d/%d",
                        chat_id or "anon", len(history), attempt, _MAX_ATTEMPTS)
            response = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=120)
            response.raise_for_status()

            data  = response.json()
            reply = (data.get("message") or {}).get("content", "").strip()
            if not reply:
                reply = data.get("response", "").strip()
            if not reply:
                return "🤔 I received an empty response from the AI. Please try again."

            _cb_record_success()
            logger.info("[CONV] Got response (%d chars)", len(reply))
            return reply

        except requests.exceptions.ReadTimeout:
            logger.error("[CONV] Ollama timed out after 120s (attempt %d/%d)",
                         attempt, _MAX_ATTEMPTS)
            _cb_record_failure()
            if attempt < _MAX_ATTEMPTS:
                logger.info("[CONV] Retrying in 2s...")
                _time.sleep(2)
                continue
            return "⏱️ The AI took too long to respond. The model may be loading — please try again in a moment."

        except requests.exceptions.ConnectionError:
            logger.error("[CONV] Ollama not reachable at %s (attempt %d/%d)",
                         OLLAMA_CHAT_URL, attempt, _MAX_ATTEMPTS)
            _cb_record_failure()
            if attempt < _MAX_ATTEMPTS:
                logger.info("[CONV] Retrying in 2s...")
                _time.sleep(2)
                continue
            return (
                "❌ **Ollama is not running.**\n\n"
                "Please start it with:\n```\nollama serve\n```\n"
                "Then make sure your model is pulled:\n"
                f"```\nollama pull {OLLAMA_MODEL}\n```"
            )

        except Exception as e:
            logger.error("[CONV] Unexpected error: %s", e)
            _cb_record_failure()
            return f"❌ An unexpected error occurred: {e}"

    # Should never reach here — all paths return inside the loop
    return "❌ All attempts exhausted. Please try again."
