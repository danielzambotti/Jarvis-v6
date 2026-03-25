from pathlib import Path
ws = Path("C:/Jarvis/workspace")
f = ws / "structured_logger.py"
print("EXISTS:", f.exists())
if f.exists():
    lines = f.read_text(encoding="utf-8").splitlines()
    print("LINES:", len(lines))
    for l in lines[:15]:
        print(l)
