"""
skills/web_search.py — DuckDuckGo Web Search Skill (v2)
========================================================
MIGRATION v2: Updated from deprecated DDG API to modern DDGS context manager.

PATTERN:
  with DDGS() as ddgs:
      results = [r for r in ddgs.text(query, max_results=5)]

TWO-PHASE SEARCH with fallback:
  Phase 1: Precise query extracted by Ollama
  Phase 2: If Phase 1 returns 0 results → broaden to raw user input
  Phase 3: Ollama summarises the results in natural language (PT-BR)
"""

import logging
import os
import requests

# Use the modern `ddgs` package (v9+). The old `duckduckgo_search` package
# was renamed and returns 0 results on all queries as of 2025.
# Correct import: `from ddgs import DDGS`
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS   # fallback for older installs

from config import OLLAMA_MODEL

# Build URL from OLLAMA_HOST directly — same pattern as os_controller.py.
# Avoids vault/env-var override that may return a bare host without the path suffix.
_OLLAMA_BASE         = os.environ.get("OLLAMA_HOST", "http://ollama:11434")
_OLLAMA_GENERATE_URL = _OLLAMA_BASE + "/api/generate"

logger = logging.getLogger(__name__)

_MAX_RESULTS   = 5
_CONTEXT_CHARS = 300   # chars to keep per result body


def _extract_query(prompt: str) -> str:
    """Ask Ollama to extract the ideal search query from the user prompt."""
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": (
            "Extract the ideal web search query from the text below.\n"
            "Reply with ONLY the search term — no quotes, no explanation.\n\n"
            f"Text: {prompt}"
        ),
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 30},
    }
    try:
        r = requests.post(_OLLAMA_GENERATE_URL, json=payload, timeout=10)
        q = r.json().get("response", "").strip().strip("'\"")
        return q if q else prompt
    except Exception:
        return prompt


def _ddg_search(query: str) -> list[dict]:
    """
    Execute DuckDuckGo search using the ddgs package (v9+).
    Supports both context manager and direct instantiation.
    Returns a list of result dicts with 'title', 'body', 'href' keys.
    """
    try:
        # ddgs v9 supports direct call without context manager
        d = DDGS()
        results = list(d.text(query, max_results=_MAX_RESULTS))
        logger.info("[WEB] '%s' → %d results", query, len(results))
        return results
    except Exception as e:
        logger.error("[WEB] DDGS error for query '%s': %s", query, e)
        return []


def _summarise(prompt: str, context: str) -> str:
    """Ask Ollama to answer the user's question based on the search results."""
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": (
            "Answer the user's question in natural, fluent Portuguese based on "
            "the web results below. Be direct and cite sources when relevant.\n\n"
            f"User question: {prompt}\n\n"
            f"Web results:\n{context}"
        ),
        "stream": False,
        "options": {"temperature": 0.4, "num_predict": 512},
    }
    try:
        r = requests.post(_OLLAMA_GENERATE_URL, json=payload, timeout=60)
        return r.json().get("response", "").strip()
    except Exception:
        return "Encontrei os resultados mas não consegui resumir. Tente novamente."


def execute(prompt: str) -> str:
    """
    Main entry point: search the web and return an AI-summarised answer.

    Phase 1: Extract precise search query via Ollama
    Phase 2: Search DuckDuckGo (modern DDGS context manager)
    Phase 3: If no results, retry with raw prompt (broader fallback)
    Phase 4: Summarise results with Ollama in PT-BR
    """
    # ── Phase 1: Query extraction ─────────────────────────────────────────────
    search_query = _extract_query(prompt)
    logger.info("[WEB] Extracted query: %s", search_query)

    # ── Phase 2: Primary search ───────────────────────────────────────────────
    results = _ddg_search(search_query)

    # ── Phase 3: Fallback with broader term if empty ──────────────────────────
    if not results and search_query.lower() != prompt.lower():
        logger.warning("[WEB] No results for '%s', retrying with raw prompt.", search_query)
        results = _ddg_search(prompt)

    if not results:
        return (
            "🌐 Não encontrei resultados para essa pesquisa.\n"
            "Tente reformular a pergunta com termos diferentes."
        )

    # ── Phase 4: Build context + summarise ────────────────────────────────────
    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "Sem título")
        body  = (r.get("body") or "")[:_CONTEXT_CHARS]
        href  = r.get("href", "")
        lines.append(f"[{i}] {title}\n{body}\nFonte: {href}")
    context = "\n\n".join(lines)

    summary = _summarise(prompt, context)
    return f"🌐 **Resultado da pesquisa:**\n\n{summary}"
