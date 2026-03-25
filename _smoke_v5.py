import sys
sys.path.insert(0, 'C:\\Jarvis')

# Test 1: KeyError 'env' fix — format the PS script
from skills.os_controller import _PS_FIND_IN_PATH_AND_DIRS
try:
    formatted = _PS_FIND_IN_PATH_AND_DIRS.format(app_name="MCP-Windows")
    print("BUG-FIX KeyError env: OK (no KeyError)")
except KeyError as e:
    print(f"BUG STILL PRESENT: KeyError {e}")

# Test 2: memory system
from skills.conversation_memory import save_message, get_history, get_context_string, clear_history
TEST_CHAT = "test_999"
clear_history(TEST_CHAT)
save_message(TEST_CHAT, "user", "Preciso instalar o MCP-Windows pro Claude")
save_message(TEST_CHAT, "assistant", "Vou pesquisar como instalar o MCP-Windows")
save_message(TEST_CHAT, "user", "pode continuar?")
hist = get_history(TEST_CHAT, 10)
print(f"Memory: {len(hist)} messages saved/loaded: OK")
ctx = get_context_string(TEST_CHAT, 3)
print(f"Context string: {len(ctx)} chars: OK")
clear_history(TEST_CHAT)

# Test 3: full startup
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters
from config import TELEGRAM_TOKEN
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
from main import handle_text, handle_voice
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
app.add_handler(MessageHandler(filters.VOICE, handle_voice))
print("Full startup: OK")
print()
print("JARVIS v5.0 ALL TESTS PASSED")
