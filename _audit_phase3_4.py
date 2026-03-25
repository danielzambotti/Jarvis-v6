import ast, sys
sys.path.insert(0, 'C:\\Jarvis')

# 1. Syntax check both files
for fname in ['skills/reasoner.py', 'skills/ui_automation.py']:
    try:
        src = open(f'C:\\Jarvis\\{fname}', encoding='utf-8').read()
        ast.parse(src)
        print(f"SYNTAX OK: {fname}")
        # Check for missing function definitions
        if '_require_pyautogui' in src:
            if 'def _require_pyautogui' not in src:
                print(f"  BUG: _require_pyautogui() called but NEVER DEFINED in {fname}")
        # Check for calculator pattern
        if 'calc' in src.lower():
            print(f"  INFO: calculator pattern present")
    except SyntaxError as e:
        print(f"SYNTAX ERROR {fname} line {e.lineno}: {e.msg}")

# 2. Verify reasoner fixes
src_r = open('C:\\Jarvis\\skills\\reasoner.py', encoding='utf-8').read()
checks = [
    ('"format": "json"' in src_r,       'format:json in payload'),
    ('_JSON_RETRIES' in src_r,           'retry constant defined'),
    ('num_predict.*500' in src_r or 'max_tokens=500' in src_r, 'num_predict>=500 for ReAct'),
    ('CRITICAL' in src_r,               'CRITICAL keyword in system prompt'),
    ('_extract_json' in src_r,           '_extract_json() defined'),
]
import re
print('\nReasoner checks:')
for cond, label in checks:
    if isinstance(cond, str):
        cond = bool(re.search(cond, src_r))
    print(f"  [{'OK' if cond else 'MISSING'}] {label}")
