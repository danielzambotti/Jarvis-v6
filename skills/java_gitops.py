"""
skills/java_gitops.py — Java GitOps Architect Skill
====================================================
Persona Hibrida: Jeff Allan (GitOps/gh CLI) + Violetio (Java Enterprise)

PIPELINE quando usuario pede "Crie uma API Java e abra PR":
  1. GENERATE: Ollama qwen3-coder:30b gera codigo Java 21 / Spring Boot 3.2
  2. WRITE:    Salva arquivos em workspace/java/{project_name}/
  3. GIT:      git init, git add, git commit (subprocess, timeout=60s)
  4. PR:       gh pr create --fill (subprocess, timeout=60s)
  5. REPORT:   Retorna sumario com paths, PR URL e proximos passos

SECURITY:
  - Todos os subprocessos tem timeout=60s (padrao do os_controller)
  - Nunca executa comandos sem sanitizacao
  - Workspace isolado em C:\\Jarvis\\workspace\\java\\

RTK DIRECTIVE: todos os comandos externos sao prefixados com 'rtk'
  quando executados pelo Claude Code. Em subprocess.run, usamos os
  binarios diretamente pois estamos dentro do Python runtime.
"""

import logging
import os
import re
import subprocess
import time
from pathlib import Path

import requests

from config import OLLAMA_URL, OLLAMA_MODEL

logger = logging.getLogger(__name__)

_WORKSPACE     = Path(__file__).resolve().parent.parent / "workspace" / "java"
_WORKSPACE.mkdir(parents=True, exist_ok=True)
_GIT_TIMEOUT   = 60   # seconds — consistent with Phase C os_controller fix
_OLLAMA_TIMEOUT = None  # no timeout for 30B code generation


# ── Ollama system prompt — carrega a persona do arquivo .md ──────────────────
_JAVA_GITOPS_SYSTEM = """You are a Senior Enterprise Java Architect combining GitOps discipline
with enterprise Spring Boot 3.2 / Java 21 engineering.

CRITICAL OUTPUT RULES:
1. Generate complete, production-ready Java code only.
2. Include package declarations, all imports, and class bodies.
3. Never use placeholder comments like "// TODO: implement".
4. Every class must compile standalone or with standard Spring Boot deps.
5. Use Java 21 features: records for DTOs, sealed interfaces where appropriate.
6. Always include Liquibase XML migration if a DB entity is involved.
7. Respond in PT-BR for explanations, English for code and commit messages.

OUTPUT FORMAT — use exactly these delimiters for each file:
=== FILE: path/to/FileName.java ===
<complete file content>
=== END FILE ===

Generate ALL files needed for the feature in a single response."""

_LIQUIBASE_HINT = """
Also generate the Liquibase migration XML file:
=== FILE: src/main/resources/db/changelog/{date}-{entity}.xml ===
<databaseChangeLog ...>
  <changeSet id="{date}-01" author="jarvis">
    <!-- table definition -->
    <rollback><dropTable tableName="..."/></rollback>
  </changeSet>
</databaseChangeLog>
=== END FILE ==="""

_JUNIT_HINT = """
Also generate a JUnit 5 unit test:
=== FILE: src/test/java/com/jarvis/{pkg}/{name}Test.java ===
<complete test class with @ExtendWith(MockitoExtension.class)>
=== END FILE ==="""


def _extract_project_name(user_input: str) -> str:
    """Extract a safe project/service name from user input."""
    m = re.search(
        r'(?:projeto|project|servico|service|api|app|sistema|system)\s+'
        r'(?:de\s+|para\s+|chamad[ao]\s+)?["\']?([a-zA-Z0-9_\-]+)["\']?',
        user_input, re.IGNORECASE
    )
    if m:
        return m.group(1).lower().replace("-", "_")
    # Fallback: slug from first meaningful word after verbs
    words = re.sub(r'\b(crie?|create|java|spring|api|boot|uma?|um|a|the|para|for)\b',
                   '', user_input, flags=re.IGNORECASE).split()
    words = [w.lower() for w in words if len(w) > 2]
    return words[0] if words else "jarvis_service"


def _generate_java_code(user_input: str, project_name: str) -> str:
    """Call Ollama to generate Java/Spring Boot code + Liquibase + JUnit."""
    today = time.strftime("%Y%m%d")
    prompt = (
        f"Project: {project_name}\n\n"
        f"Feature request: {user_input}\n\n"
        f"Date for migrations: {today}\n"
        f"{_LIQUIBASE_HINT}\n{_JUNIT_HINT}"
    )
    payload = {
        "model":  OLLAMA_MODEL,
        "prompt": prompt,
        "system": _JAVA_GITOPS_SYSTEM,
        "stream": False,
        "options": {"temperature": 0.15, "num_predict": 8000},
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=_OLLAMA_TIMEOUT)
        r.raise_for_status()
        return r.json().get("response", "").strip()
    except requests.exceptions.ConnectionError:
        return ""
    except Exception as e:
        logger.error("[JAVA_GITOPS] Ollama error: %s", e)
        return ""


def _parse_files(raw_output: str) -> list[tuple[str, str]]:
    """
    Parse delimited file blocks from Ollama output.
    Returns list of (relative_path, content) tuples.
    """
    files = []
    pattern = re.compile(
        r'=== FILE: (.+?) ===\n(.*?)=== END FILE ===',
        re.DOTALL
    )
    for m in pattern.finditer(raw_output):
        path    = m.group(1).strip()
        content = m.group(2).strip()
        if path and content:
            files.append((path, content))
    return files


def _write_project_files(project_dir: Path,
                          files: list[tuple[str, str]]) -> list[str]:
    """Write parsed files to workspace. Returns list of written paths."""
    written = []
    for rel_path, content in files:
        target = project_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(content, encoding="utf-8")
            written.append(str(target.relative_to(_WORKSPACE)))
            logger.info("[JAVA_GITOPS] Wrote: %s", rel_path)
        except OSError as e:
            logger.error("[JAVA_GITOPS] Write error %s: %s", rel_path, e)
    return written


def _run_cmd(cmd: list[str], cwd: str) -> tuple[bool, str]:
    """
    Run a shell command with 60s timeout (same as Phase C os_controller).
    Returns (success: bool, output: str).
    """
    try:
        r = subprocess.run(
            cmd, cwd=cwd,
            capture_output=True, text=True,
            timeout=_GIT_TIMEOUT,           # 60s — prevents hanging PRs
            encoding="utf-8", errors="replace"
        )
        out = (r.stdout + r.stderr).strip()
        return r.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, f"Timeout apos {_GIT_TIMEOUT}s: {' '.join(cmd)}"
    except FileNotFoundError as e:
        return False, f"Comando nao encontrado: {e}"
    except Exception as e:
        return False, f"Erro: {e}"


def _git_commit_and_pr(project_dir: Path, project_name: str,
                        user_input: str) -> tuple[bool, str]:
    """
    Steps 7-8 of the mandatory pipeline:
      git init (if needed) -> git add -> git commit -> gh pr create
    Returns (success, message).
    """
    cwd = str(project_dir)
    steps = []

    # git init (idempotent — safe to run even if already a repo)
    ok, out = _run_cmd(["git", "init"], cwd)
    steps.append(f"git init: {'OK' if ok else 'WARN: ' + out[:80]}")

    # git add all generated files
    ok, out = _run_cmd(["git", "add", "."], cwd)
    if not ok:
        return False, f"Falha em git add: {out[:200]}"
    steps.append("git add .: OK")

    # Semantic commit message
    feature_hint = user_input[:60].replace('"', '').replace("'", "")
    commit_msg   = f"feat({project_name}): {feature_hint}"
    ok, out = _run_cmd(["git", "commit", "-m", commit_msg], cwd)
    if not ok and "nothing to commit" not in out:
        return False, f"Falha em git commit: {out[:200]}"
    steps.append(f"git commit: OK ({commit_msg[:50]})")

    # gh pr create — autonomous PR with --fill
    pr_title = f"feat({project_name}): {feature_hint[:50]}"
    ok, out   = _run_cmd(
        ["gh", "pr", "create",
         "--title", pr_title,
         "--body",  f"Auto-generated by Jarvis Java GitOps\n\n**Feature:** {user_input[:200]}",
         "--fill"],
        cwd
    )
    if ok:
        pr_url = out.strip().splitlines()[-1] if out else "(URL nao disponivel)"
        steps.append(f"gh pr create: OK -> {pr_url}")
    else:
        steps.append(f"gh pr create: WARN (repositorio remoto pode ser necessario) -> {out[:150]}")

    return True, "\n".join(steps)


def execute(user_input: str) -> str:
    """
    Main entry point — full Java GitOps pipeline:
      Generate -> Write -> Git Commit -> GitHub PR -> Report
    """
    project_name = _extract_project_name(user_input)
    project_dir  = _WORKSPACE / project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    logger.info("[JAVA_GITOPS] Starting pipeline for project: %s", project_name)

    # Step 1-2: Generate + Write
    raw = _generate_java_code(user_input, project_name)
    if not raw:
        return (
            "Ollama nao retornou codigo. Verifique se o servico esta rodando.\n"
            f"Modelo configurado: `{OLLAMA_MODEL}`"
        )

    files = _parse_files(raw)
    if not files:
        # No delimited blocks — return raw as is (model formatted differently)
        return (
            f"Codigo gerado (formato livre — salve manualmente):\n\n"
            f"```\n{raw[:3000]}\n```"
        )

    written = _write_project_files(project_dir, files)

    # Steps 7-8: Git + PR
    git_ok, git_msg = _git_commit_and_pr(project_dir, project_name, user_input)

    # Final report
    files_list = "\n".join(f"  - `{f}`" for f in written)
    return (
        f"Pipeline Java GitOps concluido!\n\n"
        f"**Projeto:** `{project_name}`\n"
        f"**Diretorio:** `{project_dir}`\n"
        f"**Arquivos gerados ({len(written)}):**\n{files_list}\n\n"
        f"**Git + PR:**\n{git_msg}\n\n"
        f"**Proximos passos:**\n"
        f"- `rtk mvn clean test` para rodar os testes\n"
        f"- `rtk mvn jacoco:report` para cobertura\n"
        f"- Revise o PR no GitHub antes de fazer merge"
    )
