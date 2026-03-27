"""
web_ui/app.py — Jarvis Real-Time Web Dashboard & REST API
"""
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Set

import psutil
from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.security.iam import UserPrincipal, require_role

# ── Importações do Cérebro (Configuração de Path) ─────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))
from router import route, INTENT_MEMORY_VAULT, _DEBUG_ROUTING
from skills import (
    conversational, os_controller, web_search, creator, fs_manager,
    backup_manager, inspector, github_search, refactor, java_gitops,
    system_status, jarvis_health,
    memory_vault, tech_lead, software_factory, ui_automation,
)
from core.metrics import jarvis_requests_total

logger = logging.getLogger(__name__)

# ── Inicialização ÚNICA do FastAPI ───────────────────────────────────────────
app = FastAPI(title="Jarvis v6.0 Enterprise", version="6.0")

# ── Static files ──────────────────────────────────────────────────────────────
_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# ── WebSocket connection pool ─────────────────────────────────────────────────
_ws_clients: Set[WebSocket] = set()

async def broadcast(event_type: str, data: dict) -> None:
    if not _ws_clients: return
    payload = json.dumps({"type": event_type, "ts": time.time(), **data})
    dead = set()
    for ws in list(_ws_clients):
        try: await ws.send_text(payload)
        except: dead.add(ws)
    _ws_clients.difference_update(dead)

@app.websocket("/ws/stream")
async def ws_stream(websocket: WebSocket):
    await websocket.accept()
    _ws_clients.add(websocket)
    try:
        await websocket.send_text(json.dumps({"type": "connected", "message": "Jarvis Stream Active"}))
        while True:
            await asyncio.sleep(30)
            await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect: pass
    finally: _ws_clients.discard(websocket)

# ── API REST Endpoints ────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html_path = _STATIC_DIR / "index.html"
    if html_path.exists(): return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Dashboard Jarvis (Static index.html não encontrado)</h1>")

# ── Health Check Endpoint (Docker/K8s Ready) ─────────────────────────────────
@app.get("/health")
async def health_check():
    """
    Kubernetes/Docker health check endpoint.
    Returns 200 if system is operational, 503 if degraded.
    """
    try:
        # Check system resources
        cpu_percent = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        
        # Health status calculation
        is_healthy = (
            cpu_percent < 95 and
            ram.percent < 95 and
            disk.percent < 95
        )
        
        status = "healthy" if is_healthy else "degraded"
        http_code = 200 if is_healthy else 503
        
        return JSONResponse(
            status_code=http_code,
            content={
                "status": status,
                "version": "6.0",
                "timestamp": time.time(),
                "resources": {
                    "cpu_percent": cpu_percent,
                    "ram_percent": ram.percent,
                    "disk_percent": disk.percent
                }
            }
        )
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": str(e)}
        )

class ExecuteRequest(BaseModel):
    message: str
    user_id: str = "api_baymax"

# ── Web API Skill Dispatch Table — must stay in sync with main.py SKILL_MAP ──
# Defined at module level: built once, not on every request.
# CREATOR and JAVA_GITOPS intentionally use tech_lead (aligned with main.py).
_WEB_SKILL_MAP = {
    "OS_COMMAND":       os_controller.execute,
    "WEB_SEARCH":       web_search.execute,
    "CREATOR":          tech_lead.execute,
    "FACTORY":          software_factory.execute,
    "BACKUP":           backup_manager.execute,
    "INSPECTOR":        inspector.execute,
    "GITHUB":           github_search.execute,
    "REFACTOR":         refactor.execute,
    "JAVA_GITOPS":      tech_lead.execute,
    "CONVERSATION":     conversational.respond,
    INTENT_MEMORY_VAULT: memory_vault.execute,   # keyed by contract constant
    "JARVIS_HEALTH":    jarvis_health.execute,
    "UI_ACTION":        ui_automation.execute,
    "SYSTEM_STATUS":    system_status.execute,
}

# Hard contract guard — fail fast at startup if MEMORY_VAULT is unmapped
if INTENT_MEMORY_VAULT not in _WEB_SKILL_MAP:
    raise RuntimeError(
        f"CRITICAL: {INTENT_MEMORY_VAULT!r} not mapped in web_ui _WEB_SKILL_MAP — "
        "web API will silently drop all memory requests"
    )


@app.post("/api/v1/execute")
async def execute_command(
    req: ExecuteRequest,
    caller: UserPrincipal = Depends(require_role("operator")),
):
    msg = req.message
    uid = caller.user_id  # use the verified identity, not the self-reported req.user_id

    skill_name = route(msg)
    logger.info("[DISPATCH-TRACE] Received intent=%s | input='%s'", skill_name, msg[:80])
    if _DEBUG_ROUTING:
        logger.debug("[DISPATCH-TRACE] Available skills=%s", list(_WEB_SKILL_MAP.keys()))

    try:
        if skill_name == "FS_MANAGER":
            res = fs_manager.execute(msg, confirm_key=uid)
        elif skill_name == "CONVERSATION":
            res = conversational.respond(msg, chat_id=uid)
        else:
            if skill_name == INTENT_MEMORY_VAULT:
                logger.info("[DISPATCH-TRACE] Executing MEMORY_VAULT skill")
            fn = _WEB_SKILL_MAP.get(skill_name)
            if fn is None:
                logger.error("[DISPATCH-TRACE] FALLBACK triggered for intent=%s — no handler found", skill_name)
                fn = conversational.respond
            res = fn(msg)

        jarvis_requests_total.labels(skill=skill_name).inc()
    except Exception as e:
        logger.exception("[API] Unhandled exception in skill '%s' for user '%s'", skill_name, uid)
        res = f"❌ Skill Execution Failed: {type(e).__name__} — {e}"

    return {"intent": skill_name, "response": res}
