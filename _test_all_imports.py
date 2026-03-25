import sys
sys.path.insert(0, 'C:\\Jarvis')
# Test all imports that main.py uses
try:
    from config import TELEGRAM_TOKEN
    print("config: OK")
except Exception as e:
    print(f"config FAIL: {e}")

try:
    from security import is_authorized, sanitize_input
    print("security: OK")
except Exception as e:
    print(f"security FAIL: {e}")

try:
    from router import route
    print("router: OK")
except Exception as e:
    print(f"router FAIL: {e}")

try:
    from skills import os_controller, conversational, creator
    print("skills core: OK")
except Exception as e:
    print(f"skills core FAIL: {e}")

try:
    from skills import web_search
    print("web_search: OK")
except Exception as e:
    print(f"web_search FAIL: {e}")

try:
    from skills import inspector
    print("inspector: OK")
except Exception as e:
    print(f"inspector FAIL: {e}")

try:
    from skills.structured_logger import StructuredLogger
    print("structured_logger: OK")
except Exception as e:
    print(f"structured_logger FAIL: {e}")

try:
    from skills.notion_logger import log_to_notion
    print("notion_logger: OK")
except Exception as e:
    print(f"notion_logger FAIL: {e}")

print("All imports done.")
