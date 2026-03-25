"""
skills/conversation_memory.py — Persistent Conversation Context
================================================================
Stores the last N messages per chat_id so Jarvis always has context.

Every message (user + assistant) is saved to:
  C:\\Jarvis\\memory\\{chat_id}.jsonl

On each request, the last MAX_HISTORY messages are loaded and injected
into the Ollama prompt as conversation history. This gives Jarvis:
  - Memory across restarts (file-backed)
  - True context window (knows what was said before)
  - Ability to reference previous commands and results

USED BY:
  - conversational.py   (injected into every LLM call)
  - os_controller.py    (context for smarter command generation)
  - main.py             (save every message + response)
"""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"
_MEMORY_DIR.mkdir(parents=True, exist_ok=True)

MAX_HISTORY   = 20   # messages to keep in context window
MAX_STORED    = 200  # messages to keep on disk per chat


def _path(chat_id: str) -> Path:
    return _MEMORY_DIR / f"{chat_id}.jsonl"


def save_message(chat_id: str, role: str, content: str) -> None:
    """Append one message to the chat history file."""
    entry = {
        "role":      role,       # "user" or "assistant"
        "content":   content,
        "timestamp": time.time(),
    }
    try:
        with open(_path(chat_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.error("[MEMORY] Write error for chat %s: %s", chat_id, e)


def get_history(chat_id: str, n: int = MAX_HISTORY) -> list[dict]:
    """
    Return the last n messages as a list of {role, content} dicts.
    Suitable for direct injection into Ollama's messages format.
    """
    p = _path(chat_id)
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
        entries = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        # Return last n, only role+content (drop timestamp)
        return [{"role": e["role"], "content": e["content"]}
                for e in entries[-n:]]
    except OSError as e:
        logger.error("[MEMORY] Read error for chat %s: %s", chat_id, e)
        return []


def get_context_string(chat_id: str, n: int = 10) -> str:
    """
    Return the last n exchanges as a formatted string for injection
    into system prompts that don't support structured history.
    """
    history = get_history(chat_id, n * 2)  # n exchanges = 2n messages
    if not history:
        return ""
    lines = []
    for msg in history:
        prefix = "Daniel" if msg["role"] == "user" else "Jarvis"
        lines.append(f"{prefix}: {msg['content'][:300]}")
    return "\n".join(lines)


def rotate_history(chat_id: str) -> None:
    """Keep only the last MAX_STORED messages to prevent unbounded growth."""
    p = _path(chat_id)
    if not p.exists():
        return
    try:
        lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        if len(lines) > MAX_STORED:
            p.write_text("\n".join(lines[-MAX_STORED:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def clear_history(chat_id: str) -> str:
    """Delete conversation history for this chat."""
    p = _path(chat_id)
    if p.exists():
        p.unlink()
        return f"Historico de conversa limpo para chat {chat_id}."
    return "Nenhum historico encontrado."
