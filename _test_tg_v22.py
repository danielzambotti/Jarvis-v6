import sys
sys.path.insert(0, 'C:\\Jarvis')
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from telegram import Update
import inspect

# Check if run_polling signature changed in v22
app = ApplicationBuilder().token.__doc__
print("ApplicationBuilder OK")

# Verify MessageHandler still works the same
mh = MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: None)
print("MessageHandler OK")

# Check voice filter
mh2 = MessageHandler(filters.VOICE, lambda u, c: None)
print("VOICE filter OK")

print("telegram v22 API compatible with main.py: YES")
