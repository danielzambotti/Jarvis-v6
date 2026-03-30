"""
skills/os_controller.py — Dynamic OS Command Execution Skill (v3)
==================================================================
CHANGELOG v3 (fixes from live failure analysis):

  [BUG-01 FIXED] Hallucinated placeholder paths
    Root cause: system prompt had example paths like C:\\full\\path.exe
    which the model copied literally into output.
    Fix: Removed ALL example paths from prompt. Replaced with abstract
    token NEEDS_SEARCH:<AppName> as the ONLY fallback pattern.

  [BUG-02 FIXED] `start epicgameslauncher` bypassed Phase B probe
    Root cause: Phase B-alt only matched `Start-Process "X"` pattern.
    The `start X` verb form went straight to Phase C and failed.
    Fix: Expand intercept regex to catch `start X` form too, AND
    force NEEDS_SEARCH for any app name that is not a known system binary.

  [BUG-03 FIXED] CREATOR requests leaked into OS_COMMAND
    Root cause: "create a file called X" matched OS_COMMAND in router.
    Fix: Added explicit guard at top of execute() — if input smells like
    file creation, return a redirect message rather than hallucinating PS.

ARCHITECTURE — THREE PHASES:
  Phase A: Ollama translates NL → PowerShell token (never a real path)
  Phase B: Dynamic Windows search resolves the real executable path
  Phase C: Security blacklist + subprocess.run() execution
"""

import logging
import os
import re
import subprocess
import requests
from security import is_safe_command
from config import OLLAMA_URL, OLLAMA_MODEL
from core.metrics import jarvis_skill_failures_total

logger = logging.getLogger(__name__)

_MAX_OUTPUT_CHARS = 3000
_SELF_HEAL_RETRIES = 3   # Phase 3.2: max auto-fix attempts

_HEAL_SYSTEM = """You are a Windows PowerShell debugging expert.
A command failed. Analyze the error and output the FIXED command only.
Output ONE LINE: the corrected PowerShell command. No explanation. No markdown.
If the error is unfixable, output: CANNOT_FIX"""


def _ask_for_fix(original_cmd: str, stderr: str) -> str:
    """Phase 3.2: Ask Ollama to fix a failed command. Returns fixed command or CANNOT_FIX."""
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": f"Failed command: {original_cmd}\nError: {stderr[:500]}\nFixed command:",
        "system": _HEAL_SYSTEM,
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 80},
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=60)
        if r.status_code != 200:
            logger.error("[HEAL] Non-200 response: status=%d body=%s", r.status_code, r.text[:300])
            return "CANNOT_FIX"
        fix = r.json().get("response", "").strip().splitlines()[0].strip()
        logger.info("[HEAL] Suggested fix: %s", fix[:150])
        return fix
    except Exception as e:
        logger.error("[HEAL] Fix request failed: %s", e)
        jarvis_skill_failures_total.labels(skill="OS_COMMAND").inc()
        return "CANNOT_FIX"

# ── Output post-processor: convert raw bytes to human-readable units ──────────
_BYTE_PATTERN = re.compile(r'\b(\d{6,})\b')   # numbers with 6+ digits → likely bytes

def _humanise_bytes(text: str) -> str:
    """
    Replace large raw integers that look like byte counts with MB/GB labels.
    Heuristic: any standalone integer >= 100_000 is treated as bytes.
    Applied to stdout before sending to Telegram — keeps output readable.
    """
    def _fmt(m: re.Match) -> str:
        n = int(m.group(1))
        if n >= 100_000:
            if n >= 1_073_741_824:
                return f"{n / 1_073_741_824:.2f} GB ({n:,} bytes)"
            if n >= 1_048_576:
                return f"{n / 1_048_576:.1f} MB ({n:,} bytes)"
            if n >= 1_024:
                return f"{n / 1_024:.1f} KB ({n:,} bytes)"
        return m.group(1)   # leave small numbers unchanged

    return _BYTE_PATTERN.sub(_fmt, text)

# ── System binaries that are guaranteed to be in PATH — skip probing ──────────
_KNOWN_SYSTEM_BINS = {
    "calc", "notepad", "mspaint", "explorer", "taskmgr", "regedit",
    "cmd", "powershell", "wt", "chrome", "firefox", "msedge",
    "code", "git", "python", "python3", "pip", "node", "npm",
}

# ── Regex to detect file-creation intent that belongs to CREATOR skill ────────
_CREATION_KEYWORDS = re.compile(
    r'\b(cri[ae]|escreve[r]?|gere?|make|write|create|novo arquivo|new file)\b',
    re.IGNORECASE
)
_FILE_EXTENSION = re.compile(r'\.[a-z]{2,4}\b', re.IGNORECASE)


# ── PowerShell probe scripts — use {app_name} placeholder ────────────────────
# Double-braces {{ }} are Python's way of escaping literal { } in f-strings.

_PS_FIND_IN_START_MENU = r"""
$appName = '{app_name}'
# Search UWP / Store apps registered in Get-StartApps
$results = Get-StartApps | Where-Object {{ $_.Name -like "*$appName*" }}
if ($results) {{
    $first = $results[0]
    Write-Output "FOUND_UWP:$($first.AppID)"
}} else {{
    # Search .lnk shortcuts in both per-user and all-users Start Menu
    $lnk = Get-ChildItem -Path @(
        "$env:APPDATA\Microsoft\Windows\Start Menu\Programs",
        "C:\ProgramData\Microsoft\Windows\Start Menu\Programs"
    ) -Recurse -Filter "*.lnk" -ErrorAction SilentlyContinue |
        Where-Object {{ $_.BaseName -like "*$appName*" }} |
        Select-Object -First 1
    if ($lnk) {{
        Write-Output "FOUND_LNK:$($lnk.FullName)"
    }} else {{
        Write-Output "NOT_FOUND"
    }}
}}
"""

# _PS_FIND_IN_PATH_AND_DIRS removed — was causing KeyError: 'env' because
# Python's str.format() misparses ${env:ProgramFiles(x86)} as a placeholder.
# Replaced by _build_search_script() which uses string concatenation.


# ── CRITICAL: No example paths in system prompt — model must use NEEDS_SEARCH ─
_TRANSLATE_SYSTEM_PROMPT = """You are a Windows PowerShell command translator for Jarvis.
Convert the user's request into a SINGLE PowerShell token. Follow these rules exactly:

RULE 1 — SYSTEM UTILITIES (always safe, use directly):
  calc | notepad | mspaint | explorer | taskmgr | regedit | cmd | wt
  → Output exactly the tool name. Example: calc

RULE 2 — WELL-KNOWN PATH APPS (only if you are 100% certain of binary name):
  chrome | firefox | msedge | code | git | python | node
  → Output: Start-Process "<binary_name>"  e.g.  Start-Process "chrome"

RULE 3 — ANY OTHER APP (games, launchers, productivity tools, etc.):
  → You do NOT know the path. Output: NEEDS_SEARCH:<AppName>
  Examples:
    "open epic games"    → NEEDS_SEARCH:Epic Games
    "open steam"         → NEEDS_SEARCH:Steam
    "open spotify"       → NEEDS_SEARCH:Spotify
    "open discord"       → NEEDS_SEARCH:Discord
    "open antigravity"   → NEEDS_SEARCH:Antigravity
  DO NOT invent paths. DO NOT use Start-Process for unknown apps.

RULE 4 — SYSTEM QUERIES (read-only, always safe):
  → Use Get-* cmdlets.
  Examples: Get-Process | Sort-Object CPU -Desc | Select -First 10

RULE 5 — CANNOT EXPRESS SAFELY:
  → Output exactly: UNSAFE_REQUEST

OUTPUT: One line only. No markdown. No explanation. No code fences."""


def _run_ps(script: str, timeout: int = 20) -> str:
    """Run a PowerShell scriptblock and return stripped stdout."""
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace"
        )
        return r.stdout.strip()
    except Exception as e:
        logger.error("[OS_CTRL] PS helper error: %s", e)
        return ""


def _build_search_script(app_name: str) -> str:
    """
    Build the PowerShell search script for a given app name.
    Uses string concatenation instead of .format() to avoid KeyError
    on ${env:ProgramFiles(x86)} which Python's str.format() misparses.
    """
    safe_name = app_name.replace("'", "").replace('"', "").replace("`", "")

    start_menu = (
        "$appName = '" + safe_name + "'\n"
        "$results = Get-StartApps | Where-Object { $_.Name -like \"*$appName*\" }\n"
        "if ($results) {\n"
        "    $first = $results[0]\n"
        "    Write-Output \"FOUND_UWP:$($first.AppID)\"\n"
        "} else {\n"
        "    $lnk = Get-ChildItem -Path @(\n"
        "        \"$env:APPDATA\\Microsoft\\Windows\\Start Menu\\Programs\",\n"
        "        \"C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\"\n"
        "    ) -Recurse -Filter \"*.lnk\" -ErrorAction SilentlyContinue |\n"
        "        Where-Object { $_.BaseName -like \"*$appName*\" } |\n"
        "        Select-Object -First 1\n"
        "    if ($lnk) { Write-Output \"FOUND_LNK:$($lnk.FullName)\" }\n"
        "    else { Write-Output \"NOT_FOUND\" }\n"
        "}"
    )

    path_dirs = (
        "$appName = '" + safe_name + "'\n"
        "$x86 = ${env:ProgramFiles(x86)}\n"
        "$cmd = Get-Command \"$appName.exe\" -ErrorAction SilentlyContinue\n"
        "if ($cmd) { Write-Output \"FOUND_CMD:$($cmd.Source)\" }\n"
        "else {\n"
        "    $dirs = @($env:ProgramFiles, $x86,\n"
        "              \"$env:LOCALAPPDATA\\Programs\",\n"
        "              \"$env:LOCALAPPDATA\") | Where-Object { $_ -and (Test-Path $_) }\n"
        "    $exe = Get-ChildItem -Path $dirs -Recurse -Filter \"*.exe\"\n"
        "              -ErrorAction SilentlyContinue |\n"
        "              Where-Object { $_.BaseName -like \"*$appName*\" } |\n"
        "              Select-Object -First 1\n"
        "    if ($exe) { Write-Output \"FOUND_EXE:$($exe.FullName)\" }\n"
        "    else { Write-Output \"NOT_FOUND\" }\n"
        "}"
    )
    return start_menu, path_dirs


def _extract_app_name_from_command(command: str) -> str | None:
    """
    Extract the raw app name from any launch command pattern.
    Handles: NEEDS_SEARCH:X, Start-Process "X", start X, Invoke-Item "X"
    """
    for pat in [
        r'NEEDS_SEARCH:(.+)',
        r'Start-Process\s+"([^"]+)"',
        r'Start-Process\s+(\S+)',
        r'start\s+(?:""\s+)?"?([^";\s]+)"?',
        r'Invoke-Item\s+"([^"]+)"',
    ]:
        m = re.search(pat, command, re.IGNORECASE)
        if m:
            return m.group(1).strip().strip('"')
    return None


def _dynamic_find_app(app_name: str) -> str | None:
    """
    Phase B: Probe Windows to locate the real executable for app_name.

    Search pipeline (fast → broad):
      1. Get-StartApps   → UWP Store apps + Start Menu .lnk shortcuts
      2. Get-Command     → System PATH lookup
      3. Get-ChildItem   → Deep search in Program Files + AppData

    Returns a ready-to-run PowerShell command string, or None.
    """
    logger.info("[OS_CTRL] Phase B: probing for '%s'", app_name)

    start_menu_script, path_script = _build_search_script(app_name)

    result = _run_ps(start_menu_script, timeout=12)
    if result.startswith("FOUND_UWP:"):
        app_id = result.split(":", 1)[1].strip()
        logger.info("[OS_CTRL] Found UWP: %s", app_id)
        return f'Start-Process "shell:AppsFolder\\{app_id}"'
    if result.startswith("FOUND_LNK:"):
        lnk = result.split(":", 1)[1].strip()
        logger.info("[OS_CTRL] Found .lnk: %s", lnk)
        return f'Invoke-Item "{lnk}"'

    result2 = _run_ps(path_script, timeout=25)
    if result2.startswith("FOUND_CMD:") or result2.startswith("FOUND_EXE:"):
        path = result2.split(":", 1)[1].strip()
        logger.info("[OS_CTRL] Found exe: %s", path)
        return f'Start-Process "{path}"'

    logger.warning("[OS_CTRL] '%s' not found.", app_name)
    return None


def _translate_to_command(natural_language: str) -> str:
    """Phase A: Ollama NL → PowerShell token (no real paths, uses NEEDS_SEARCH)."""
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": f"Request: {natural_language}",
        "system": _TRANSLATE_SYSTEM_PROMPT,
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 80},
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=60)
        if r.status_code != 200:
            logger.error("[OS_CTRL] Non-200 from Ollama: status=%d body=%s",
                         r.status_code, r.text[:300])
            return "UNSAFE_REQUEST"
        cmd = r.json().get("response", "").strip()
        # Strip accidental markdown fences
        cmd = re.sub(r"```[a-z]*\n?", "", cmd).replace("```", "").strip()
        # Take only the first line (model sometimes adds explanation)
        cmd = cmd.splitlines()[0].strip() if cmd else ""
        logger.info("[OS_CTRL] Phase A output: %s", cmd[:200])
        return cmd
    except requests.exceptions.ConnectionError:
        logger.error("[OS_CTRL] Ollama not reachable.")
        jarvis_skill_failures_total.labels(skill="OS_COMMAND").inc()
        return "UNSAFE_REQUEST"
    except Exception as e:
        logger.error("[OS_CTRL] Translation error: %s", e)
        jarvis_skill_failures_total.labels(skill="OS_COMMAND").inc()
        return "UNSAFE_REQUEST"


def execute(user_input: str) -> str:
    """
    Main skill entry point — THREE-PHASE execution.

    GUARD: Redirect file-creation requests to CREATOR skill.
    Phase A: Translate NL → PowerShell token via Ollama.
    Phase B: Resolve NEEDS_SEARCH or suspicious launches via Windows probe.
    Phase C: Security blacklist check + subprocess.run() execution.
    """
    # ── GUARD: Catch leaked CREATOR requests ──────────────────────────────────
    # If the router misfired and sent a file-creation intent here, redirect.
    if _CREATION_KEYWORDS.search(user_input) and _FILE_EXTENSION.search(user_input):
        return (
            "📝 Parece que você quer criar um arquivo. "
            "Use o comando novamente — o Jarvis vai redirecioná-lo para a skill correta."
        )

    # ── Phase A: Translate ────────────────────────────────────────────────────
    command = _translate_to_command(user_input)

    if not command or command.upper() == "UNSAFE_REQUEST":
        return (
            "⚠️ Não consegui traduzir isso em um comando seguro.\n"
            "Tente reformular o pedido."
        )

    # ── Phase B: Resolve unknown apps ─────────────────────────────────────────
    needs_search = command.startswith("NEEDS_SEARCH:")

    # Also intercept bare `start appname` pattern (verb `start` without quotes)
    # which PowerShell cannot resolve for non-PATH apps.
    bare_start = bool(re.match(r'^start\s+\S+$', command, re.IGNORECASE))

    if needs_search or bare_start:
        app_name = _extract_app_name_from_command(command)
        if not app_name:
            return "⚠️ Não consegui extrair o nome do aplicativo do comando gerado."

        found_cmd = _dynamic_find_app(app_name)
        if found_cmd:
            command = found_cmd
        else:
            return (
                f"🔍 Não encontrei **{app_name}** instalado neste computador.\n\n"
                f"Deseja que eu procure em Program Files? "
                f"Responda: *procure {app_name} em Program Files*"
            )

    # Phase B-alt: probe even for Start-Process "UnknownApp" (no path given)
    elif re.match(r'^Start-Process\s+"?[a-zA-Z][^\\/:]{1,40}"?\s*$', command, re.IGNORECASE):
        app_name = _extract_app_name_from_command(command)
        if app_name and app_name.lower() not in _KNOWN_SYSTEM_BINS:
            logger.info("[OS_CTRL] Phase B-alt probe for '%s'", app_name)
            found_cmd = _dynamic_find_app(app_name)
            if found_cmd:
                command = found_cmd

    # ── Phase C: Security + Execute ───────────────────────────────────────────
    if not is_safe_command(command):
        return f"🚫 **Bloqueado pelo filtro de segurança**\n`{command[:200]}`"

    logger.info("[OS_CTRL] Executing: %s", command)
    last_stderr = ""
    current_cmd = command

    for attempt in range(1, _SELF_HEAL_RETRIES + 1):
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive",
                 "-Command", current_cmd],
                capture_output=True, text=True, timeout=60,  # raised: 30→60s for slow commands
                encoding="utf-8", errors="replace",
            )
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()

            # SUCCESS or stdout-only result
            if result.returncode == 0 or (stdout and not stderr):
                parts = [f"Comando: `{current_cmd}`\n"]
                if attempt > 1:
                    parts.insert(0, f"(Auto-corrigido em {attempt} tentativas)\n")
                if stdout:
                    stdout = _humanise_bytes(stdout)
                    parts.append(f"Saida:\n```\n{stdout[:_MAX_OUTPUT_CHARS]}\n```")
                if not stdout and not stderr:
                    parts.append("Executado sem output.")
                return "\n".join(parts)

            # FAILURE — attempt self-healing
            last_stderr = stderr
            if attempt < _SELF_HEAL_RETRIES:
                logger.warning("[HEAL] Attempt %d failed (exit %d). Asking for fix...",
                               attempt, result.returncode)
                fix = _ask_for_fix(current_cmd, stderr)
                if fix == "CANNOT_FIX" or not fix:
                    break
                if not is_safe_command(fix):
                    logger.error("[HEAL] Fixed command blocked by security filter.")
                    break
                current_cmd = fix
            else:
                break

        except subprocess.TimeoutExpired:
            jarvis_skill_failures_total.labels(skill="OS_COMMAND").inc()
            return (
                f"Timeout apos 60s: `{current_cmd}`\n"
                "Comando abortado por seguranca — nenhuma alteracao foi feita."
            )
        except FileNotFoundError:
            jarvis_skill_failures_total.labels(skill="OS_COMMAND").inc()
            return "PowerShell nao encontrado."
        except Exception as e:
            logger.error("[OS_CTRL] Subprocess error: %s", e)
            jarvis_skill_failures_total.labels(skill="OS_COMMAND").inc()
            return f"Erro: {e}"

    # All retries exhausted — report root cause
    return (
        f"Falha apos {_SELF_HEAL_RETRIES} tentativas de auto-correcao.\n"
        f"Comando original: `{command}`\n"
        f"Ultimo erro:\n```\n{last_stderr[:400]}\n```"
    )
