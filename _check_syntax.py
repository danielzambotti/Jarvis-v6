import ast, sys
files = [
    'main.py', 'router.py',
    'skills/creator.py', 'skills/os_controller.py',
    'skills/voice_handler.py', 'skills/structured_logger.py',
]
all_ok = True
for f in files:
    try:
        ast.parse(open(f, encoding='utf-8').read())
        print(f"OK:   {f}")
    except SyntaxError as e:
        print(f"FAIL: {f}  line {e.lineno}: {e.msg}")
        all_ok = False
sys.exit(0 if all_ok else 1)
