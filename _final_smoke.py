"""Final smoke test — verifies main.py can build the Application without TypeError."""
import sys
sys.path.insert(0, 'C:\\Jarvis')

print("Step 1: checking python-telegram-bot version...")
import importlib.metadata
ptb_ver = importlib.metadata.version("python-telegram-bot")
httpx_ver = importlib.metadata.version("httpx")
print(f"  PTB:   {ptb_ver}")
print(f"  httpx: {httpx_ver}")

print("Step 2: all skill imports...")
from config import TELEGRAM_TOKEN
from security import is_authorized, sanitize_input
from router import route
from skills import os_controller, conversational, web_search, creator
from skills import inspector, fs_manager, backup_manager
from skills.notion_logger import log_to_notion
from skills.structured_logger import StructuredLogger
print("  All imports: OK")

print("Step 3: building Telegram Application...")
from telegram.ext import ApplicationBuilder, MessageHandler, filters
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
print("  ApplicationBuilder: OK")

print("Step 4: registering handlers...")
from main import handle_text, handle_voice
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
app.add_handler(MessageHandler(filters.VOICE, handle_voice))
print("  Handlers: OK")

print()
print("JARVIS v4.0 READY")
print(f"Run: python main.py  OR  start.bat")
