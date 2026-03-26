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
from router import route
from skills import (
    conversational, os_controller, web_search, creator, fs_manager,
    backup_manager, inspector, github_search, refactor, java_gitops,
    system_status, jarvis_health
)

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

# FIX 4: Criar a classe que mapeia o JSON do Baymax
class ExecuteRequest(BaseModel):
    message: str
    user_id: str = "api_baymax"

@app.post("/api/v1/execute")
async def execute_command(
    req: ExecuteRequest,
    caller: UserPrincipal = Depends(require_role("operator")),
):
    msg = req.message
    uid = caller.user_id  # use the verified identity, not the self-reported req.user_id
    
    skill_name = route(msg)
    try:
        if skill_name == "FS_MANAGER":
            res = fs_manager.execute(msg, confirm_key=uid)
        elif skill_name == "CONVERSATION":
            res = conversational.respond(msg, chat_id=uid)
        else:
            # Mapeamento dinâmico das skills
            skills_map = {
                "OS_COMMAND": os_controller.execute, "WEB_SEARCH": web_search.execute,
                "CREATOR": creator.execute, "BACKUP": backup_manager.execute,
                "INSPECTOR": inspector.execute, "GITHUB": github_search.execute,
                "REFACTOR": refactor.execute, "JAVA_GITOPS": java_gitops.execute,
                "SYSTEM_STATUS": system_status.execute,
                "JARVIS_HEALTH": jarvis_health.execute,
            }
            fn = skills_map.get(skill_name, conversational.respond)
            res = fn(msg)
    except Exception as e:
        logger.exception("[API] Unhandled exception in skill '%s' for user '%s'", skill_name, uid)
        res = f"❌ Skill Execution Failed: {type(e).__name__} — {e}"
    
    return {"intent": skill_name, "response": res}
