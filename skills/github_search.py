"""
skills/github_search.py — GitHub Docking System
=================================================
MISSION (from Master System Prompt):
  - Search for relevant repositories on GitHub
  - Extract patterns and best practices
  - NEVER blindly copy code
  - Adapt intelligently to local context

CAPABILITIES:
  1. Search repos by topic/keyword via GitHub API (no auth needed for public)
  2. Fetch README content for a given repo
  3. Extract code patterns and best practices using Ollama
  4. Suggest adaptations for the Jarvis project context

SAFETY RULES:
  - Only reads public GitHub data — no write operations
  - No blind copy-paste — all code goes through Ollama analysis first
  - Results are suggestions, not auto-applied changes
  - Rate limit: 60 req/hour unauthenticated (add GITHUB_TOKEN to .env to raise to 5000)
"""

import logging
import os
import re
import requests
from config import OLLAMA_URL, OLLAMA_MODEL

logger = logging.getLogger(__name__)

GITHUB_API   = "https://api.github.com"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")   # optional: raises rate limit
_HEADERS     = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
if GITHUB_TOKEN:
    _HEADERS["Authorization"] = f"Bearer {GITHUB_TOKEN}"

_MAX_REPOS   = 5
_MAX_README  = 3000   # chars of README to analyse


def _gh_get(path: str, params: dict = None) -> dict | list | None:
    """Safe GitHub API GET with error handling."""
    try:
        r = requests.get(
            f"{GITHUB_API}{path}",
            headers=_HEADERS,
            params=params or {},
            timeout=10,
        )
        if r.status_code == 403:
            logger.warning("[GITHUB] Rate limited. Add GITHUB_TOKEN to .env for higher limits.")
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error("[GITHUB] API error: %s", e)
        return None


def search_repos(query: str, language: str = "") -> list[dict]:
    """
    Search GitHub for repositories matching a query.
    Returns list of {name, description, url, stars, language} dicts.
    """
    q = query
    if language:
        q += f" language:{language}"

    data = _gh_get("/search/repositories", {
        "q": q, "sort": "stars", "order": "desc", "per_page": _MAX_REPOS
    })
    if not data or "items" not in data:
        return []

    repos = []
    for item in data["items"][:_MAX_REPOS]:
        repos.append({
            "name":        item.get("full_name", ""),
            "description": (item.get("description") or "")[:200],
            "url":         item.get("html_url", ""),
            "stars":       item.get("stargazers_count", 0),
            "language":    item.get("language") or "unknown",
            "topics":      item.get("topics", []),
        })
    return repos


def fetch_readme(full_name: str) -> str:
    """Fetch the README of a repo as plain text (truncated)."""
    data = _gh_get(f"/repos/{full_name}/readme")
    if not data:
        return ""
    import base64
    try:
        content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
        # Strip markdown badges and HTML tags
        content = re.sub(r'\[!\[.*?\]\(.*?\)\]\(.*?\)', '', content)
        content = re.sub(r'<[^>]+>', '', content)
        return content[:_MAX_README]
    except Exception:
        return ""


def _analyse_repo(repo: dict, readme: str, context: str) -> str:
    """Ask Ollama to extract patterns and suggest adaptations."""
    prompt = (
        f"Repository: {repo['name']}\n"
        f"Description: {repo['description']}\n"
        f"Stars: {repo['stars']}\n\n"
        f"README excerpt:\n{readme[:1500]}\n\n"
        f"Project context: {context}\n\n"
        f"Task: Extract the 3 most useful patterns or best practices from this repo "
        f"that could improve the project described in 'Project context'. "
        f"Be specific. Never suggest copy-pasting code. Suggest architectural adaptations only."
    )
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 400},
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=45)
        return r.json().get("response", "").strip()
    except Exception:
        return ""


_JARVIS_CONTEXT = (
    "Jarvis is a local Python AI assistant running on Windows 10. "
    "It uses Ollama (local LLM), Telegram Bot API, DuckDuckGo search, "
    "PowerShell execution, and Notion for memory. "
    "It has a modular skill-based architecture (router → skill → execute)."
)


def execute(user_input: str) -> str:
    """
    Main entry point — search GitHub and return analysis.

    Handles requests like:
      "search github for MCP Windows"
      "find repos for telegram bot python"
      "github best practices for ollama agent"
    """
    # Extract search query from user input
    query_match = re.search(
        r'(?:github|buscar?|search|find|procurar?|repos?)\s+(?:for\s+|por\s+|de\s+)?(.+)',
        user_input, re.IGNORECASE
    )
    query = query_match.group(1).strip() if query_match else user_input

    # Detect optional language filter
    lang = ""
    for candidate in ["python", "typescript", "javascript", "go", "rust"]:
        if candidate in user_input.lower():
            lang = candidate
            break

    logger.info("[GITHUB] Searching: '%s' lang=%s", query, lang or "any")

    repos = search_repos(query, lang)
    if not repos:
        return (
            f"Nao encontrei repositorios para '{query}' no GitHub.\n"
            f"Verifique sua conexao ou tente termos diferentes."
        )

    lines = [f"**GitHub: '{query}' — {len(repos)} repositorios encontrados**\n"]

    for repo in repos:
        lines.append(
            f"**{repo['name']}** ({repo['stars']} stars | {repo['language']})\n"
            f"{repo['description']}\n"
            f"{repo['url']}"
        )

    # Analyse the top repo in detail
    top = repos[0]
    readme = fetch_readme(top["name"])
    if readme:
        analysis = _analyse_repo(top, readme, _JARVIS_CONTEXT)
        if analysis:
            lines.append(
                f"\n**Analise do top repo `{top['name']}`:**\n{analysis}"
            )

    return "\n\n".join(lines)
