"""
skills/creator.py — File & Script Creator Skill (v3 — production-stable)
=========================================================================
FIXES v3:
  [BUG-A] Duplicate _get_workspace() definition removed (second one shadowed first)
  [BUG-B] ALLOWED_EXTENSIONS/MAX_FILE_SIZE_CHARS moved to module top-level constants
  [BUG-C] Pure Python open().write() — zero PowerShell, zero subprocess, zero escaping

ARCHITECTURE:
  Phase A: Extract safe filename from user intent
  Phase B: Generate file content via Ollama (text only, no shell commands)
  Phase C: Write to C:\\Jarvis\\workspace\\ using Python open() exclusively
"""

import logging
import re
import requests
from pathlib import Path
from config import OLLAMA_URL, OLLAMA_MODEL
from skills.backup_manager import create_backup
from security import is_safe_path

logger = logging.getLogger(__name__)

# ── Module-level constants (defined ONCE, used everywhere) ───────────────────
ALLOWED_EXTENSIONS = {".py", ".txt", ".md", ".json", ".bat", ".ps1", ".csv", ".yaml", ".yml"}
MAX_FILE_SIZE_CHARS = 20_000  # ~20KB hard cap to prevent disk exhaustion


def _get_workspace() -> Path:
    """
    Returns the workspace directory path, creating it if needed.
    Always resolved relative to THIS file's location — never uses cwd.

    C:\\Jarvis\\skills\\creator.py
      → parent: C:\\Jarvis\\skills\\
      → parent: C:\\Jarvis\\
      → / "workspace": C:\\Jarvis\\workspace\\
    """
    ws = Path(__file__).resolve().parent.parent / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


# ── Ollama system prompts ─────────────────────────────────────────────────────

_PYTHON_SYSTEM_PROMPT = """You are an expert Python developer writing a standalone script.

STRICT OUTPUT RULES — VIOLATIONS WILL BREAK THE SYSTEM:
1. Output ONLY raw Python code. Absolutely zero markdown. Zero code fences (no ```).
2. The very first line must be a triple-quoted docstring describing the script.
3. Use only the Python standard library unless a package is explicitly requested.
4. All logic must be inside an if __name__ == "__main__": block at the bottom.
5. Add try/except around all file I/O and network operations.
6. No placeholder comments like "# add your code here". Write real, working code."""

_TEXT_SYSTEM_PROMPT = """You are a precise technical writer creating a file.

STRICT OUTPUT RULES:
1. Output ONLY the file content. Zero explanation. Zero preamble. Zero postamble.
2. Match the exact format implied by the filename extension and user request."""


def _sanitize_filename(raw: str) -> str | None:
    """
    Sanitize a filename string from user input.
    - Strips path separators and shell-dangerous characters
    - Blocks directory traversal sequences
    - Enforces the ALLOWED_EXTENSIONS whitelist
    - Auto-appends .py if no extension present
    Returns safe filename string, or None if invalid.
    """
    # Strip any path component and dangerous shell characters
    name = re.sub(r'[/\\<>:"|?*]', '', raw).strip()
    # Collapse ".." sequences used in traversal attacks
    name = re.sub(r'\.\.+', '.', name)

    if not name:
        return None

    # Auto-add .py if user gave a bare name like "my_script"
    if '.' not in name:
        name += ".py"

    if Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
        logger.warning("[CREATOR] Blocked disallowed extension: '%s'", Path(name).suffix)
        return None

    return name


def _extract_filename(user_input: str) -> str:
    """
    Parse the desired filename from user natural language.

    Priority order:
      1. Explicit keyword: "chamado X.py" / "called X.py" / "named X.py"
      2. Quoted filename: "structured_logger.py"
      3. Bare known extension: anything ending in .py/.json/etc.
      4. Fallback slug from first 4 meaningful words + .py
    """
    patterns = [
        # PT-BR and EN explicit naming keywords
        r'(?:chamad[ao]|called|named|nomeado|salv[ae]\s+como|save\s+as)\s+"?([a-zA-Z0-9_\-\.]+\.[a-z]{2,5})"?',
        # Filename inside double quotes
        r'"([a-zA-Z0-9_\-]+\.[a-z]{2,5})"',
        # Bare filename token with a recognised extension
        r'\b([a-zA-Z0-9_\-]+\.(?:py|txt|md|json|bat|ps1|csv|yaml|yml))\b',
    ]
    for pat in patterns:
        m = re.search(pat, user_input, re.IGNORECASE)
        if m:
            return m.group(1)

    # Fallback: slug from first meaningful words
    words = re.sub(r'[^a-zA-Z0-9\s]', '', user_input).split()
    slug_words = [w.lower() for w in words if len(w) > 3][:4]
    return f"{'_'.join(slug_words) or 'jarvis_file'}.py"


def _generate_content(user_input: str, filename: str) -> str:
    """
    Phase B: Ask Ollama to generate the file content as raw text.

    Key design choices:
    - Uses _PYTHON_SYSTEM_PROMPT for .py files (strict no-markdown rules)
    - Uses _TEXT_SYSTEM_PROMPT for all other extensions
    - Strips any markdown fences the model accidentally adds
    - Returns empty string on any failure (caller handles the error)
    """
    ext = Path(filename).suffix.lower()
    system = _PYTHON_SYSTEM_PROMPT if ext == ".py" else _TEXT_SYSTEM_PROMPT
    prompt = f"Create the file named '{filename}' that does the following:\n\n{user_input}"

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {
            "temperature": 0.2,    # Low temp = consistent, correct code
            "num_predict": 2000,   # ~1500 tokens → enough for a complete script
        },
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=120)
        r.raise_for_status()
        content = r.json().get("response", "").strip()

        # Remove any markdown code fences the model accidentally adds
        # These patterns cover: ```python, ```py, ```, etc.
        content = re.sub(r"^```[a-z]*\s*\n?", "", content, flags=re.MULTILINE)
        content = re.sub(r"\n?```\s*$", "", content, flags=re.MULTILINE)

        return content.strip()

    except requests.exceptions.ConnectionError:
        logger.error("[CREATOR] Ollama not reachable at %s", OLLAMA_URL)
        return ""
    except requests.exceptions.Timeout:
        logger.error("[CREATOR] Ollama request timed out after 120s")
        return ""
    except Exception as e:
        logger.error("[CREATOR] Unexpected generation error: %s", e)
        return ""


def execute(user_input: str) -> str:
    """
    Main skill entry point called by main.py.

    Phase A: Extract + sanitize the target filename from user intent
    Phase B: Generate file content via Ollama (pure text, no shell)
    Phase C: Write to workspace using Python open() only — no subprocess

    Args:
        user_input: Sanitized natural language from the user.
    Returns:
        A human-readable confirmation string with file path + preview,
        or a descriptive error message.
    """
    workspace = _get_workspace()

    # ── Phase A: Filename ─────────────────────────────────────────────────────
    raw_name  = _extract_filename(user_input)
    filename  = _sanitize_filename(raw_name)

    if not filename:
        return (
            "⚠️ Nome de arquivo inválido ou extensão não permitida.\n"
            "Extensões aceitas: .py  .txt  .md  .json  .bat  .ps1  .csv  .yaml  .yml"
        )

    logger.info("[CREATOR] Target file: %s", filename)

    # ── Phase B: Generate content ─────────────────────────────────────────────
    content = _generate_content(user_input, filename)

    if not content:
        return (
            "❌ Ollama não retornou conteúdo.\n"
            "Verifique se está rodando: `ollama serve`\n"
            f"Modelo configurado: `{OLLAMA_MODEL}`"
        )

    if len(content) > MAX_FILE_SIZE_CHARS:
        logger.warning("[CREATOR] Truncating content from %d to %d chars",
                       len(content), MAX_FILE_SIZE_CHARS)
        content = content[:MAX_FILE_SIZE_CHARS]

    # ── Phase C: Write with pure Python open() ────────────────────────────────
    target = workspace / filename

    # Phase 1.1: use is_safe_path (os.path.abspath-based) instead of resolve()
    if not is_safe_path(str(target)):
        logger.error("[CREATOR] Path traversal blocked: %s", target)
        return "Operacao bloqueada: tentativa de path traversal detectada."

    # Auto-backup before writing (Self-Improvement Engine rule)
    create_backup(trigger="pre_create")

    try:
        # Pure Python I/O — no PowerShell, no subprocess, no quoting issues
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)

        line_count = content.count("\n") + 1
        preview    = "\n".join(content.splitlines()[:10])
        has_more   = line_count > 10

        return "\n".join(filter(None, [
            "✅ **Arquivo criado com sucesso!**",
            f"📁 `{target}`",
            f"📊 {len(content):,} chars  |  {line_count} linhas",
            f"\n**Preview:**\n```python\n{preview}",
            "..." if has_more else "",
            "```",
        ]))

    except OSError as e:
        logger.error("[CREATOR] Write failed: %s", e)
        return f"❌ Erro ao gravar arquivo: {e}"
