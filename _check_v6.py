import ast, sys
files = [
    'main.py', 'router.py', 'config.py', 'security.py',
    'skills/os_controller.py', 'skills/dev_architect.py',
    'skills/conversational.py', 'skills/conversation_memory.py',
    'skills/reasoner.py', 'skills/web_search.py',
    'skills/fs_manager.py', 'skills/github_search.py',
    'skills/backup_manager.py', 'skills/inspector.py',
    'skills/perceptor.py', 'skills/ui_automation.py',
    'web_ui/app.py',
]
ok = True
for f in files:
    try:
        ast.parse(open(f, encoding='utf-8').read())
        print(f"OK:   {f}")
    except SyntaxError as e:
        print(f"FAIL: {f}  line {e.lineno}: {e.msg}")
        ok = False
    except FileNotFoundError:
        print(f"MISS: {f}")
        ok = False
print()
print(f"Result: {'ALL OK' if ok else 'ERRORS'} ({len(files)} files)")
sys.exit(0 if ok else 1)
