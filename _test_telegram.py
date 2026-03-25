from telegram.ext import ApplicationBuilder
import inspect
# Check build() return and what filters are available
from telegram.ext import filters
print("filters.VOICE:", hasattr(filters, 'VOICE'))
print("filters.TEXT:", hasattr(filters, 'TEXT'))
print("telegram-bot import OK")
