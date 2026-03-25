"""
skills/reasoner.py — ReAct Engine v2 (Phase 3 Fix)
====================================================
FIXES (from production failure analysis):

  [BUG-01] JSONDecodeError on empty/partial Ollama response
    Root cause: Model returned preamble text before JSON, or empty string.
    Fix 1: Add "format": "json" to payload — forces Ollama to output valid JSON.
    Fix 2: Retry up to 2 times on JSONDecodeError, appending a stricter reminder.
    Fix 3: Aggressive markdown fence stripping + extract first {...} block.

  [BUG-02] num_predict too low for ReAct thought process
    Fix: Raise to 500 for ReAct, 350 for simple reasoning.

  [BUG-03] System prompts not assertive enough about JSON-only output
    Fix: Prepend "CRITICAL:" to JSON-only rule in both system prompts.
"""

import json
import logging
import re
import requests
from config import OLLAMA_URL, OLLAMA_MODEL
from skills.conversation_memory import get_context_string

logger = logging.getLogger(__name__)

_MAX_STEPS    = 6
_JSON_RETRIES = 2   # retry count on JSONDecodeError

_REACT_SYSTEM = """You are Jarvis's ReAct reasoning engine.

CRITICAL: Output ONLY a single valid JSON object. No markdown. No code fences. No explanation before or after. The first character of your response MUST be '{'.

Output this exact schema:
{
  "thought": "your reasoning about current state",
  "action": "ONE of: web_search | os_command | create_file | inspect_code | converse | done",
  "action_input": "specific input for the action",
  "needs_web_search": true,
  "needs_os_action": false,
  "search_query": "search query string or null",
  "final_answer": "complete response when action is done, else null"
}

Use action=done when goal is achieved or cannot proceed further."""

_SIMPLE_SYSTEM = """You are Jarvis's intent analyzer.

CRITICAL: Output ONLY a single valid JSON object. No markdown. No code fences. No preamble. First character MUST be '{'.

{
  "intent": "one sentence describing what the user wants",
  "needs_web_search": true,
  "needs_os_action": false,
  "needs_file_creation": false,
  "search_query": "optimal search query or null",
  "reasoning": "2-3 sentences of reasoning",
  "response_plan": "how to respond"
}"""


def _extract_json(raw: str) -> str:
    """
    Robustly extract a JSON object from raw Ollama output.

    Handles:
      - Markdown code fences (```json ... ```)
      - Preamble text before the JSON object
      - Trailing explanation after the closing brace
    """
    # 1. Strip markdown fences
    raw = re.sub(r"^```[a-zA-Z]*\s*\n?", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"\n?```\s*$", "", raw.strip(), flags=re.MULTILINE)
    raw = raw.strip()

    # 2. Find first complete {...} block — handles preamble text
    start = raw.find("{")
    if start == -1:
        return raw   # let caller raise JSONDecodeError

    # Walk forward to find matching closing brace
    depth = 0
    for i, ch in enumerate(raw[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start:i + 1]

    return raw[start:]   # truncated — let caller handle


def _call_ollama(prompt: str, system: str, max_tokens: int = 400) -> dict:
    """
    Call Ollama and return parsed JSON dict.

    Improvements over v1:
      - "format": "json" in payload forces Ollama to emit JSON
      - Up to _JSON_RETRIES retries on JSONDecodeError
      - Each retry appends a stricter reminder to the prompt
      - _extract_json() handles preamble/fence pollution
    """
    base_prompt = prompt
    for attempt in range(1 + _JSON_RETRIES):
        retry_suffix = (
            "\n\nCRITICAL: Output MUST be strictly valid JSON without "
            "markdown wrapping. Start with '{' immediately."
            if attempt > 0 else ""
        )
        payload = {
            "model":  OLLAMA_MODEL,
            "prompt": base_prompt + retry_suffix,
            "system": system,
            "stream": False,
            "format": "json",          # forces Ollama to emit JSON
            "options": {
                "temperature": 0.0,    # deterministic on retry
                "num_predict": max_tokens,
            },
        }
        try:
            r = requests.post(OLLAMA_URL, json=payload, timeout=120)
            r.raise_for_status()
            raw = r.json().get("response", "").strip()
            cleaned = _extract_json(raw)
            result = json.loads(cleaned)
            if attempt > 0:
                logger.info("[REASONER] JSON parsed on retry %d", attempt)
            return result

        except json.JSONDecodeError as e:
            logger.warning(
                "[REASONER] JSONDecodeError attempt %d/%d: %s | raw: %s",
                attempt + 1, 1 + _JSON_RETRIES, e, raw[:120] if 'raw' in dir() else "?"
            )
            if attempt == _JSON_RETRIES:
                logger.error("[REASONER] All retries exhausted. Returning {}.")
                return {}
        except Exception as e:
            logger.warning("[REASONER] Ollama call failed: %s", e)
            return {}

    return {}


def reason_about(user_input: str, chat_id: str) -> dict:
    """
    Simple one-shot intent classification with conversation context.
    Returns a plan dict with needs_web_search, search_query, etc.
    Falls back to {} — callers must handle empty dict gracefully.
    """
    context = get_context_string(chat_id, n=6)
    context_block = f"\n\nRecent conversation:\n{context}" if context else ""
    prompt = f"User request: {user_input}{context_block}"
    plan = _call_ollama(prompt, _SIMPLE_SYSTEM, max_tokens=350)
    logger.info("[REASONER] Simple plan: %s", str(plan)[:150])
    return plan


def react_loop(goal: str, chat_id: str, execute_fn=None) -> str:
    """
    Full ReAct loop for complex multi-step goals (max _MAX_STEPS iterations).

    Args:
        goal:       The user's complex goal
        chat_id:    Used to inject conversation history context
        execute_fn: Optional callable(action, action_input) -> str

    Returns:
        Final answer string for the user.
    """
    context = get_context_string(chat_id, n=8)
    observations: list[str] = []

    for step in range(_MAX_STEPS):
        obs_summary = "\n".join(
            f"Step {i+1} result: {o[:200]}"
            for i, o in enumerate(observations)
        )
        prompt = (
            f"Goal: {goal}\n"
            f"Step: {step + 1}/{_MAX_STEPS}\n"
            f"Previous observations:\n{obs_summary or 'None yet'}\n"
            f"Context: {context[:400] if context else 'None'}"
        )

        plan = _call_ollama(prompt, _REACT_SYSTEM, max_tokens=500)
        if not plan:
            break

        thought      = plan.get("thought", "")
        action       = plan.get("action", "done")
        action_input = plan.get("action_input", "")

        logger.info("[REACT] Step %d | action=%s | input=%s",
                    step + 1, action, str(action_input)[:80])

        if action == "done" or not action_input:
            return plan.get("final_answer") or thought or "Tarefa concluida."

        if execute_fn:
            try:
                obs = execute_fn(action, action_input)
            except Exception as e:
                obs = f"ERROR: {e}"
        else:
            obs = f"[Plan only] action={action} input={action_input}"

        observations.append(obs)
        logger.info("[REACT] Observation: %s", obs[:150])

    summary = "\n".join(f"- {o[:150]}" for o in observations)
    return f"Completei {len(observations)} passos:\n{summary}" if observations else "Nao foi possivel completar a tarefa."
