import ast, sys
sys.path.insert(0, 'C:\\Jarvis')

files = ['main.py', 'skills/fs_manager.py']
ok = True
for f in files:
    try:
        ast.parse(open(f, encoding='utf-8').read())
        print(f"SYNTAX OK: {f}")
    except SyntaxError as e:
        print(f"SYNTAX FAIL: {f} line {e.lineno}: {e.msg}")
        ok = False

print()
print("=== _parse_create_dir tests ===")
from skills.fs_manager import _parse_create_dir, has_pending_confirmation, execute

cases = [
    ("Jarvis, crie uma pasta no meu disco local C chamado de Jarvis 2.0", "C:\\Jarvis 2.0"),
    ("crie a pasta MeuProjeto em C:\\Users\\Daniel",                      "C:\\Users\\Daniel\\MeuProjeto"),
    ("crie uma pasta chamada Teste",                                       "C:\\Teste"),
    ("crie pasta C:\\ProjetosIA",                                         "C:\\ProjetosIA"),
]
for msg, expected_suffix in cases:
    p, name = _parse_create_dir(msg)
    path_str = str(p) if p else "None"
    status = "OK" if p and (path_str.endswith(expected_suffix.split("\\")[-1]) or expected_suffix in path_str) else "FAIL"
    print(f"  [{status}] '{msg[:55]}' -> {path_str}")

print()
print("=== Confirmation flow test ===")
KEY = "test_7072087793"

# Step 1: request mkdir
r1 = execute("crie uma pasta chamada Jarvis 2.0 no disco C", KEY)
print("Step1 (mkdir request):", "OK - pending" if "aprova" in r1 else f"FAIL: {r1[:100]}")

# Step 2: check pending
has = has_pending_confirmation(KEY)
print("Step2 (has_pending):", "OK - True" if has else "FAIL - False")

# Step 3: confirm
r3 = execute("sim", KEY)
print("Step3 (sim):", "CREATED" if "sucesso" in r3 or "ja existe" in r3 else f"RESULT: {r3[:100]}")

print()
print("All done." if ok else "SYNTAX ERRORS found!")
