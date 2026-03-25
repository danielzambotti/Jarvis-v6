import ast, sys
sys.path.insert(0, 'C:\\Jarvis')
src = open('C:\\Jarvis\\main.py', encoding='utf-8').read()
try:
    ast.parse(src)
    print("SYNTAX OK: main.py")
except SyntaxError as e:
    print(f"SYNTAX ERROR line {e.lineno}: {e.msg}")
    sys.exit(1)

# Also verify cmd_clear is properly defined
assert 'async def cmd_clear' in src, "cmd_clear missing!"
assert 'async def cmd_help' in src, "cmd_help missing!"
assert 'async def cmd_start' in src, "cmd_start missing!"
print("All command handlers defined: OK")
