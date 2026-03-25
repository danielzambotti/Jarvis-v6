import ast, sys
files = [
    'main.py', 'router.py',
    'skills/os_controller.py',
    'skills/conversational.py',
    'skills/conversation_memory.py',
    'skills/reasoner.py',
]
ok = True
for f in files:
    try:
        ast.parse(open(f, encoding='utf-8').read())
        print(f"OK:   {f}")
    except SyntaxError as e:
        print(f"FAIL: {f}  line {e.lineno}: {e.msg}")
        ok = False
sys.exit(0 if ok else 1)
