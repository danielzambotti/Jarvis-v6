import ast, sys
files = [
    'main.py', 'router.py',
    'skills/fs_manager.py',
    'skills/creator.py',
    'skills/os_controller.py',
    'skills/web_search.py',
    'skills/inspector.py',
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
