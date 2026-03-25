"""
Full startup smoke-test for Jarvis v4.0.
Imports main module and verifies ApplicationBuilder initializes without TypeError.
"""
import sys
sys.path.insert(0, 'C:\\Jarvis')

print("Step 1: importing main module...")
# Import all the pieces main.py imports
from config import TELEGRAM_TOKEN
from security import is_authorized, sanitize_input
from router import route
from skills import os_controller, conversational, web_search, creator, inspector
from skills.notion_logger import log_to_notion
from skills.structured_logger import StructuredLogger
from skills.voice_handler import download_and_transcribe
print("Step 1 OK: all imports successful")

print("Step 2: building Telegram Application...")
from telegram.ext import ApplicationBuilder, MessageHandler, filters
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
print("Step 2 OK: ApplicationBuilder().token().build() succeeded")

print("Step 3: registering handlers...")
from main import handle_text, handle_voice
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
app.add_handler(MessageHandler(filters.VOICE, handle_voice))
print("Step 3 OK: handlers registered")

print("\n✅ JARVIS v4.0 STARTUP SMOKE-TEST PASSED")
print("Run: .\\venv\\Scripts\\python.exe main.py")
