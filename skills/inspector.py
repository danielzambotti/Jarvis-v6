"""
skills/inspector.py — Jarvis Self-Inspection Skill
====================================================
Allows Jarvis to read its own source code and provide code review,
architectural analysis, and self-improvement suggestions.

WHAT IT CAN READ:
  - C:\\Jarvis\\main.py
  - C:\\Jarvis\\router.py
  - C:\\Jarvis\\security.py
  - C:\\Jarvis\\config.py
  - C:\\Jarvis\\skills\\*.py  (any skill file)

PIPELINE:
  1. Parse which file(s) the user wants to inspect
  2. Read the file(s) from disk (Python open() — no shell)
  3. Send source code + user question to Ollama for analysis
  4. Return the review/suggestions

SECURITY NOTE:
  Only files inside C:\\Jarvis\\ can be read. Path traversal is blocked.
  Reading .env or credential files is explicitly denied.
"""

import logging
import re
from pathlib import Path
import requests
from config import OLLAMA_URL, OLLAMA_MODEL

logger = logging.getLogger(__name__)

# ── Project root (always resolved from this file's location) ─────────────────
_JARVIS_ROOT   = Path(__file__).resolve().parent.parent   # C:\Jarvis\
_SKILLS_DIR    = _JARVIS_ROOT / "skills"
_MAX_SRC_CHARS = 6000   # Clip large files to avoid context overflow

# Files that must NEVER be read (security)
_DENIED_FILES = {".env", "secrets.txt", "credentials.txt"}

# Canonical file aliases the user might say
_FILE_ALIASES = {
    "router":        "router.py",
    "main":          "main.py",
    "security":      "security.py",
    "config":        "config.py",
    "os controller": "skills/os_controller.py",
    "os_controller": "skills/os_controller.py",
    "web search":    "skills/web_search.py",
    "web_search":    "skills/web_search.py",
    "conversational":"skills/conversational.py",
    "creator":       "skills/creator.py",
    "inspector":     "skills/inspector.py",
    "logger":        "skills/structured_logger.py",
    "notion":        "skills/notion_logger.py",
    "voice":         "skills/voice_handler.py",
}


_REVIEW_SYSTEM_PROMPT = """You are a Principal Staff Engineer doing a code review of Jarvis, a personal AI assistant.

CRITICAL LANGUAGE RULE: You MUST respond in the EXACT same language the user used.
- If the user wrote in Portuguese (PT-BR), your ENTIRE response MUST be 100% in PT-BR.
- If the user wrote in English, respond in English.
- NEVER switch languages mid-response. NEVER default to English when the prompt is in Portuguese.

Your review style:
1. Be specific — cite line numbers or function names when relevant.
2. Prioritise: security issues > correctness bugs > performance > style.
3. For each issue found, suggest a concrete fix (1-3 lines of code).
4. End with a short summary: overall quality rating (1-5 estrelas) and top 3 improvement ideas.
5. When suggesting code fixes, use the same language as the file being reviewed (Python stays Python)."""


def _resolve_file(user_input: str) -> Path | None:
    """
    Parse the user's message to find which source file to read.

    Priority:
      1. Exact .py filename mentioned (e.g. "router.py")
      2. Alias match (e.g. "router" → router.py)
      3. Any skill file mentioned by partial name
    Returns resolved Path or None if not found.
    """
    lowered = user_input.lower()

    # 1. Exact filename with .py
    m = re.search(r'\b([a-z_]+\.py)\b', lowered)
    if m:
        fname = m.group(1)
        # Check root dir first, then skills/
        for candidate in [_JARVIS_ROOT / fname, _SKILLS_DIR / fname]:
            if candidate.exists():
                return candidate

    # 2. Alias lookup
    for alias, rel_path in _FILE_ALIASES.items():
        if alias in lowered:
            candidate = _JARVIS_ROOT / rel_path
            if candidate.exists():
                return candidate

    return None


def _safe_read(path: Path) -> str | None:
    """
    Read a source file safely. Returns content or None on error/denial.
    Enforces:
      - File must be inside _JARVIS_ROOT
      - File must not be in the denied list
      - File must exist and be a .py file
    """
    # Deny sensitive files
    if path.name in _DENIED_FILES:
        logger.warning("[INSPECTOR] Denied read of sensitive file: %s", path)
        return None

    # Enforce root boundary
    try:
        path.resolve().relative_to(_JARVIS_ROOT.resolve())
    except ValueError:
        logger.error("[INSPECTOR] Path traversal blocked: %s", path)
        return None

    if not path.exists():
        return None

    try:
        content = path.read_text(encoding="utf-8")
        if len(content) > _MAX_SRC_CHARS:
            content = content[:_MAX_SRC_CHARS] + f"\n\n... [clipped at {_MAX_SRC_CHARS} chars]"
        return content
    except OSError as e:
        logger.error("[INSPECTOR] Read error: %s", e)
        return None


def _analyse(source_code: str, filename: str, user_question: str) -> str:
    """Send source + question to Ollama for code review."""
    prompt = (
        f"File: {filename}\n\n"
        f"```python\n{source_code}\n```\n\n"
        f"User request: {user_question}"
    )
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "system": _REVIEW_SYSTEM_PROMPT,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 1024},
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=90)
        r.raise_for_status()
        return r.json().get("response", "").strip()
    except requests.exceptions.ConnectionError:
        return "❌ Ollama não está rodando. Use: `ollama serve`"
    except Exception as e:
        logger.error("[INSPECTOR] Analysis error: %s", e)
        return f"❌ Erro na análise: {e}"


def execute(user_input: str) -> str:
    """
    Main entry point: resolve file → read → analyse with Ollama.

    Special case: if user asks "list your files" or "what files do you have",
    return a directory listing of the Jarvis project instead.
    """
    # ── Special: list all source files ───────────────────────────────────────
    lowered = user_input.lower()
    if any(k in lowered for k in ["list files", "liste arquivos", "what files",
                                   "quais arquivos", "show structure"]):
        files = sorted(_JARVIS_ROOT.glob("*.py")) + \
                sorted(_SKILLS_DIR.glob("*.py"))
        names = [f"📄 `{f.relative_to(_JARVIS_ROOT)}`" for f in files]
        return "📁 **Arquivos do Jarvis:**\n\n" + "\n".join(names)

    # ── Resolve which file to read ────────────────────────────────────────────
    target = _resolve_file(user_input)

    if target is None:
        available = ", ".join(f"`{k}`" for k in sorted(_FILE_ALIASES))
        return (
            "🔍 Não encontrei o arquivo solicitado.\n\n"
            f"**Arquivos que posso inspecionar:**\n{available}\n\n"
            "Exemplo: *leia o router.py e diga se é eficiente*"
        )

    # ── Read source code ──────────────────────────────────────────────────────
    source = _safe_read(target)
    if source is None:
        return f"❌ Não consegui ler `{target.name}`. Arquivo negado ou inexistente."

    logger.info("[INSPECTOR] Analysing %s (%d chars)", target.name, len(source))
    filename = str(target.relative_to(_JARVIS_ROOT))

    # ── Analyse with Ollama ───────────────────────────────────────────────────
    analysis = _analyse(source, filename, user_input)

    return (
        f"🔬 **Análise de `{filename}`**\n"
        f"📊 {len(source.splitlines())} linhas | {len(source):,} chars\n\n"
        f"{analysis}"
    )
