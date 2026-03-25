"""
ui/app.py — Jarvis v6.0 Command Center
=======================================
Flet Web Dashboard served on port 8501.

Views:
  💬 Chat       Real-time conversation via POST /api/v1/execute
  📁 Workspace  File tree + viewer from /app/workspace
  🛡️ Security   Grafana embed + live security event counters
  🐙 GitOps     Recent commits + PR status from /api/v1/execute

Config (env vars):
  JARVIS_API_URL    FastAPI backend  (default: http://localhost:8765)
  JARVIS_UI_TOKEN   Bearer JWT token (default: "")
  GRAFANA_URL       Grafana base URL (default: http://localhost:3000)

Usage:
  flet run ui/app.py --web --port 8501 --host 0.0.0.0
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path

import flet as ft

try:
    import aiohttp
    _AIOHTTP_OK = True
except ImportError:
    _AIOHTTP_OK = False

# ── Config ─────────────────────────────────────────────────────────────────────

JARVIS_API   = os.environ.get("JARVIS_API_URL",  "http://localhost:8765")
JARVIS_TOKEN = os.environ.get("JARVIS_UI_TOKEN", "")
GRAFANA_URL  = os.environ.get("GRAFANA_URL",     "http://localhost:3000")
WORKSPACE    = Path(os.environ.get("WORKSPACE_PATH", "/app/workspace"))

# ── Design tokens ──────────────────────────────────────────────────────────────

_BG        = "#0D1117"
_BG_CARD   = "#161B22"
_BG_INPUT  = "#21262D"
_BORDER    = "#30363D"
_ACCENT    = "#58A6FF"
_SUCCESS   = "#3FB950"
_WARN      = "#D29922"
_DANGER    = "#F85149"
_TEXT      = "#C9D1D9"
_DIM       = "#8B949E"
_PURPLE    = "#BC8CFF"


# ── HTTP helpers ───────────────────────────────────────────────────────────────

async def _get(path: str) -> dict:
    """GET request to Jarvis API. Returns parsed JSON or error dict."""
    url = f"{JARVIS_API}{path}"
    headers = {"Authorization": f"Bearer {JARVIS_TOKEN}"} if JARVIS_TOKEN else {}
    try:
        if _AIOHTTP_OK:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    return await r.json()
        else:
            import urllib.request, json
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read())
    except Exception as exc:
        return {"error": str(exc)}


async def _post(path: str, payload: dict) -> dict:
    """POST JSON to Jarvis API. Returns parsed JSON or error dict."""
    url = f"{JARVIS_API}{path}"
    headers = {"Content-Type": "application/json"}
    if JARVIS_TOKEN:
        headers["Authorization"] = f"Bearer {JARVIS_TOKEN}"
    try:
        if _AIOHTTP_OK:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json=payload, headers=headers,
                                  timeout=aiohttp.ClientTimeout(total=120)) as r:
                    return await r.json()
        else:
            import urllib.request, json
            data = json.dumps(payload).encode()
            req  = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
    except Exception as exc:
        return {"error": str(exc)}


# ── Shared widget factories ────────────────────────────────────────────────────

def _card(content: ft.Control, padding: int = 16) -> ft.Container:
    return ft.Container(
        content=content,
        bgcolor=_BG_CARD,
        border=ft.border.all(1, _BORDER),
        border_radius=8,
        padding=padding,
    )


def _badge(label: str, color: str) -> ft.Container:
    return ft.Container(
        content=ft.Text(label, size=11, weight=ft.FontWeight.W_600, color=color),
        bgcolor=f"{color}22",
        border=ft.border.all(1, f"{color}66"),
        border_radius=4,
        padding=ft.padding.symmetric(horizontal=8, vertical=2),
    )


def _section_title(text: str) -> ft.Text:
    return ft.Text(text, size=12, weight=ft.FontWeight.W_600,
                   color=_DIM, font_family="monospace")


# ── View: Chat ─────────────────────────────────────────────────────────────────

def build_chat_view(page: ft.Page) -> ft.Control:
    """Real-time chat connected to POST /api/v1/execute."""

    messages = ft.ListView(
        expand=True,
        spacing=8,
        auto_scroll=True,
    )
    input_field = ft.TextField(
        hint_text="Send a message to Jarvis...",
        hint_style=ft.TextStyle(color=_DIM),
        bgcolor=_BG_INPUT,
        border_color=_BORDER,
        focused_border_color=_ACCENT,
        color=_TEXT,
        text_size=14,
        expand=True,
        multiline=False,
        on_submit=lambda e: asyncio.create_task(_send(e)),
    )
    send_btn = ft.IconButton(
        icon=ft.Icons.SEND_ROUNDED,
        icon_color=_ACCENT,
        tooltip="Send",
        on_click=lambda e: asyncio.create_task(_send(e)),
    )
    status_row = ft.Row([
        ft.Icon(ft.Icons.CIRCLE, color=_SUCCESS, size=8),
        ft.Text("Connected to Jarvis API", size=11, color=_DIM),
    ], spacing=6)

    def _bubble(text: str, role: str) -> ft.Container:
        is_user = role == "user"
        return ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text(
                        "You" if is_user else "Jarvis",
                        size=11, weight=ft.FontWeight.W_700,
                        color=_ACCENT if is_user else _PURPLE,
                    ),
                    ft.Text(
                        datetime.now().strftime("%H:%M"),
                        size=10, color=_DIM,
                    ),
                ], spacing=8),
                ft.Text(
                    text, size=13, color=_TEXT,
                    selectable=True,
                    no_wrap=False,
                ),
            ], spacing=4, tight=True),
            bgcolor=_BG_INPUT if is_user else _BG_CARD,
            border=ft.border.all(1, _BORDER),
            border_radius=ft.border_radius.only(
                top_left=8, top_right=8,
                bottom_right=0 if is_user else 8,
                bottom_left=8 if is_user else 0,
            ),
            padding=12,
            margin=ft.margin.only(
                left=80 if is_user else 0,
                right=0 if is_user else 80,
            ),
        )

    async def _send(e) -> None:
        text = input_field.value.strip()
        if not text:
            return
        input_field.value = ""
        send_btn.disabled = True
        page.update()

        messages.controls.append(_bubble(text, "user"))
        page.update()

        thinking = ft.Container(
            content=ft.Row([
                ft.ProgressRing(width=14, height=14, stroke_width=2, color=_PURPLE),
                ft.Text("Jarvis is thinking...", size=12, color=_DIM, italic=True),
            ], spacing=8),
            margin=ft.margin.only(top=4),
        )
        messages.controls.append(thinking)
        page.update()

        result = await _post("/api/v1/execute", {
            "message": text, "user_id": "ui_operator"
        })
        messages.controls.remove(thinking)

        reply = result.get("result") or result.get("response") or result.get("error", "No response")
        messages.controls.append(_bubble(str(reply), "assistant"))
        send_btn.disabled = False
        page.update()

    return ft.Column([
        ft.Row([
            ft.Text("Command Interface", size=16,
                    weight=ft.FontWeight.W_700, color=_TEXT),
            ft.Container(expand=True),
            status_row,
        ]),
        ft.Divider(height=1, color=_BORDER),
        ft.Container(
            content=messages,
            expand=True,
            bgcolor=_BG,
            border=ft.border.all(1, _BORDER),
            border_radius=8,
            padding=12,
        ),
        ft.Row([input_field, send_btn], spacing=8),
    ], expand=True, spacing=12)


# ── View: Workspace ────────────────────────────────────────────────────────────

def build_workspace_view(page: ft.Page) -> ft.Control:
    """File tree from /app/workspace with inline text preview."""

    file_content = ft.Text(
        "Select a file to preview.",
        size=13, color=_DIM,
        selectable=True,
        font_family="monospace",
        no_wrap=False,
    )
    content_header = ft.Text("", size=12, color=_ACCENT, font_family="monospace")
    file_list = ft.ListView(spacing=2, expand=True)

    def _load_workspace():
        file_list.controls.clear()
        ws = WORKSPACE if WORKSPACE.exists() else (Path(__file__).parents[1] / "workspace")
        if not ws.exists():
            file_list.controls.append(
                ft.Text(f"Workspace not found: {ws}", size=12, color=_WARN)
            )
            page.update()
            return

        files = sorted(ws.rglob("*"))
        if not files:
            file_list.controls.append(
                ft.Text("Workspace is empty.", size=12, color=_DIM)
            )
            page.update()
            return

        for f in files:
            rel = f.relative_to(ws)
            indent = len(rel.parts) - 1
            icon = ft.Icons.FOLDER_OUTLINED if f.is_dir() else ft.Icons.DESCRIPTION_OUTLINED
            icon_color = _WARN if f.is_dir() else _ACCENT

            def _make_click(fp=f):
                def _on_click(e):
                    if fp.is_file():
                        content_header.value = str(fp.relative_to(ws))
                        try:
                            raw = fp.read_text(encoding="utf-8", errors="replace")
                            file_content.value = raw[:8000] + ("\n…[truncated]" if len(raw) > 8000 else "")
                        except OSError as err:
                            file_content.value = f"Error reading file: {err}"
                        page.update()
                return _on_click

            file_list.controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Container(width=indent * 16),
                        ft.Icon(icon, size=14, color=icon_color),
                        ft.Text(f.name, size=13, color=_TEXT),
                        ft.Container(expand=True),
                        ft.Text(
                            f"{f.stat().st_size:,} B" if f.is_file() else "",
                            size=11, color=_DIM,
                        ),
                    ], spacing=6),
                    padding=ft.padding.symmetric(horizontal=8, vertical=4),
                    border_radius=4,
                    ink=True,
                    on_click=_make_click(),
                )
            )
        page.update()

    refresh_btn = ft.IconButton(
        icon=ft.Icons.REFRESH_ROUNDED,
        icon_color=_ACCENT,
        tooltip="Refresh",
        on_click=lambda e: _load_workspace(),
    )

    # Load on first render
    asyncio.get_event_loop().call_soon(_load_workspace)

    return ft.Column([
        ft.Row([
            ft.Text("Workspace", size=16, weight=ft.FontWeight.W_700, color=_TEXT),
            ft.Container(expand=True),
            refresh_btn,
        ]),
        ft.Divider(height=1, color=_BORDER),
        ft.Row([
            # File tree pane
            _card(
                ft.Column([
                    _section_title("FILES"),
                    ft.Container(height=8),
                    file_list,
                ], expand=True, spacing=0),
                padding=8,
            ),
            # Preview pane
            _card(
                ft.Column([
                    _section_title("PREVIEW"),
                    ft.Container(height=4),
                    ft.Text(content_header.value or "No file selected",
                            ref=ft.Ref[ft.Text](), size=11, color=_ACCENT,
                            font_family="monospace"),
                    ft.Divider(height=1, color=_BORDER),
                    ft.Container(
                        content=ft.Column([file_content], scroll=ft.ScrollMode.AUTO),
                        expand=True,
                    ),
                ], expand=True, spacing=4),
                padding=12,
            ),
        ], expand=True, spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
    ], expand=True, spacing=12)


# ── View: Security ─────────────────────────────────────────────────────────────

def build_security_view(page: ft.Page) -> ft.Control:
    """Security status panel + Grafana dashboard link."""

    metrics_col = ft.Column(spacing=8)
    last_updated = ft.Text("", size=11, color=_DIM)

    async def _refresh_metrics():
        data = await _get("/health")
        metrics_col.controls.clear()

        status = data.get("status", "unknown")
        res    = data.get("resources", {})
        color  = _SUCCESS if status == "healthy" else _DANGER

        metrics_col.controls += [
            ft.Row([
                _badge(status.upper(), color),
                ft.Container(expand=True),
                ft.Text(f"v{data.get('version','?')}", size=11, color=_DIM),
            ]),
            ft.Container(height=4),
            _card(ft.Column([
                _section_title("SYSTEM RESOURCES"),
                ft.Container(height=8),
                *[
                    ft.Column([
                        ft.Row([
                            ft.Text(label, size=12, color=_DIM, expand=True),
                            ft.Text(f"{val:.1f}%", size=12, color=_text_color(val)),
                        ]),
                        ft.ProgressBar(
                            value=val / 100,
                            bgcolor=_BG_INPUT,
                            color=_text_color(val),
                            height=4,
                            border_radius=2,
                        ),
                        ft.Container(height=4),
                    ], spacing=4)
                    for label, val in [
                        ("CPU", res.get("cpu_percent", 0)),
                        ("RAM", res.get("ram_percent", 0)),
                        ("Disk", res.get("disk_percent", 0)),
                    ]
                ],
            ], spacing=0)),
            ft.Container(height=8),
            _card(ft.Column([
                _section_title("SECURITY PIPELINE"),
                ft.Container(height=8),
                *[
                    ft.Row([
                        ft.Icon(ft.Icons.SHIELD_OUTLINED, size=14, color=_SUCCESS),
                        ft.Text(label, size=13, color=_TEXT, expand=True),
                        _badge("ACTIVE", _SUCCESS),
                    ])
                    for label in [
                        "Guardrails (ADR-005)",
                        "DLP Engine (ADR-004)",
                        "Sandbox (ADR-002)",
                        "SDLC Gate (ADR-010)",
                        "Threat Modeler (ADR-011)",
                        "Doc Processor (ADR-009)",
                    ]
                ],
            ], spacing=8)),
        ]
        last_updated.value = f"Updated {datetime.now().strftime('%H:%M:%S')}"
        page.update()

    refresh_btn = ft.IconButton(
        icon=ft.Icons.REFRESH_ROUNDED,
        icon_color=_ACCENT,
        tooltip="Refresh metrics",
        on_click=lambda e: asyncio.create_task(_refresh_metrics()),
    )

    grafana_btn = ft.ElevatedButton(
        text="Open Grafana Dashboard",
        icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
        color=_ACCENT,
        bgcolor=f"{_ACCENT}22",
        style=ft.ButtonStyle(
            side=ft.BorderSide(1, _ACCENT),
            shape=ft.RoundedRectangleBorder(radius=6),
        ),
        url=f"{GRAFANA_URL}/d/jarvis-main-v1",
    )

    # Initial load
    asyncio.get_event_loop().call_soon(lambda: asyncio.create_task(_refresh_metrics()))

    return ft.Column([
        ft.Row([
            ft.Text("Security & Observability", size=16,
                    weight=ft.FontWeight.W_700, color=_TEXT),
            ft.Container(expand=True),
            last_updated,
            refresh_btn,
        ]),
        ft.Divider(height=1, color=_BORDER),
        ft.Row([
            ft.Column([metrics_col], expand=True, scroll=ft.ScrollMode.AUTO),
            ft.Column([
                _card(ft.Column([
                    _section_title("GRAFANA OBSERVABILITY"),
                    ft.Container(height=8),
                    ft.Text(
                        "Live metrics: requests/s, security blocks, DLP redactions, p95 latency.",
                        size=12, color=_DIM,
                    ),
                    ft.Container(height=12),
                    grafana_btn,
                    ft.Container(height=8),
                    ft.Text(
                        f"Direct: {GRAFANA_URL}",
                        size=11, color=_DIM, font_family="monospace",
                    ),
                ], spacing=4)),
                ft.Container(height=12),
                _card(ft.Column([
                    _section_title("ADR KNOWLEDGE VAULT"),
                    ft.Container(height=8),
                    *[
                        ft.Row([
                            ft.Text(n, size=12, color=_ACCENT, font_family="monospace"),
                            ft.Text(t, size=12, color=_DIM, expand=True),
                        ], spacing=8)
                        for n, t in [
                            ("ADR-002", "Zero-Trust Sandbox"),
                            ("ADR-004", "DLP Engine"),
                            ("ADR-005", "Guardrails"),
                            ("ADR-007", "Autonomous GitOps"),
                            ("ADR-008", "Observability"),
                            ("ADR-009", "Doc Security"),
                            ("ADR-010", "Secure SDLC"),
                            ("ADR-011", "Threat Modeling"),
                            ("ADR-012", "Backup Recovery"),
                        ]
                    ],
                ], spacing=6)),
            ], width=320, spacing=0),
        ], expand=True, spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
    ], expand=True, spacing=12)


# ── View: GitOps ───────────────────────────────────────────────────────────────

def build_gitops_view(page: ft.Page) -> ft.Control:
    """GitOps status: last factory run summary + manual factory trigger."""

    output_area = ft.Text(
        "No factory run yet this session.",
        size=12, color=_DIM,
        selectable=True,
        font_family="monospace",
        no_wrap=False,
    )
    factory_input = ft.TextField(
        hint_text="Describe a software feature to build...",
        hint_style=ft.TextStyle(color=_DIM),
        bgcolor=_BG_INPUT,
        border_color=_BORDER,
        focused_border_color=_ACCENT,
        color=_TEXT,
        text_size=13,
        expand=True,
        multiline=True,
        min_lines=3,
        max_lines=6,
    )
    run_btn = ft.ElevatedButton(
        text="Run Software Factory",
        icon=ft.Icons.PRECISION_MANUFACTURING_ROUNDED,
        color=_BG,
        bgcolor=_ACCENT,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6)),
        on_click=lambda e: asyncio.create_task(_run_factory(e)),
    )
    status_badge = ft.Row([])

    async def _run_factory(e):
        text = factory_input.value.strip()
        if not text:
            return
        run_btn.disabled = True
        output_area.value = "Initiating pipeline...\n(Stage 0: Threat Model → Architect → Developer → QA → Sandbox → SDLC → Guardian → GitOps)"
        output_area.color = _DIM
        status_badge.controls = [
            ft.ProgressRing(width=14, height=14, stroke_width=2, color=_ACCENT),
            ft.Text("Running...", size=12, color=_DIM),
        ]
        page.update()

        result = await _post("/api/v1/execute", {
            "message": f"FACTORY: {text}", "user_id": "ui_operator"
        })

        reply = result.get("result") or result.get("response") or result.get("error", "No response")
        output_area.value = str(reply)
        output_area.color = _TEXT

        ok = "error" not in result
        status_badge.controls = [
            ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE if ok else ft.Icons.ERROR_OUTLINE,
                    size=14, color=_SUCCESS if ok else _DANGER),
            ft.Text("Complete" if ok else "Error", size=12,
                    color=_SUCCESS if ok else _DANGER),
        ]
        run_btn.disabled = False
        page.update()

    pipeline_steps = [
        ("Stage 0", "Threat Modeler", "STRIDE analysis before code generation", _PURPLE),
        ("Stage 1", "Architect",      "Blueprint & design patterns",            _ACCENT),
        ("Stage 2", "Developer",      "Code generation with security directives", _ACCENT),
        ("Stage 3", "Test Master",    "Tests for every STRIDE threat",          _ACCENT),
        ("Stage 3.5", "Sandbox",      "Zero-Trust Docker execution",            _WARN),
        ("Stage 3.7", "SDLC Gate",    "Bandit + Safety + SBOM + pytest",        _WARN),
        ("Stage 4",   "Guardian",     "Senior code review + seal of approval",  _SUCCESS),
        ("Stage 5",   "GitOps",       "Draft PR on GitHub",                     _SUCCESS),
    ]

    return ft.Column([
        ft.Row([
            ft.Text("GitOps & Software Factory", size=16,
                    weight=ft.FontWeight.W_700, color=_TEXT),
        ]),
        ft.Divider(height=1, color=_BORDER),
        ft.Row([
            ft.Column([
                _card(ft.Column([
                    _section_title("FACTORY TRIGGER"),
                    ft.Container(height=8),
                    factory_input,
                    ft.Container(height=8),
                    ft.Row([run_btn, ft.Container(expand=True), *status_badge.controls]),
                ], spacing=4)),
                ft.Container(height=12),
                _card(
                    ft.Column([
                        _section_title("LAST OUTPUT"),
                        ft.Container(height=8),
                        ft.Container(
                            content=ft.Column([output_area], scroll=ft.ScrollMode.AUTO),
                            height=280,
                        ),
                    ], spacing=4),
                ),
            ], expand=True, spacing=0),
            ft.Column([
                _card(ft.Column([
                    _section_title("PIPELINE ARCHITECTURE"),
                    ft.Container(height=8),
                    *[
                        ft.Container(
                            content=ft.Row([
                                ft.Container(
                                    content=ft.Text(stage, size=10,
                                                    color=_BG, font_family="monospace",
                                                    weight=ft.FontWeight.W_700),
                                    bgcolor=color,
                                    border_radius=3,
                                    padding=ft.padding.symmetric(horizontal=6, vertical=2),
                                    width=68,
                                ),
                                ft.Column([
                                    ft.Text(name, size=12, color=_TEXT,
                                            weight=ft.FontWeight.W_600),
                                    ft.Text(desc, size=11, color=_DIM),
                                ], spacing=1, tight=True, expand=True),
                            ], spacing=10),
                            margin=ft.margin.only(bottom=6),
                        )
                        for stage, name, desc, color in pipeline_steps
                    ],
                ], spacing=0)),
            ], width=340, spacing=0),
        ], expand=True, spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
    ], expand=True, spacing=12)


# ── Health color helper ────────────────────────────────────────────────────────

def _text_color(pct: float) -> str:
    if pct > 85: return _DANGER
    if pct > 65: return _WARN
    return _SUCCESS


# ── Topbar status widget (polled every 30 s) ───────────────────────────────────

async def _status_poller(dot: ft.Icon, label: ft.Text, page: ft.Page) -> None:
    while True:
        data = await _get("/health")
        ok   = data.get("status") == "healthy"
        dot.color  = _SUCCESS if ok else (_WARN if "degraded" in str(data.get("status")) else _DANGER)
        label.color = dot.color
        label.value = ("HEALTHY" if ok else data.get("status", "OFFLINE").upper())
        try:
            page.update()
        except Exception:
            break
        await asyncio.sleep(30)


# ── Main entry point ───────────────────────────────────────────────────────────

async def main(page: ft.Page) -> None:
    page.title          = "Jarvis v6.0 — Command Center"
    page.theme_mode     = ft.ThemeMode.DARK
    page.bgcolor        = _BG
    page.padding        = 0
    page.window.width   = 1280
    page.window.height  = 800
    page.fonts          = {"monospace": "RobotoMono"}

    # ── Topbar ────────────────────────────────────────────────────────────
    status_dot   = ft.Icon(ft.Icons.CIRCLE, color=_DIM, size=10)
    status_label = ft.Text("CONNECTING...", size=11, weight=ft.FontWeight.W_600, color=_DIM)

    topbar = ft.Container(
        content=ft.Row([
            ft.Row([
                ft.Icon(ft.Icons.SMART_TOY_ROUNDED, color=_ACCENT, size=20),
                ft.Text("JARVIS", size=16, weight=ft.FontWeight.W_800,
                        color=_ACCENT, font_family="monospace"),
                ft.Text("v6.0", size=11, color=_DIM),
                ft.Text("Command Center", size=13, color=_TEXT),
            ], spacing=8),
            ft.Container(expand=True),
            ft.Row([
                status_dot,
                status_label,
                ft.Container(width=16),
                ft.Text(f"API: {JARVIS_API}", size=10, color=_DIM, font_family="monospace"),
            ], spacing=4),
        ]),
        bgcolor=_BG_CARD,
        border=ft.border.only(bottom=ft.BorderSide(1, _BORDER)),
        padding=ft.padding.symmetric(horizontal=20, vertical=12),
        height=52,
    )

    # ── Navigation rail ───────────────────────────────────────────────────
    NAV_ITEMS = [
        ("💬", "Chat"),
        ("📁", "Workspace"),
        ("🛡️", "Security"),
        ("🐙", "GitOps"),
    ]

    content_area = ft.Column(expand=True)

    def _build_view(idx: int) -> None:
        content_area.controls.clear()
        builders = [
            build_chat_view,
            build_workspace_view,
            build_security_view,
            build_gitops_view,
        ]
        content_area.controls.append(builders[idx](page))
        page.update()

    nav_rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=72,
        bgcolor=_BG_CARD,
        indicator_color=f"{_ACCENT}33",
        indicator_shape=ft.RoundedRectangleBorder(radius=6),
        destinations=[
            ft.NavigationRailDestination(
                icon=ft.Text(icon, size=18),
                selected_icon=ft.Text(icon, size=18),
                label=label,
                padding=ft.padding.symmetric(vertical=4),
            )
            for icon, label in NAV_ITEMS
        ],
        on_change=lambda e: _build_view(e.control.selected_index),
    )

    # ── Layout ────────────────────────────────────────────────────────────
    body = ft.Row([
        ft.Container(
            content=nav_rail,
            bgcolor=_BG_CARD,
            border=ft.border.only(right=ft.BorderSide(1, _BORDER)),
        ),
        ft.Container(
            content=content_area,
            expand=True,
            padding=20,
            bgcolor=_BG,
        ),
    ], expand=True, spacing=0)

    page.add(
        ft.Column([
            topbar,
            ft.Container(content=body, expand=True),
        ], spacing=0, expand=True)
    )

    # Initial view + background status polling
    _build_view(0)
    asyncio.create_task(_status_poller(status_dot, status_label, page))


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ft.app(
        target=main,
        port=8501,
        host="0.0.0.0",
    )
