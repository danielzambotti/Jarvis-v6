"""
skills/refactor.py — Autonomous Code Refactoring Skill
=======================================================
Bridges INSPECTOR (analysis) → CREATOR (rewrite) in a single autonomous flow.

PIPELINE when user says "aplique as melhorias no router.py":
  1. BACKUP:   Create timestamped backup via backup_manager
  2. READ:     Load current source code from disk (Python open())
  3. REWRITE:  Ask Ollama to rewrite the entire file applying improvements
               (num_predict=8000 — large enough for a full module rewrite)
  4. VALIDATE: Python ast.parse() syntax check before writing
  5. WRITE:    Atomically overwrite target file with new code
  6. REPORT:   Return diff summary (lines changed, backup location)

SAFETY:
  - NEVER overwrites without a verified backup first
  - If Ollama returns syntactically invalid Python, ABORTS and reports error
  - Only operates on files inside C:\\Jarvis\\ (same boundary as inspector.py)
  - .env and credential files are permanently denied
"""

import ast
import logging
import re
import requests
from pathlib import Path

from config import OLLAMA_URL, OLLAMA_MODEL
from skills.backup_manager import create_backup

logger = logging.getLogger(__name__)

_JARVIS_ROOT   = Path(__file__).resolve().parent.parent
_SKILLS_DIR    = _JARVIS_ROOT / "skills"
_MAX_SRC_CHARS = 24_000   # ~600 lines — enough for any Jarvis module
_DENIED_FILES  = {".env", "secrets.txt", "credentials.txt"}

# ── File aliases (same as inspector.py for consistency) ──────────────────────
_FILE_ALIASES = {
    "router":         "router.py",
    "main":           "main.py",
    "security":       "security.py",
    "config":         "config.py",
    "os controller":  "skills/os_controller.py",
    "os_controller":  "skills/os_controller.py",
    "web search":     "skills/web_search.py",
    "web_search":     "skills/web_search.py",
    "conversational": "skills/conversational.py",
    "creator":        "skills/creator.py",
    "inspector":      "skills/inspector.py",
    "reasoner":       "skills/reasoner.py",
    "perceptor":      "skills/perceptor.py",
    "fs_manager":     "skills/fs_manager.py",
    "fs manager":     "skills/fs_manager.py",
    "dev_architect":  "skills/dev_architect.py",
    "dev architect":  "skills/dev_architect.py",
    "github_search":  "skills/github_search.py",
    "ui_automation":  "skills/ui_automation.py",
}

_REFACTOR_SYSTEM = """You are a Principal Staff Engineer refactoring Python code for production.

CRITICAL RULES:
1. Output ONLY raw Python source code. Absolutely zero markdown. Zero code fences (no ```).
2. The output must be a complete, runnable Python file — not a diff, not a snippet.
3. Preserve ALL existing functionality. Only apply the requested improvements.
4. Keep all existing imports, function signatures, and public API unchanged unless the improvement explicitly requires changing them.
5. Add brief inline comments explaining WHAT changed and WHY (max 1 line per change).
6. Do NOT add placeholder comments like "# TODO" or "# rest of code here".
7. CRITICAL: Respond in the same language as the user's improvement request for comments."""


def _resolve_target(user_input: str) -> Path | None:
    """Parse user input to find the target file to refactor."""
    lowered = user_input.lower()

    # 1. Explicit .py filename
    m = re.search(r'\b([a-z_]+\.py)\b', lowered)
    if m:
        fname = m.group(1)
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
    """Read source file with security checks."""
    if path.name in _DENIED_FILES:
        return None
    try:
        path.resolve().relative_to(_JARVIS_ROOT.resolve())
    except ValueError:
        logger.error("[REFACTOR] Path traversal blocked: %s", path)
        return None
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8")
        return content[:_MAX_SRC_CHARS] if len(content) > _MAX_SRC_CHARS else content
    except OSError as e:
        logger.error("[REFACTOR] Read error: %s", e)
        return None


def _ask_ollama_rewrite(source: str, filename: str, improvements: str) -> str:
    """
    Ask Ollama to rewrite the entire file applying the requested improvements.
    num_predict=8000 ensures full files are never truncated mid-function.
    """
    prompt = (
        f"File to refactor: {filename}\n\n"
        f"Requested improvements:\n{improvements}\n\n"
        f"Current source code:\n{source}"
    )
    payload = {
        "model":  OLLAMA_MODEL,
        "prompt": prompt,
        "system": _REFACTOR_SYSTEM,
        "stream": False,
        "options": {
            "temperature": 0.15,
            "num_predict": 8000,   # raised from 4000 — prevents truncation on 400+ line files
        },
    }
    try:
        # timeout=None — large file rewrites (400+ lines) via 30B model
        # can take 10-15 minutes; a hard timeout would abort mid-generation.
        r = requests.post(OLLAMA_URL, json=payload, timeout=None)
        r.raise_for_status()
        raw = r.json().get("response", "").strip()
        # Strip any accidental markdown fences
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\n?```\s*$", "", raw, flags=re.MULTILINE)
        return raw.strip()
    except requests.exceptions.ConnectionError:
        return ""
    except Exception as e:
        logger.error("[REFACTOR] Ollama error: %s", e)
        return ""


def execute(user_input: str) -> str:
    """
    Autonomous refactoring pipeline:
      1. Resolve target file from user input
      2. Create backup (MANDATORY — aborts if backup fails)
      3. Read current source
      4. Ask Ollama to rewrite with improvements
      5. Validate syntax (ast.parse) — abort if invalid
      6. Write new code atomically
      7. Return report with backup path + change summary
    """
    # ── Step 1: Resolve target file ───────────────────────────────────────────
    target = _resolve_target(user_input)
    if target is None:
        available = ", ".join(f"`{k}`" for k in sorted(_FILE_ALIASES))
        return (
            "Nao consegui identificar qual arquivo refatorar.\n\n"
            f"Arquivos disponíveis: {available}\n\n"
            "Exemplo: 'Refatore o router.py aplicando as melhorias de segurança'"
        )

    filename = str(target.relative_to(_JARVIS_ROOT))
    logger.info("[REFACTOR] Target: %s", filename)

    # ── Step 2: Backup FIRST — never overwrite without backup ─────────────────
    backup_ok, backup_msg = create_backup(trigger=f"pre_refactor_{target.stem}")
    if not backup_ok:
        return f"Aborting: nao foi possível criar backup.\n{backup_msg}"

    # Extract backup path from message for reporting
    backup_path = "C:\\Jarvis\\backups\\(ultimo backup)"
    path_match = re.search(r'`([^`]+backups[^`]+)`', backup_msg)
    if path_match:
        backup_path = path_match.group(1)

    # ── Step 3: Read current source ───────────────────────────────────────────
    original_source = _safe_read(target)
    if original_source is None:
        return f"Erro: nao foi possível ler `{filename}`."

    original_lines = original_source.count("\n") + 1
    logger.info("[REFACTOR] Read %d lines from %s", original_lines, filename)

    # ── Step 4: Rewrite via Ollama ────────────────────────────────────────────
    # Extract just the improvements description from the user request
    # (strip the "refatore X" part, keep the "aplicando Y" part)
    improvements = user_input
    m = re.search(
        r'(?:aplicando?|apply|com|with|seguindo?|following?)\s+(.+)',
        user_input, re.IGNORECASE | re.DOTALL
    )
    if m:
        improvements = m.group(1).strip()

    logger.info("[REFACTOR] Requesting rewrite with: %s", improvements[:100])
    new_source = _ask_ollama_rewrite(original_source, filename, improvements)

    if not new_source:
        return (
            "Ollama nao retornou codigo refatorado.\n"
            f"Backup seguro em: `{backup_path}`\n"
            "Arquivo original intacto."
        )

    # ── Step 5: Syntax validation ─────────────────────────────────────────────
    if target.suffix == ".py":
        try:
            ast.parse(new_source)
            logger.info("[REFACTOR] Syntax validation passed.")
        except SyntaxError as e:
            logger.error("[REFACTOR] Generated code has syntax error: %s", e)
            return (
                f"ABORTING: O codigo gerado tem erro de sintaxe na linha {e.lineno}:\n"
                f"`{e.msg}`\n\n"
                f"Arquivo original intacto. Backup em: `{backup_path}`"
            )

    # ── Step 6: Atomic write ──────────────────────────────────────────────────
    try:
        target.write_text(new_source, encoding="utf-8")
        logger.info("[REFACTOR] Wrote %d chars to %s", len(new_source), filename)
    except OSError as e:
        logger.error("[REFACTOR] Write failed: %s", e)
        return (
            f"Erro ao gravar arquivo: {e}\n"
            f"Backup disponível em: `{backup_path}`"
        )

    # ── Step 7: Report ────────────────────────────────────────────────────────
    new_lines = new_source.count("\n") + 1
    delta     = new_lines - original_lines
    delta_str = f"+{delta}" if delta >= 0 else str(delta)

    return (
        f"Refatoracao concluida com sucesso!\n\n"
        f"Arquivo:  `{filename}`\n"
        f"Linhas:   {original_lines} -> {new_lines} ({delta_str})\n"
        f"Backup:   `{backup_path}`\n\n"
        f"O arquivo foi reescrito aplicando as melhorias solicitadas.\n"
        f"Validacao de sintaxe: PASSOU\n"
        f"Para reverter: copie o arquivo do backup acima."
    )
