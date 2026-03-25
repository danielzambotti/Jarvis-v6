import sys
sys.path.insert(0, 'C:\\Jarvis')
from skills.fs_manager import _parse_create_dir

# This case: "crie a pasta MeuProjeto em C:\Users\Daniel"
# Level 1 regex captures "C:\Users\Daniel" (path after "em")
# But user wants to CREATE MeuProjeto INSIDE C:\Users\Daniel
# So correct result = C:\Users\Daniel\MeuProjeto
# Level 1 currently returns C:\Users\Daniel alone (doesn't include folder name)

# Test the actual problematic sentence from the chat log
msg = "Jarvis, crie uma pasta no meu disco local C chamado de Jarvis 2.0."
p, n = _parse_create_dir(msg)
print(f"REAL TEST: '{msg[:60]}'")
print(f"  Path: {p}")
print(f"  Name: {n}")
print(f"  EXPECTED: C:\\Jarvis 2.0")
print(f"  RESULT: {'OK' if p and 'Jarvis 2.0' in str(p) else 'FAIL'}")
