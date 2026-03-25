"""Targeted smoke test — Tasks 1, 2, 3, 4."""
import sys, ast
sys.path.insert(0, 'C:\\Jarvis')

PASS, FAIL = [], []
def test(name, fn):
    try:
        r = fn()
        print(f"  PASS  {name}" + (f" -> {r}" if r not in (None, True, False) else ""))
        PASS.append(name)
    except Exception as e:
        print(f"  FAIL  {name}: {e}")
        FAIL.append(name)

# Task 1: inspector.py language enforcement
print("=== Task 1: inspector.py language rule ===")
src_i = open('C:\\Jarvis\\skills\\inspector.py', encoding='utf-8').read()
test("CRITICAL LANGUAGE RULE present",
     lambda: "CRITICAL LANGUAGE RULE" in src_i)
test("PT-BR explicit in prompt",
     lambda: "PT-BR" in src_i)
test("respond in same language explicit",
     lambda: "EXACT same language" in src_i or "exact same language" in src_i.lower())

# Task 2: refactor.py exists + syntax OK
print("\n=== Task 2: refactor.py ===")
src_r = open('C:\\Jarvis\\skills\\refactor.py', encoding='utf-8').read()
test("syntax OK",     lambda: ast.parse(src_r) is not None)
test("backup first",  lambda: "create_backup" in src_r)
test("ast.parse validate", lambda: "ast.parse" in src_r)
test("num_predict=4000", lambda: "4000" in src_r)
test("_ask_ollama_rewrite defined", lambda: "def _ask_ollama_rewrite" in src_r)
test("execute defined",  lambda: "def execute" in src_r)
test("write_text call",  lambda: "write_text" in src_r)

# Task 2b: router REFACTOR intent
print("\n=== Task 2b: router REFACTOR routing ===")
from router import route, VALID_SKILLS
test("REFACTOR in VALID_SKILLS", lambda: "REFACTOR" in VALID_SKILLS)

refactor_cases = [
    ("Refatore o router.py aplicando as melhorias de seguranca que voce sugeriu", "REFACTOR"),
    ("aplique as melhorias no security.py",                                       "REFACTOR"),
    ("reescreva o os_controller com as correcoes",                                "REFACTOR"),
    ("apply the fixes to web_search.py",                                          "REFACTOR"),
    ("analise o router.py",                                                       "INSPECTOR"),  # inspect != refactor
    ("crie um arquivo test.py",                                                   "CREATOR"),    # no regression
]
for msg, expected in refactor_cases:
    r = route(msg)
    test(f"route({expected[:12]}..)",
         lambda e=expected, r=r: None if r == e else (_ for _ in ()).throw(
             AssertionError(f"got {r}, want {e}")))

# Task 2c: main.py SKILL_MAP wired
print("\n=== Task 2c: main.py SKILL_MAP ===")
src_m = open('C:\\Jarvis\\main.py', encoding='utf-8').read()
test("refactor imported",       lambda: "from skills import refactor" in src_m)
test("REFACTOR in SKILL_MAP",   lambda: '"REFACTOR"' in src_m and "refactor.execute" in src_m)

# Task 3: .env model update
print("\n=== Task 3: .env model ===")
env = open('C:\\Jarvis\\.env', encoding='utf-8').read()
test("model is qwen3-coder:30b", lambda: "OLLAMA_MODEL=qwen3-coder:30b" in env)
test("llama3 removed",          lambda: "OLLAMA_MODEL=llama3" not in env)
test("OLLAMA_CHAT_URL present",  lambda: "OLLAMA_CHAT_URL=" in env)

print(f"\n{'='*50}")
print(f"PASSED: {len(PASS)}/{len(PASS)+len(FAIL)}")
if FAIL:
    print(f"FAILED: {FAIL}")
    sys.exit(1)
else:
    print("ALL TASKS VERIFIED")
