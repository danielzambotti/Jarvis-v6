"""
main.py — Jarvis v6.0 Entry Point (Enterprise API-First)
"""
import asyncio
import logging
import sys
import os
from pathlib import Path

# ── Headless X11 Workaround for PyAutoGUI ────────────────────────────────────
# Must execute BEFORE any import that loads pyautogui → Xlib.
# In headless Docker + Xvfb, .Xauthority does not exist at startup, causing:
#   FileNotFoundError: /app/.Xauthority
# This patch is safe, idempotent, and cannot crash the app.
import os as _os
from pathlib import Path as _Path

def _ensure_xauthority() -> None:
    try:
        xauth_path = _os.environ.get("XAUTHORITY", _os.path.expanduser("~/.Xauthority"))
        path_obj = _Path(xauth_path)

        # Ensure parent directory exists
        path_obj.parent.mkdir(parents=True, exist_ok=True)

        # Create file if missing (idempotent — touch is a no-op if it already exists)
        path_obj.touch(exist_ok=True)

        print(f"[BOOT] XAUTHORITY ensured at: {path_obj}")
    except Exception as exc:
        print(f"[BOOT-WARN] Failed to ensure XAUTHORITY: {exc}")

_ensure_xauthority()
# ─────────────────────────────────────────────────────────────────────────────

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
)

from config import TELEGRAM_TOKEN, ALLOWED_CHAT_ID
from security import is_authorized, sanitize_input, detect_prompt_injection, check_rate_limit
from router import route, INTENT_MEMORY_VAULT, _DEBUG_ROUTING
from skills import (
    conversational, web_search, creator, fs_manager, backup_manager,
    inspector, github_search, perceptor, refactor, tech_lead, os_controller,
    software_factory, memory_vault, jarvis_health, ui_automation, system_status
)
from core.memory.retriever import get_relevant_context, should_use_memory
from core.memory.vector_store import get_vector_store
from skills.conversation_memory import save_message, rotate_history
from skills.notion_logger import log_to_notion
from skills.structured_logger import StructuredLogger
from core.memory.manager import get_memory_manager
from core.metrics import (
    jarvis_requests_total,
    jarvis_security_blocks_total,
    jarvis_response_time_seconds,
    start_metrics_server,
)
from core.security.doc_processor import get_doc_processor
from core.security.dlp import get_dlp_engine as _get_dlp
from core.resilience.backup_orchestrator import get_backup_manager
from skills.voice_handler import download_and_transcribe

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Singletons ────────────────────────────────────────────────────────────────
_slogger    = StructuredLogger()
_memory     = get_memory_manager()
_doc_proc   = get_doc_processor()
_main_dlp   = _get_dlp()
_bk_manager = get_backup_manager()

# Resilience config (overridable via environment)
_BACKUP_INTERVAL_HOURS = int(os.environ.get("BACKUP_INTERVAL_HOURS", "12"))
_RPO_HOURS             = int(os.environ.get("RPO_HOURS", "72"))
_BACKUP_STARTUP_DELAY  = 300   # seconds before first backup (let system stabilise)

# ── Skill Dispatch Table ───────────────────────────────────────────────────────
SKILL_MAP = {
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

# ── Hard contract guard — fail fast at startup if MEMORY_VAULT is unmapped ───
if INTENT_MEMORY_VAULT not in SKILL_MAP:
    raise RuntimeError(
        f"CRITICAL: {INTENT_MEMORY_VAULT!r} intent is not mapped in SKILL_MAP — "
        "dispatcher will silently drop all memory requests"
    )

async def _process_text(update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str) -> None:
    chat_id = update.effective_chat.id
    confirm_key = str(chat_id)
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    _t = _slogger.start()
    save_message(confirm_key, "user", user_input)

    # ── Memory: store incoming turn & fetch context for injection ─────────────
    _memory.store_interaction(
        user_id=confirm_key, session_id=confirm_key,
        role="user", content=user_input, tokens=len(user_input.split()),
    )
    active_ctx = _memory.get_active_context(session_id=confirm_key)
    # Inject context into user_input for CONVERSATION skill (prepend as digest)
    if active_ctx:
        ctx_digest = "\n".join(
            f"[{m['role'].upper()}]: {m['content'][:200]}"
            for m in active_ctx[-6:]   # last 6 turns max
            if m["role"] != "system"
        )
        user_input_with_ctx = f"[CONVERSATION HISTORY]\n{ctx_digest}\n\n[CURRENT MESSAGE]\n{user_input}"
    else:
        user_input_with_ctx = user_input

    if fs_manager.has_pending_confirmation(confirm_key):
        skill_name = "FS_MANAGER"
        try: result = fs_manager.execute(user_input, confirm_key=confirm_key)
        except Exception as e: result = f"Erro na confirmacao: {e}"
    else:
        skill_name = route(user_input)

        logger.info("[DISPATCH-TRACE] Received intent=%s | input='%s'", skill_name, user_input[:80])
        if _DEBUG_ROUTING:
            logger.debug("[DISPATCH-TRACE] Available skills=%s", list(SKILL_MAP.keys()))

        # RAG semantic memory injection: if the prompt references past context,
        # retrieve relevant memories and prepend them to the effective input.
        _rag_context = ""
        if skill_name not in (INTENT_MEMORY_VAULT, "FS_MANAGER", "INSTALL") and should_use_memory(user_input):
            try:
                _rag_context = await asyncio.to_thread(get_relevant_context, user_input)
            except Exception as _rag_exc:
                logger.warning("[MAIN] RAG context retrieval failed: %s", _rag_exc)

        if _rag_context:
            effective_input = (
                f"[SEMANTIC MEMORY CONTEXT]\n{_rag_context}\n\n"
                f"[CURRENT MESSAGE]\n{user_input}"
            )
        elif skill_name == "CONVERSATION":
            effective_input = user_input_with_ctx
        else:
            effective_input = user_input

        try:
            with jarvis_response_time_seconds.labels(skill=skill_name).time():
                if skill_name == "FS_MANAGER":
                    result = fs_manager.execute(effective_input, confirm_key=confirm_key)
                elif skill_name == "INSTALL":
                    await update.message.reply_text("Pesquisando como instalar... aguarde.")
                    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
                    search_context = web_search.execute(effective_input)
                    from skills import dev_architect
                    await update.message.reply_text("Instruções encontradas. Instalando...")
                    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
                    result = dev_architect.install_and_execute(effective_input, search_context)
                elif skill_name == "GITHUB":
                    result = github_search.execute(effective_input)
                elif skill_name == "CONVERSATION":
                    result = conversational.respond(effective_input, chat_id=confirm_key)
                else:
                    if skill_name == INTENT_MEMORY_VAULT:
                        logger.info("[DISPATCH-TRACE] Executing MEMORY_VAULT skill")
                    skill_fn = SKILL_MAP.get(skill_name)
                    if skill_fn is None:
                        logger.error("[DISPATCH-TRACE] FALLBACK triggered for intent=%s — no handler found", skill_name)
                        skill_fn = conversational.respond
                    result = skill_fn(effective_input)
            jarvis_requests_total.labels(skill=skill_name).inc()
        except Exception as e:
            logger.error("[MAIN] skill=%s raised: %s", skill_name, e)
            result = f"Erro na skill '{skill_name}': {e}"

    if len(result) > 4000: result = result[:4000] + "\n\n…[truncado]"
    await update.message.reply_text(result)
    save_message(confirm_key, "assistant", result[:500])
    rotate_history(confirm_key)

    # ── Memory: store assistant reply (DLP-sanitised inside store_interaction) ──
    _memory.store_interaction(
        user_id=confirm_key, session_id=confirm_key,
        role="assistant", content=result, tokens=len(result.split()),
    )
    asyncio.create_task(asyncio.to_thread(log_to_notion, user_input, skill_name, result))
    asyncio.create_task(asyncio.to_thread(_slogger.log, _t, skill_name, user_input, result))

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text: return
    chat_id = update.effective_chat.id
    if not is_authorized(chat_id): return
    user_input = sanitize_input(update.message.text)
    if not user_input: return
    is_safe, reason = detect_prompt_injection(user_input)
    if not is_safe:
        jarvis_security_blocks_total.labels(layer="prompt_injection").inc()
        await update.message.reply_text("Tentativa de manipulacao bloqueada.")
        return
    if not check_rate_limit(chat_id):
        jarvis_security_blocks_total.labels(layer="rate_limit").inc()
        await update.message.reply_text("Muitas mensagens em pouco tempo. Aguarde.")
        return
    await _process_text(update, context, user_input)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Telegram document upload handler.
    All files pass through SecureDocumentProcessor before content touches the LLM.
    Allowed: PDF, TXT, MD, CSV — max 5 MB.
    """
    if not update.message or not update.message.document: return
    chat_id = update.effective_chat.id
    if not is_authorized(chat_id): return

    import tempfile
    doc = update.message.document
    caption = sanitize_input(update.message.caption or "")

    await context.bot.send_chat_action(chat_id=chat_id, action="upload_document")

    # Download to a temp file
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        suffix = Path(doc.file_name or "upload").suffix.lower() or ".bin"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        await tg_file.download_to_drive(str(tmp_path))
    except Exception as exc:
        await update.message.reply_text(f"Erro ao baixar arquivo: {exc}")
        return

    # Full ingestion pipeline
    result = _doc_proc.safe_parse(tmp_path)
    try:
        tmp_path.unlink(missing_ok=True)
    except OSError:
        pass

    if not result.ok:
        jarvis_security_blocks_total.labels(layer="doc_ingestion").inc()
        await update.message.reply_text(
            f"Arquivo bloqueado pelo pipeline de seguranca:\n`{result.rejected_reason}`"
        )
        return

    # DLP-sanitise extracted text
    clean_text, findings = _main_dlp.sanitize_text(result.text)
    if findings:
        logger.warning("[MAIN] DLP removed %d item(s) from document %s", len(findings), doc.file_name)

    # Build user input for routing: inject document text as context
    user_input = (
        f"[DOCUMENT: {doc.file_name}]\n{clean_text[:4000]}"
        + (f"\n\n[USER NOTE]: {caption}" if caption else "")
    )
    await _process_text(update, context, user_input)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.voice: return
    chat_id = update.effective_chat.id
    if not is_authorized(chat_id): return
    await context.bot.send_chat_action(chat_id=chat_id, action="record_voice")
    transcription = await download_and_transcribe(context.bot, update.message.voice.file_id)
    if transcription.startswith("❌"):
        await update.message.reply_text(transcription)
        return
    user_input = sanitize_input(transcription)
    if not user_input: return
    await update.message.reply_text(f"🎤 _Entendi:_ {user_input}", parse_mode="Markdown")
    await _process_text(update, context, user_input)

def _sre_startup_checks() -> None:
    """Proactive sanity checks that run before the event loop starts."""
    import shutil
    import requests as _req
    from config import OLLAMA_URL
    from security import is_safe_path

    # ── Check 1: Ollama reachability ─────────────────────────────────────
    try:
        r = _req.get(OLLAMA_URL.replace("/api/generate", ""), timeout=5)
        logger.info("[SRE] Ollama connection verified at %s (HTTP %s)", OLLAMA_URL, r.status_code)
    except Exception as e:
        logger.critical("[SRE] CRITICAL — Ollama unreachable at %s: %s", OLLAMA_URL, e)
        logger.critical("[SRE] All LLM-dependent skills will fail until Ollama is running.")

    # ── Check 2: Workspace path resolution ───────────────────────────────
    workspace = os.environ.get("WORKSPACE_PATH", "/app/workspace")
    test_path = str(Path(workspace) / ".sre_test")
    try:
        if is_safe_path(test_path):
            logger.info("[SRE] Workspace path resolution OK: %s", workspace)
        else:
            logger.critical(
                "[SRE] CRITICAL — is_safe_path('%s') returned False. "
                "File-based skills (CREATOR, REFACTOR, FS_MANAGER) will be blocked.", test_path
            )
    except Exception as e:
        logger.critical("[SRE] CRITICAL — Path resolution check threw: %s", e)

    # ── Check 3: CLI tool availability ───────────────────────────────────
    for tool, skills in [("git", "REFACTOR/JAVA_GITOPS"), ("docker", "FACTORY/SYSTEM_STATUS")]:
        path = shutil.which(tool)
        if path:
            logger.info("[SRE] %s found at %s — %s skills operational.", tool, path, skills)
        else:
            logger.warning("[SRE] WARNING — '%s' not found in PATH. %s skills will fail.", tool, skills)


def main() -> None:
    import uvicorn
    sys.path.insert(0, str(Path(__file__).parent))
    from web_ui.app import app as fastapi_app

    _sre_startup_checks()
    logger.info("Jarvis v6.0 iniciando (Modo Enterprise / API-First)...")
    start_metrics_server(port=8000)
    telegram_enabled = bool(TELEGRAM_TOKEN and TELEGRAM_TOKEN.strip() != "DISABLED")
    tg_app = None

    if telegram_enabled:
        tg_app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            if is_authorized(update.effective_chat.id):
                await update.message.reply_text("Jarvis v6.0 online.\nDashboard: http://localhost:8765")
        async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            if is_authorized(update.effective_chat.id):
                import psutil
                await update.message.reply_text(f"Jarvis v6.0 Online\nCPU: {psutil.cpu_percent()}%\nAPI: http://localhost:8765")
        tg_app.add_handler(CommandHandler("start", cmd_start))
        tg_app.add_handler(CommandHandler("status", cmd_status))
        tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        tg_app.add_handler(MessageHandler(filters.VOICE, handle_voice))
        tg_app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    async def _metrics_loop():
        import psutil
        from web_ui.app import broadcast
        while True:
            try:
                disk = psutil.disk_usage("/")
                ram = psutil.virtual_memory()
                await broadcast("metrics", {
                    "cpu_pct": psutil.cpu_percent(interval=0.5),
                    "ram_pct": ram.percent,
                    "ram_used_gb": round(ram.used / 1e9, 2),
                    "ram_total_gb": round(ram.total / 1e9, 2),
                    "disk_pct": disk.percent,
                    "disk_free_gb": round(disk.free / 1e9, 2),
                    "ws_clients": 0,
                })
            except Exception: pass
            await asyncio.sleep(5)

    async def _resilience_loop():
        """
        Autonomous backup scheduler.
        Runs create_backup() + enforce_rpo() every _BACKUP_INTERVAL_HOURS hours.
        First cycle is delayed by _BACKUP_STARTUP_DELAY seconds to let the
        application stabilise before stressing the filesystem at cold start.

        Config (env vars):
          BACKUP_INTERVAL_HOURS  — how often to back up (default: 12)
          RPO_HOURS              — max backup retention window (default: 72)
        """
        logger.info(
            "[RESILIENCE] Backup scheduler active — interval=%dh, RPO=%dh, first run in %ds",
            _BACKUP_INTERVAL_HOURS, _RPO_HOURS, _BACKUP_STARTUP_DELAY,
        )
        await asyncio.sleep(_BACKUP_STARTUP_DELAY)
        while True:
            try:
                result = await asyncio.to_thread(
                    _bk_manager.create_backup, _RPO_HOURS
                )
                if result.ok:
                    logger.info("[RESILIENCE] %s", result)
                else:
                    logger.error("[RESILIENCE] Backup failed: %s", result.error)
            except Exception as exc:
                logger.error("[RESILIENCE] Backup cycle exception: %s", exc)
            await asyncio.sleep(_BACKUP_INTERVAL_HOURS * 3600)

    async def _memory_decay_loop():
        """
        Autonomous memory decay scheduler.
        Every MEMORY_DECAY_INTERVAL_HOURS hours, applies importance decay to
        memories not accessed in 7+ days, and soft-deletes entries that fall
        below the minimum importance threshold.
        First cycle is delayed 300 s to allow the system to stabilise.
        """
        _DECAY_INTERVAL = int(os.environ.get("MEMORY_DECAY_INTERVAL_HOURS", "6")) * 3600
        logger.info("[MEMORY_DECAY] Decay scheduler active — interval=%dh", _DECAY_INTERVAL // 3600)
        await asyncio.sleep(300)
        while True:
            try:
                vs    = get_vector_store()
                count = await asyncio.to_thread(vs.decay_memories)
                logger.info("[MEMORY_DECAY] Decay cycle complete — %d memories updated", count)
            except Exception as exc:
                logger.error("[MEMORY_DECAY] Decay cycle failed: %s", exc)
            await asyncio.sleep(_DECAY_INTERVAL)

    async def _run_all():
        nonlocal telegram_enabled
        uvi_config = uvicorn.Config(
            fastapi_app, host="0.0.0.0", port=8765, log_level="info", loop="asyncio"
        )
        uvi_server = uvicorn.Server(uvi_config)

        if telegram_enabled:
            try:
                await tg_app.initialize()
                await tg_app.start()
                await tg_app.updater.start_polling(drop_pending_updates=True)
                logger.info("[MAIN] Telegram iniciado.")
            except Exception as e:
                logger.warning(f"Telegram bot disabled: {e}")
                telegram_enabled = False
        else:
            logger.info("[MAIN] Telegram DESABILITADO. Rodando como API Local.")

        logger.info("Dashboard e API disponiveis: http://localhost:8765")
        loop = asyncio.get_running_loop()
        bot_instance = tg_app.bot if telegram_enabled else None
        
        perceptor.start(event_loop=loop, bot=bot_instance, chat_id=str(ALLOWED_CHAT_ID) if ALLOWED_CHAT_ID else "API_MODE")

        try:
            await asyncio.gather(
                uvi_server.serve(),
                _metrics_loop(),
                _resilience_loop(),
                _memory_decay_loop(),
            )
        finally:
            logger.info("Jarvis desligando...")
            if telegram_enabled and tg_app is not None:
                await tg_app.updater.stop()
                await tg_app.stop()
                await tg_app.shutdown()

    asyncio.run(_run_all())

if __name__ == "__main__":
    main()