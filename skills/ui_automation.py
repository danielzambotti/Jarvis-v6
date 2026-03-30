"""
skills/ui_automation.py — RPA & UI Automation Skill (Phase 4 — fixed)
=======================================================================
PHASE 4 FIXES:
  [BUG-01] _require_pyautogui() called in 4 places but never defined.
    Fix: Added function at module level, above all callers.

CALCULATOR DECISION TREE (Phase 4 directive):
  "open calculator and calculate 15 x 3"
    -> Strategy A (preferred): Pure PowerShell [math]::Round((15*3),6)
       Zero GUI, instant, no window management needed.
    -> Strategy B (GUI demo): open calc.exe + wait 2s + type "15*3=" via PyAutoGUI
       Used when user explicitly asks for calculator GUI or demo.

SAFETY FAIL-SAFES (Phase 4.2):
  - FAILSAFE = True: move mouse to (0,0) to abort any running automation
  - PAUSE = 0.3s between every PyAutoGUI action (prevents runaway input)
  - All actions gated through _require_pyautogui() guard
"""

import logging
import re
import subprocess
import time
from pathlib import Path
from core.metrics import jarvis_skill_failures_total

logger = logging.getLogger(__name__)

# ── Optional dependencies ─────────────────────────────────────────────────────
try:
    import pyautogui
    pyautogui.FAILSAFE = True   # top-left corner (0,0) = emergency abort
    pyautogui.PAUSE    = 0.3    # 0.3s between every action
    _PYAUTOGUI_AVAILABLE = True
    logger.info("[UI] PyAutoGUI loaded (FAILSAFE=ON, PAUSE=0.3s)")
except ImportError:
    _PYAUTOGUI_AVAILABLE = False
    logger.warning("[UI] PyAutoGUI not installed. Run: pip install pyautogui pygetwindow pillow")

try:
    import pygetwindow as gw
    _PYGETWINDOW_AVAILABLE = True
except (NotImplementedError, ImportError) as e:
    gw = None
    _PYGETWINDOW_AVAILABLE = False
    logger.warning("[UI_AUTOMATION] PyGetWindow disabled (Linux/Headless environment): %s", e)


# ── Guard function — MUST be defined before any caller ───────────────────────
def _require_pyautogui() -> bool:
    """
    Returns True if PyAutoGUI is available, False otherwise.
    All automation functions call this first — if False they return early.
    """
    return _PYAUTOGUI_AVAILABLE


# ── Calculator helpers ────────────────────────────────────────────────────────
def _ps_calculate(expression: str) -> str:
    """
    Strategy A: PowerShell arithmetic — zero GUI, instant result.
    Accepts: 15*3, 100/4, 2**8, (5+3)*2, etc.
    Returns result string or descriptive error.
    """
    # Only allow safe math characters
    safe = re.sub(r"[^0-9+\-*/.() \tx]", "", expression).strip()
    safe = safe.replace("x", "*").replace("X", "*")   # handle "15 x 3"
    if not safe:
        return f"Expressao invalida: {expression!r}"
    ps_cmd = f"[math]::Round(({safe}), 6)"
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-Command", ps_cmd],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace"
        )
        result = r.stdout.strip()
        return result if result else f"Erro: {r.stderr.strip()[:200]}"
    except Exception as e:
        return f"Falha no calculo PowerShell: {e}"


def _open_app_ps(app_name: str) -> bool:
    """
    Open an application via os_controller dynamic finder.
    Returns True if launched successfully, False otherwise.
    """
    try:
        from skills.os_controller import _dynamic_find_app, _run_ps
        cmd = _dynamic_find_app(app_name)
        if cmd:
            _run_ps(cmd, timeout=10)
            time.sleep(2.0)   # wait for window to appear
            return True
    except Exception as e:
        logger.error("[UI] Open app error: %s", e)
    return False


def _calc_gui(expression: str) -> str:
    """
    Strategy B: Open calc.exe and type the expression via PyAutoGUI.
    Used when user explicitly wants the GUI calculator (demo mode).
    """
    if not _require_pyautogui():
        return "PyAutoGUI nao disponivel para abrir calculadora GUI."

    # Normalize: "15 x 3" -> "15*3"
    expr_norm = re.sub(r"\s*[xX]\s*", "*", expression).replace(" ", "")

    opened = _open_app_ps("calc")
    if not opened:
        # Try direct PS as fallback
        subprocess.Popen(["calc.exe"])
        time.sleep(2.0)

    try:
        # Type the expression + Enter
        pyautogui.write(expr_norm + "=", interval=0.08)
        time.sleep(0.5)
        # Take screenshot to capture result
        path = take_screenshot("calc_result")
        return (
            f"Calculadora aberta e expressao digitada: {expr_norm}=\n"
            f"Screenshot: `{path}`"
        )
    except Exception as e:
        logger.error("[UI] calc GUI failed: %s", e)
        return f"Erro ao digitar na calculadora: {e}"


# ── Core automation primitives ────────────────────────────────────────────────
def take_screenshot(label: str = "ui_action") -> str:
    """Capture full desktop screenshot. Returns path on success, empty string on failure."""
    if not _require_pyautogui():
        return ""
    ws = Path(__file__).parent.parent / "workspace"
    ws.mkdir(exist_ok=True)
    out = str(ws / f"screenshot_{label}_{int(time.time())}.png")
    try:
        pyautogui.screenshot(out)
        logger.info("[UI] Screenshot saved: %s", out)
        return out
    except Exception as e:
        logger.error("[UI-AUTO] Screenshot failed: %s", e)
        jarvis_skill_failures_total.labels(skill="UI_ACTION").inc()
        return ""


def find_window(title_fragment: str):
    """Find open window by title fragment. Returns window object or None."""
    if not _PYGETWINDOW_AVAILABLE:
        return None
    try:
        wins = gw.getWindowsWithTitle(title_fragment)
        return wins[0] if wins else None
    except Exception:
        return None


def activate_window(title_fragment: str) -> bool:
    """Bring matching window to foreground."""
    win = find_window(title_fragment)
    if not win:
        return False
    try:
        win.activate()
        time.sleep(0.5)
        return True
    except Exception as e:
        logger.error("[UI] Window activate failed: %s", e)
        return False


def click_at(x: int, y: int, clicks: int = 1, button: str = "left") -> bool:
    """Click at screen coordinates."""
    if not _require_pyautogui():
        return False
    try:
        pyautogui.click(x, y, clicks=clicks, button=button)
        return True
    except Exception as e:
        logger.error("[UI] Click failed: %s", e)
        return False


def type_text(text: str, interval: float = 0.05) -> bool:
    """Type text into focused element."""
    if not _require_pyautogui():
        return False
    try:
        pyautogui.write(text, interval=interval)
        return True
    except Exception as e:
        logger.error("[UI] Type failed: %s", e)
        return False


def hotkey(*keys: str) -> bool:
    """Execute a keyboard shortcut combination."""
    if not _require_pyautogui():
        return False
    try:
        pyautogui.hotkey(*keys)
        return True
    except Exception as e:
        logger.error("[UI] Hotkey failed: %s", e)
        return False


def execute(user_input: str) -> str:
    """
    Main entry point — parses NL command and dispatches to correct handler.

    DECISION TREE for calculator requests:
      "calculate 15 x 3" | "quanto e 15*3" | "compute 100/4"
        -> Strategy A: _ps_calculate() — PowerShell, zero GUI, instant
      "abrir calculadora e calcular" | "open calc gui"
        -> Strategy B: _calc_gui() — opens calc.exe + types via PyAutoGUI

    All other patterns:
      screenshot, click at X Y, digitar X, pressionar X+Y, focus/ativar window, abrir app
    """
    text = user_input.lower().strip()

    # ── Calculator: pure compute (Strategy A — preferred) ────────────────────
    calc_pure = re.search(
        r'(?:calcul[ae]|comput[ae]|quanto[e\s]+|result[ao]|evaluate|eval)\s*'
        r'[:\s]*([0-9][0-9+\-*/.() x\t]*)',
        text, re.IGNORECASE
    )
    # Also match patterns like "15 x 3", "15*3", "100/4" directly
    bare_expr = re.match(r'^([0-9][0-9+\-*/.() xX\t]+)$', text.strip())

    if calc_pure or bare_expr:
        expr = (calc_pure.group(1) if calc_pure else bare_expr.group(1)).strip()
        # If user also mentions "calculator" app, use GUI (Strategy B)
        if any(k in text for k in ["calculadora", "calc.exe", "gui", "abrir calc"]):
            return _calc_gui(expr)
        # Otherwise PowerShell is faster and more reliable (Strategy A)
        result = _ps_calculate(expr)
        return f"Resultado: {expr} = {result}"

    # ── Screenshot ───────────────────────────────────────────────────────────
    if "screenshot" in text or "capturar tela" in text or "printscreen" in text:
        path = take_screenshot("manual")
        return f"Screenshot salvo: `{path}`" if path else "Falha ao capturar tela."

    # ── Focus / activate window ───────────────────────────────────────────────
    m = re.search(r'(?:focus|ativar?|activate|janela|window)\s+(.+)', text)
    if m:
        title = m.group(1).strip()
        ok = activate_window(title)
        return f"Janela '{title}' ativada." if ok else f"Janela '{title}' nao encontrada."

    # ── Click at coordinates ──────────────────────────────────────────────────
    m = re.search(r'click\s+(?:at\s+)?(\d+)[,\s]+(\d+)', text)
    if m:
        x, y = int(m.group(1)), int(m.group(2))
        ok = click_at(x, y)
        return f"Clique em ({x}, {y})." if ok else "Falha no clique."

    # ── Type text ────────────────────────────────────────────────────────────
    m = re.search(r'(?:type|digitar?|escrever?|write)\s+(.+)', text)
    if m:
        content = m.group(1).strip()
        ok = type_text(content)
        return f"Digitado: '{content}'" if ok else "Falha ao digitar."

    # ── Hotkey / keyboard shortcut ────────────────────────────────────────────
    m = re.search(r'(?:press|pressionar?|hotkey|atalho|shortcut)\s+(.+)', text)
    if m:
        keys_str = m.group(1).strip()
        keys = [k.strip() for k in re.split(r'[+,\s]+', keys_str) if k.strip()]
        ok = hotkey(*keys)
        return f"Atalho '{'+'.join(keys)}' executado." if ok else "Falha no atalho."

    # ── Open application ──────────────────────────────────────────────────────
    m = re.search(r'(?:open|abrir?|launch|iniciar?|start)\s+(.+)', text)
    if m:
        app_name = m.group(1).strip()
        if not _require_pyautogui():
            # Fallback to os_controller directly
            from skills.os_controller import _dynamic_find_app, _run_ps
            cmd = _dynamic_find_app(app_name)
            if cmd:
                _run_ps(cmd, timeout=10)
                return f"Aplicativo '{app_name}' iniciado (sem screenshot — PyAutoGUI indisponivel)."
            return f"Aplicativo '{app_name}' nao encontrado."

        ok = _open_app_ps(app_name)
        if ok:
            path = take_screenshot(f"open_{app_name.replace(' ','_')}")
            return (f"Aplicativo '{app_name}' aberto.\nScreenshot: `{path}`"
                    if path else f"Aplicativo '{app_name}' aberto.")
        return f"Aplicativo '{app_name}' nao encontrado."

    # ── Fallback ──────────────────────────────────────────────────────────────
    return (
        "Comando de UI nao reconhecido. Exemplos:\n"
        "- 'calcular 15 x 3'        (PowerShell)\n"
        "- 'abrir calc e calcular X' (GUI)\n"
        "- 'screenshot'\n"
        "- 'click at 500 300'\n"
        "- 'digitar hello world'\n"
        "- 'pressionar ctrl+s'\n"
        "- 'abrir notepad'"
    )
