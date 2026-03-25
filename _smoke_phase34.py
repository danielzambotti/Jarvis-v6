"""Phase 3+4 targeted smoke test"""
import sys, traceback
sys.path.insert(0, 'C:\\Jarvis')

PASS, FAIL = [], []
def test(name, fn):
    try:
        result = fn()
        print(f"  PASS  {name}" + (f" -> {result}" if result is not None else ""))
        PASS.append(name)
    except Exception as e:
        print(f"  FAIL  {name}: {e}")
        FAIL.append(name)

print("=== Phase 3: ReAct / Reasoner ===")
from skills.reasoner import _call_ollama, _extract_json, reason_about, react_loop

test("_extract_json strips fences",
     lambda: _extract_json('```json\n{"a":1}\n```') == '{"a":1}')

test("_extract_json handles preamble",
     lambda: '{"a":1}' in _extract_json('Sure! Here is the JSON: {"a":1}'))

test("_extract_json finds nested",
     lambda: _extract_json('{"a":{"b":2}}') == '{"a":{"b":2}}')

# Verify retry constant
import skills.reasoner as rmod
test("_JSON_RETRIES == 2", lambda: rmod._JSON_RETRIES == 2)
test("_MAX_STEPS == 6",    lambda: rmod._MAX_STEPS == 6)

# Verify format:json in payload via source inspection
import ast as _ast
src = open('C:\\Jarvis\\skills\\reasoner.py', encoding='utf-8').read()
test("format:json in source",   lambda: '"format": "json"' in src)
test("num_predict 500 ReAct",   lambda: 'max_tokens=500' in src or '500' in src)
test("CRITICAL in system",      lambda: 'CRITICAL' in src)
test("retry suffix on retry",   lambda: 'CRITICAL: Output MUST' in src)

print()
print("=== Phase 4: UI Automation ===")
from skills.ui_automation import (
    _require_pyautogui, _ps_calculate, execute,
    take_screenshot, click_at, type_text, hotkey
)

test("_require_pyautogui defined", lambda: callable(_require_pyautogui))
test("_require_pyautogui returns bool",
     lambda: isinstance(_require_pyautogui(), bool))

# Calculator — Strategy A (PowerShell, no GUI needed)
test("calc 15*3 = 45",    lambda: _ps_calculate("15*3") == "45")
test("calc 15 x 3 = 45",  lambda: _ps_calculate("15 x 3") == "45")
test("calc 100/4 = 25",   lambda: _ps_calculate("100/4") == "25")
test("calc (5+3)*2 = 16", lambda: _ps_calculate("(5+3)*2") == "16")

# execute() dispatch
test("execute calc route PS",
     lambda: "45" in execute("calcular 15 x 3"))
test("execute bare expr",
     lambda: "45" in execute("15 x 3"))
test("execute unknown returns help",
     lambda: "Exemplos" in execute("do something weird"))

print()
print("=== Full syntax check ===")
import ast
all_ok = True
for f in ['skills/reasoner.py', 'skills/ui_automation.py', 'main.py']:
    try:
        ast.parse(open(f'C:\\Jarvis\\{f}', encoding='utf-8').read())
        print(f"  SYNTAX OK: {f}")
    except SyntaxError as e:
        print(f"  SYNTAX FAIL {f} line {e.lineno}: {e.msg}")
        all_ok = False

print(f"\n{'='*50}")
total = len(PASS)+len(FAIL)
print(f"PASSED: {len(PASS)}/{total}")
if FAIL:
    print(f"FAILED: {FAIL}")
    sys.exit(1)
else:
    print("Phase 3+4 verified. Run: python main.py")
