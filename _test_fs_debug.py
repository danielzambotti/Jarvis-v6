import sys, traceback
sys.path.insert(0, 'C:\\Jarvis')
from skills.fs_manager import execute, _parse_create_dir

msg = "crie uma pasta no meu disco local C chamado de Jarvis 2.0"

print("=== _parse_create_dir ===")
try:
    p, display = _parse_create_dir(msg)
    print("Path:", p)
    print("Display:", display)
except Exception as e:
    traceback.print_exc()

print()
print("=== execute() ===")
try:
    r = execute(msg, "test_user")
    print(r)
except Exception as e:
    traceback.print_exc()
