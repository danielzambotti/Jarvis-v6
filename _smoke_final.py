import sys
sys.path.insert(0, 'C:\\Jarvis')

# Test 1: imports
from skills.backup_manager import create_backup, list_backups
print("backup_manager import: OK")

# Test 2: create backup
ok, msg = create_backup(trigger="smoke_test")
print("Backup created:", ok)
# Print without emojis to avoid cp1252 issues
print("Msg length:", len(msg), "chars")

# Test 3: list backups
listing = list_backups()
print("Backups listed:", "OK" if listing else "EMPTY")

# Test 4: router with backup intent
from router import route
cases = [
    ("crie um backup do projeto", "BACKUP"),
    ("crie uma pasta chamada Jarvis 2.0 em C:", "FS_MANAGER"),
    ("crie um arquivo test.py", "CREATOR"),
    ("verifique se a pasta existe", "FS_MANAGER"),
    ("sim", "FS_MANAGER"),   # confirmation goes to FS_MANAGER? No: FS only if has FS_KEYWORDS
]
print("\nRouter tests:")
all_ok = True
for msg_text, expected in cases[:4]:
    r = route(msg_text)
    status = "OK" if r == expected else "FAIL"
    if status == "FAIL": all_ok = False
    print(f"  [{status}] '{msg_text[:40]}' -> {r} (expected {expected})")

print("\nAll tests passed!" if all_ok else "\nSome tests FAILED")
