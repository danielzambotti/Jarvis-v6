"""
main.py — Jarvis v6.0 Entry Point (Enterprise API-First)
"""
import asyncio
import logging
import sys
import os
from pathlib import Path
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
)

from config import TELEGRAM_TOKEN, ALLOWED_CHAT_ID
from security import is_authorized, sanitize_input, detect_prompt_injection, check_rate_limit
from router import route
from skills import (
    conversational, web_search, creator, fs_manager, backup_manager,
    inspector, github_search, perceptor, refactor, tech_lead, os_controller,
    software_factory
)
from skills.conversation_memory import save_message, rotate_history
from skills.notion_logger import log_to_notion
from skills.structured_logger import StructuredLogger
from core.memory.manager import get_memory_manager
from skills.voice_handler import download_and_transcribe

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Singletons ────────────────────────────────────────────────────────────────
_slogger = StructuredLogger()
_memory  = get_memory_manager()

# ── Skill Dispatch Table (Sem UI_ACTION) ──────────────────────────────────────
# Dentro do seu main.py
SKILL_MAP = {
    "OS_COMMAND":   os_controller.execute,
    "WEB_SEARCH":   web_search.execute,
    "CREATOR":      tech_lead.execute, 
    "FACTORY":      software_factory.execute, # <-- ESTA LINHA CONECTA A ESTEIRA
    "BACKUP":       backup_manager.execute,
    "INSPECTOR":    inspector.execute,
    "GITHUB":       github_search.execute,
    "REFACTOR":     refactor.execute,
    "JAVA_GITOPS":  tech_lead.execute,
    "CONVERSATION": conversational.respond,
}

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
        plan = {}
        if len(user_input.split()) > 5:
            try: plan = reason_about(user_input, confirm_key)
            except: pass

        if plan.get("needs_web_search") and not plan.get("needs_os_action"):
            skill_name = "WEB_SEARCH"
            effective_input = plan.get("search_query", user_input)
        else:
            skill_name = route(user_input)
            # Use context-enriched input only for conversational skill
            effective_input = user_input_with_ctx if skill_name == "CONVERSATION" else user_input

        try:
            if skill_name == "FS_MANAGER":
                result = fs_manager.execute(effective_input, confirm_key=confirm_key)
            elif skill_name == "INSTALL":
                await update.message.reply_text("Pesquisando como instalar... aguarde.")
                await context.bot.send_chat_action(chat_id=chat_id, action="typing")
                search_query = plan.get("search_query", effective_input)
                search_context = web_search.execute(search_query)
                from skills import dev_architect
                await update.message.reply_text("Instruções encontradas. Instalando...")
                await context.bot.send_chat_action(chat_id=chat_id, action="typing")
                result = dev_architect.install_and_execute(effective_input, search_context)
            elif skill_name == "GITHUB":
                result = github_search.execute(effective_input)
            elif skill_name == "CONVERSATION":
                result = conversational.respond(effective_input, chat_id=confirm_key)
            else:
                skill_fn = SKILL_MAP.get(skill_name, conversational.respond)
                result = skill_fn(effective_input)
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
        await update.message.reply_text("Tentativa de manipulacao bloqueada.")
        return
    if not check_rate_limit(chat_id):
        await update.message.reply_text("Muitas mensagens em pouco tempo. Aguarde.")
        return
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

def main() -> None:
    import uvicorn
    sys.path.insert(0, str(Path(__file__).parent))
    from web_ui.app import app as fastapi_app

    logger.info("Jarvis v6.0 iniciando (Modo Enterprise / API-First)...")
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

    async def _run_all():
        uvi_config = uvicorn.Config(
            fastapi_app, host="0.0.0.0", port=8765, log_level="info", loop="asyncio"
        )
        uvi_server = uvicorn.Server(uvi_config)

        if telegram_enabled:
            await tg_app.initialize()
            await tg_app.start()
            await tg_app.updater.start_polling(drop_pending_updates=True)
            logger.info("[MAIN] Telegram iniciado.")
        else:
            logger.info("[MAIN] Telegram DESABILITADO. Rodando como API Local.")

        logger.info("Dashboard e API disponiveis: http://localhost:8765")
        loop = asyncio.get_running_loop()
        bot_instance = tg_app.bot if telegram_enabled else None
        
        perceptor.start(event_loop=loop, bot=bot_instance, chat_id=str(ALLOWED_CHAT_ID) if ALLOWED_CHAT_ID else "API_MODE")

        try:
            await asyncio.gather(uvi_server.serve(), _metrics_loop())
        finally:
            logger.info("Jarvis desligando...")
            if telegram_enabled:
                await tg_app.updater.stop()
                await tg_app.stop()
                await tg_app.shutdown()

    asyncio.run(_run_all())

if __name__ == "__main__":
    main()