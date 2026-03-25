import sys, ast
sys.path.insert(0, 'C:\\Jarvis')

# Syntax check
for f in ['skills/java_gitops.py']:
    try:
        ast.parse(open(f'C:\\Jarvis\\{f}', encoding='utf-8').read())
        print(f'SYNTAX OK: {f}')
    except SyntaxError as e:
        print(f'SYNTAX FAIL: {f} line {e.lineno}: {e.msg}')

# Routing tests
from router import route, VALID_SKILLS
print()
print('JAVA_GITOPS in VALID_SKILLS:', 'JAVA_GITOPS' in VALID_SKILLS)

cases = [
    ('Crie uma API Java Spring Boot para pedidos e abra um PR', 'JAVA_GITOPS'),
    ('crie um microservico com Kafka e Liquibase',              'JAVA_GITOPS'),
    ('quero um pull request com endpoint Spring',               'JAVA_GITOPS'),
    ('preciso de um controller Java para usuarios',             'JAVA_GITOPS'),
    ('busque no github python agent',                           'GITHUB'),
    ('crie um arquivo test.py',                                 'CREATOR'),
    ('crie a pasta MeuProjeto',                                 'FS_MANAGER'),
]
all_ok = True
for msg, exp in cases:
    r = route(msg)
    ok = r == exp
    if not ok:
        all_ok = False
    print(f"  [{'OK' if ok else 'FAIL'}] {exp:12s} <- {msg[:50]}")

print()
print('ALL ROUTES OK' if all_ok else 'SOME ROUTES FAILED')

# main.py wiring
src_m = open('C:\\Jarvis\\main.py', encoding='utf-8').read()
print()
print('main.py wiring:')
print('  java_gitops import:', 'from skills import java_gitops' in src_m)
print('  JAVA_GITOPS SKILL_MAP:', '"JAVA_GITOPS"' in src_m and 'java_gitops.execute' in src_m)

# .md file exists
from pathlib import Path
md = Path('C:\\Jarvis\\.claude\\skills\\java-gitops-architect.md')
print()
print(f'.claude/skills/java-gitops-architect.md: exists={md.exists()} size={md.stat().st_size}B')
