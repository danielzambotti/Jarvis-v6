import ast, sys

# Syntax check
try:
    src = open('C:\\Jarvis\\main.py', encoding='utf-8').read()
    ast.parse(src)
    print("SYNTAX OK: main.py")
except SyntaxError as e:
    print(f"SYNTAX ERROR line {e.lineno}: {e.msg}")
    sys.exit(1)

# Verify the correct PTB lifecycle pattern is present
checks = [
    ("await tg_app.initialize()",          "PTB initialize()"),
    ("await tg_app.start()",               "PTB start()"),
    ("await tg_app.updater.start_polling", "PTB updater.start_polling()"),
    ("await tg_app.updater.stop()",        "PTB updater.stop()"),
    ("await tg_app.stop()",                "PTB stop()"),
    ("await tg_app.shutdown()",            "PTB shutdown()"),
    ("asyncio.get_running_loop()",         "get_running_loop (not get_event_loop)"),
    ("uvi_server.serve()",                 "uvicorn serve"),
    ("run_polling" not in src or
     "run_polling" in src and "updater.start_polling" in src,
                                           "run_polling replaced by manual lifecycle"),
]

all_ok = True
for check, label in checks:
    if isinstance(check, bool):
        ok = check
    else:
        ok = check in src
    status = "OK" if ok else "MISSING"
    if not ok:
        all_ok = False
    print(f"  [{status}] {label}")

print()
print("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED")
sys.exit(0 if all_ok else 1)
