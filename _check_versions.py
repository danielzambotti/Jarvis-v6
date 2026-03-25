import sys
print("VENV PTB version:")
import importlib.metadata
try:
    v = importlib.metadata.version("python-telegram-bot")
    print("  PTB:", v)
except:
    print("  not found")
try:
    v2 = importlib.metadata.version("httpx")
    print("  httpx:", v2)
except:
    print("  httpx not found")
