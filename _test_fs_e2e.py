import sys
sys.path.insert(0, 'C:\\Jarvis')
from router import route, _is_fs_intent, _is_creator_intent

tests = [
    ("Jarvis, crie uma pasta no meu disco local C chamado de Jarvis 2.0",   "FS_MANAGER"),
    ("crie um arquivo chamado test.py que faz X",                           "CREATOR"),
    ("liste o conteudo de C:\\",                                            "FS_MANAGER"),
    ("verifique se a pasta Jarvis 2.0 foi criada",                          "FS_MANAGER"),
    ("abra o epic games",                                                   "OS_COMMAND"),
    ("qual o preco do bitcoin",                                             "WEB_SEARCH"),
    ("leia o router.py e diga se e eficiente",                              "INSPECTOR"),
]

print("=== Router Tests ===\n")
all_ok = True
for msg, expected in tests:
    result = route(msg)
    status = "OK" if result == expected else "FAIL"
    if status == "FAIL":
        all_ok = False
    print(f"[{status}] Expected={expected:12s} Got={result:12s} | {msg[:55]}")

print()
print("=== FS_MANAGER Skill Test ===\n")
from skills.fs_manager import execute

# Test 1: primeiro comando -> deve pedir confirmacao
r1 = execute("crie uma pasta no meu disco local C chamado de Jarvis 2.0", "test_user")
print("Step 1 (create request):")
print(r1[:200])
print()

# Test 2: confirmacao -> deve criar a pasta
r2 = execute("sim", "test_user")
print("Step 2 (confirm):")
print(r2)
print()

# Test 3: verificar existencia
r3 = execute("verifique se a pasta Jarvis 2.0 foi criada", "test_user")
print("Step 3 (check exists):")
print(r3)
