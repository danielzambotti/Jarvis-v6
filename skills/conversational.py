"""
skills/conversational.py — Natural Language Conversation Skill
"""
import os
import logging
import requests
from config import OLLAMA_MODEL
from skills.conversation_memory import get_history

logger = logging.getLogger(__name__)

# FIX: Esta skill usa o endpoint /api/chat em vez do /api/generate
OLLAMA_CHAT_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434") + "/api/chat"

_JARVIS_SYSTEM_PROMPT = """You are Jarvis, a highly capable Intelligent Agent and personal AI assistant for Daniel Zambotti, running on his Windows 10 computer. Your tone is professional yet friendly — think Iron Man's Jarvis.

## Your Capabilities (you HAVE all of these):
1. **OS Control** — you can open apps, run commands, check system info via PowerShell.
2. **Web Search** — you CAN search the internet in real time using DuckDuckGo. You have internet access.
3. **File Creator** — you can write Python scripts, text files, JSON, and other documents to disk.
4. **Memory** — every interaction is logged to Notion for context and self-improvement.

## Critical Behaviour Rules:
- NEVER say "I don't have internet access" or "I cannot browse the web." You CAN search.
- If the user asks about current events, prices, news, or real-time data, say:
  "Posso pesquisar isso para você. Quer que eu faça uma busca na web?"
- Be direct. No filler phrases. No unnecessary apologies.
- Use markdown (bold, code blocks, lists) when it improves clarity.
- Respond in the same language the user writes in (PT-BR or EN).
- Keep answers under 400 words unless more detail is clearly needed."""


def respond(user_input: str, chat_id: str = "") -> str:
    history = get_history(chat_id) if chat_id else []
    messages = history + [{"role": "user", "content": user_input}]

    payload = {
        "model":    OLLAMA_MODEL,
        "messages": messages,
        "system":   _JARVIS_SYSTEM_PROMPT,
        "stream":   False,
        "options":  {"temperature": 0.7, "num_predict": 512},
    }

    try:
        logger.info("[CONV] chat=%s history=%d msgs", chat_id or "anon", len(history))
        response = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=60)
        response.raise_for_status()

        data  = response.json()
        reply = (data.get("message") or {}).get("content", "").strip()
        
        if not reply:
            reply = data.get("response", "").strip()

        if not reply:
            return "🤔 I received an empty response from the AI. Please try again."

        logger.info("[CONV] Got response (%d chars)", len(reply))
        return reply

    except requests.exceptions.ConnectionError:
        logger.error("[CONV] Ollama not reachable at %s", OLLAMA_CHAT_URL)
        return (
            "❌ **Ollama is not running.**\n\n"
            "Please start it with:\n```\nollama serve\n```\n"
            "Then make sure your model is pulled:\n"
            f"```\nollama pull {OLLAMA_MODEL}\n```"
        )
    except requests.exceptions.Timeout:
        logger.error("[CONV] Ollama request timed out after 60s")
        return "⏱️ The AI took too long to respond. The model may be loading — please try again in a moment."
    except Exception as e:
        logger.error("[CONV] Unexpected error: %s", e)
        return f"❌ An unexpected error occurred: {e}"