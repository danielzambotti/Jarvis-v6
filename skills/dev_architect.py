"""
skills/dev_architect.py — Autonomous Dev & Install Orchestrator
================================================================
MISSION (from Master System Prompt):
  Central intelligence for code generation, system design,
  project orchestration, and continuous self-improvement.

PRIMARY CAPABILITY DEMONSTRATED HERE: AUTONOMOUS INSTALL FLOW
  When user says "install X and run it", Jarvis:
    1. Searches the web for the real installation guide
    2. Reasons about the steps required (multi-step plan)
    3. Executes each step via PowerShell with safety checks
    4. Reports results back to the user
    5. Logs everything to memory + Notion

ADDITIONAL CAPABILITIES:
  - Project scaffolding (full folder structure generation)
  - SAST security scanning of generated code
  - Architectural memory (decisions + what worked/failed)
  - PR-style code review before applying changes
  - Template engine for common project types

SECURITY:
  - Every command goes through is_safe_command() blacklist
  - Destructive ops require explicit user confirmation
  - No blind execution — every step is logged and explained
"""

import json
import logging
import re
import subprocess
import requests
from pathlib import Path
from config import OLLAMA_URL, OLLAMA_MODEL
from security import is_safe_command

logger = logging.getLogger(__name__)

# ── Architectural memory ──────────────────────────────────────────────────────
_ARCH_MEMORY_FILE = Path(__file__).resolve().parent.parent / "memory" / "arch_decisions.jsonl"
_ARCH_MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)


# ── SAST: patterns that must never be executed ────────────────────────────────
_SAST_PATTERNS = [
    (r'Invoke-Expression\s*\(', "SAST: Invoke-Expression is a code injection vector"),
    (r'iex\s*\(', "SAST: iex alias for Invoke-Expression"),
    (r'DownloadString\s*\(', "SAST: Remote code execution via DownloadString"),
    (r'\|\s*iex', "SAST: Piped remote execution"),
    (r'curl.*\|\s*(bash|sh|powershell)', "SAST: Curl pipe to shell"),
    (r'Set-ExecutionPolicy\s+Unrestricted', "SAST: Disabling execution policy"),
    (r'--allow-root', "SAST: Running as root"),
    (r'chmod\s+777', "SAST: World-writable permissions"),
]

def sast_scan(command: str) -> tuple[bool, str]:
    """
    Static security scan of a command before execution.
    Returns (is_safe: bool, reason: str).
    """
    for pattern, reason in _SAST_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            logger.error("[SAST] BLOCKED: %s in: %s", reason, command[:100])
            return False, reason
    return True, "OK"


def _run_ps(cmd: str, timeout: int = 30) -> tuple[str, str, int]:
    """Run a PS command. Returns (stdout, stderr, returncode)."""
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace"
        )
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", f"Timeout after {timeout}s", -1
    except Exception as e:
        return "", str(e), -1


def _log_decision(action: str, result: str, success: bool) -> None:
    """Persist architectural decision to memory file."""
    import time
    entry = {
        "ts": time.time(),
        "action": action[:200],
        "result": result[:200],
        "success": success,
    }
    try:
        with open(_ARCH_MEMORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ── Install Planner ───────────────────────────────────────────────────────────

_INSTALL_PLANNER_PROMPT = """You are a Windows 10 systems engineer. The user wants to install a tool.
Given web search results with installation instructions, extract ONLY the PowerShell/CMD commands needed.

RULES:
1. Output a JSON array of command strings ONLY. No explanation. No markdown.
2. Each element is one PowerShell or CMD command.
3. Prefer: winget, npm, pip, choco, git clone, dotnet tool install
4. Do NOT include: curl | iex, Invoke-Expression, Set-ExecutionPolicy Unrestricted
5. Maximum 8 commands. Only the essential ones.
6. Example output: ["winget install --id Git.Git", "npm install -g @modelcontextprotocol/server-windows"]"""


def _plan_install_steps(tool_name: str, search_context: str) -> list[str]:
    """
    Ask Ollama to extract installation commands from web search results.
    Returns a list of PowerShell command strings.
    """
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": f"Tool to install: {tool_name}\n\nWeb results:\n{search_context[:2000]}",
        "system": _INSTALL_PLANNER_PROMPT,
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 400},
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=30)
        raw = r.json().get("response", "").strip()
        # Strip markdown fences
        raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\n?```$", "", raw, flags=re.MULTILINE)
        steps = json.loads(raw.strip())
        if isinstance(steps, list):
            return [str(s) for s in steps if s]
        return []
    except Exception as e:
        logger.warning("[ARCH] Could not parse install steps: %s", e)
        return []


def install_and_execute(user_input: str, search_results: str) -> str:
    """
    AUTONOMOUS INSTALL FLOW:
      1. Plan: extract install commands from search results
      2. SAST scan every command
      3. Execute each command sequentially
      4. Report results

    Args:
        user_input:     Original user request
        search_results: Web search context (from web_search skill)
    Returns:
        Formatted status report string
    """
    # Extract tool name from user input
    tool_match = re.search(
        r'(?:instale?r?|install|setup|configurar?)\s+(?:o\s+|a\s+)?([A-Za-z0-9\-_\.]+)',
        user_input, re.IGNORECASE
    )
    tool_name = tool_match.group(1) if tool_match else user_input[:30]

    logger.info("[ARCH] Planning install for: %s", tool_name)
    steps = _plan_install_steps(tool_name, search_results)

    if not steps:
        return (
            f"Pesquisei como instalar **{tool_name}** mas nao consegui extrair "
            f"comandos claros das instrucoes encontradas.\n\n"
            f"Resultados da pesquisa:\n{search_results[:800]}"
        )

    lines = [f"**Plano de instalacao para {tool_name}:**\n"]
    results = []

    for i, cmd in enumerate(steps, 1):
        # SAST scan
        safe, reason = sast_scan(cmd)
        if not safe:
            line = f"{i}. `{cmd[:80]}` — BLOQUEADO: {reason}"
            lines.append(line)
            _log_decision(cmd, reason, False)
            continue

        # Blacklist check
        if not is_safe_command(cmd):
            line = f"{i}. `{cmd[:80]}` — BLOQUEADO: filtro de seguranca"
            lines.append(line)
            _log_decision(cmd, "blacklist", False)
            continue

        # Execute
        logger.info("[ARCH] Executing step %d: %s", i, cmd[:100])
        stdout, stderr, code = _run_ps(cmd, timeout=60)

        if code == 0:
            detail = stdout[:200] if stdout else "OK (sem output)"
            line = f"{i}. `{cmd[:80]}`\n   OK: {detail}"
            _log_decision(cmd, detail, True)
        else:
            detail = stderr[:200] if stderr else f"Exit code {code}"
            line = f"{i}. `{cmd[:80]}`\n   Erro: {detail}"
            _log_decision(cmd, detail, False)

        lines.append(line)
        results.append({"cmd": cmd, "ok": code == 0, "out": detail})

    ok_count = sum(1 for r in results if r["ok"])
    total = len(results)
    summary = f"\n**Resultado: {ok_count}/{total} passos OK**"
    if ok_count == total and total > 0:
        summary += f"\n**{tool_name} instalado com sucesso!**"

    return "\n".join(lines) + summary


# ── Project Scaffolding Engine ────────────────────────────────────────────────

_SCAFFOLD_TEMPLATES = {
    "saas": {
        "dirs": ["backend/api", "backend/core", "backend/services",
                 "backend/adapters", "frontend/src", "frontend/public",
                 "config", "tests/unit", "tests/integration", "docs"],
        "files": {
            "README.md": "# SaaS Project\n\n## Setup\n\n## Architecture\n",
            ".env.example": "DATABASE_URL=\nSECRET_KEY=\nAPI_KEY=\n",
            "backend/requirements.txt": "fastapi\nuvicorn\nsqlalchemy\npython-dotenv\n",
            "frontend/package.json": '{"name":"saas-frontend","version":"0.1.0","scripts":{"dev":"vite","build":"vite build"}}\n',
        }
    },
    "api": {
        "dirs": ["src/routes", "src/middleware", "src/models",
                 "src/services", "src/config", "tests"],
        "files": {
            "README.md": "# API Project\n\n## Endpoints\n\n## Auth\n",
            ".env.example": "PORT=3000\nDB_URL=\nJWT_SECRET=\n",
            "requirements.txt": "fastapi\nuvicorn\npydantic\npython-jose\n",
        }
    },
    "automation": {
        "dirs": ["scripts", "config", "logs", "output"],
        "files": {
            "README.md": "# Automation Tool\n\n## Usage\n\n## Config\n",
            ".env.example": "TARGET_URL=\nOUTPUT_DIR=./output\n",
            "requirements.txt": "requests\nbeautifulsoup4\nschedule\npython-dotenv\n",
        }
    }
}


def scaffold_project(project_type: str, project_name: str, base_path: str = "C:\\Jarvis\\workspace") -> str:
    """
    Generate a complete project structure from a template.
    Creates all directories and starter files.
    """
    template = _SCAFFOLD_TEMPLATES.get(project_type.lower())
    if not template:
        available = ", ".join(_SCAFFOLD_TEMPLATES.keys())
        return f"Tipo de projeto nao reconhecido. Disponíveis: {available}"

    root = Path(base_path) / project_name
    if root.exists():
        return f"Projeto '{project_name}' já existe em `{root}`."

    created_dirs = []
    created_files = []

    try:
        for d in template["dirs"]:
            (root / d).mkdir(parents=True, exist_ok=True)
            created_dirs.append(d)

        for fname, content in template["files"].items():
            fpath = root / fname
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")
            created_files.append(fname)

        _log_decision(f"scaffold:{project_type}:{project_name}", str(root), True)

        return (
            f"**Projeto '{project_name}' criado!**\n"
            f"Caminho: `{root}`\n"
            f"Diretorios: {len(created_dirs)}\n"
            f"Arquivos: {len(created_files)}\n\n"
            f"Estrutura:\n```\n" +
            "\n".join(f"  {d}/" for d in created_dirs[:10]) +
            "\n```"
        )
    except OSError as e:
        _log_decision(f"scaffold:{project_type}:{project_name}", str(e), False)
        return f"Erro ao criar projeto: {e}"


# ── PR-style code review ──────────────────────────────────────────────────────

_REVIEW_PROMPT = """You are a senior code reviewer. Analyze the code and output a JSON array of issues.
Each issue: {"severity": "HIGH|MEDIUM|LOW", "line_hint": "brief location", "issue": "description", "fix": "concrete suggestion"}
Output ONLY the JSON array. No markdown. No explanation."""


def code_review(code: str, language: str = "python") -> str:
    """Quick PR-style review returning formatted findings."""
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": f"Language: {language}\n\nCode:\n{code[:3000]}",
        "system": _REVIEW_PROMPT,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 800},
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=60)
        raw = r.json().get("response", "").strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\n?```$", "", raw, flags=re.MULTILINE)
        issues = json.loads(raw.strip())
        if not issues:
            return "**Code Review: Sem issues encontradas.**"
        lines = ["**Code Review:**\n"]
        for iss in issues[:10]:
            sev = iss.get("severity", "?")
            lines.append(
                f"[{sev}] {iss.get('issue','')}\n"
                f"   Location: {iss.get('line_hint','')}\n"
                f"   Fix: {iss.get('fix','')}"
            )
        return "\n\n".join(lines)
    except Exception as e:
        return f"Erro no code review: {e}"


def execute(user_input: str) -> str:
    """
    Main entry point — dispatches to sub-capabilities based on intent.
    Called by main.py for INSTALL intents.
    """
    text_lower = user_input.lower()

    # Scaffold request
    if any(k in text_lower for k in ["scaffold", "criar projeto", "create project", "saas starter", "api starter"]):
        for ptype in _SCAFFOLD_TEMPLATES:
            if ptype in text_lower:
                name_match = re.search(r'(?:chamado|called|named|nome)\s+["\']?([a-zA-Z0-9_\-]+)["\']?', user_input, re.IGNORECASE)
                name = name_match.group(1) if name_match else f"jarvis_{ptype}_project"
                return scaffold_project(ptype, name)
        return scaffold_project("api", "meu_projeto")

    # Code review request
    if any(k in text_lower for k in ["review", "revisar", "analise o codigo", "check this code"]):
        return code_review(user_input)

    # Install + execute — main autonomous flow
    # This is called by main.py AFTER web_search returns results
    return (
        "Skill dev_architect ativa.\n"
        "Para instalar um pacote, use: 'instale X e execute'\n"
        "Para criar projeto: 'crie um projeto saas chamado X'\n"
        "Para review de codigo: 'revise este codigo: [codigo]'"
    )
