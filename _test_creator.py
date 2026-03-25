"""
Integration test: simulates the exact Telegram message that failed in production.
Tests the full pipeline: router -> creator -> file written to workspace.
"""
import sys
sys.path.insert(0, 'C:\\Jarvis')

from router import route, _is_creator_intent

# ── Test 1: deterministic pre-check ──────────────────────────────────────────
test_msg = "Crie um arquivo chamado structured_logger.py que registra cada interação em JSON com: timestamp, skill usada, SHA256 dos primeiros 50 chars do input, e latência em ms"

pre = _is_creator_intent(test_msg)
print(f"Pre-check result: {pre}  (expected: True)")
assert pre is True, "FAIL: pre-check should be True"

routed = route(test_msg)
print(f"route() result:   {routed}  (expected: CREATOR)")
assert routed == "CREATOR", f"FAIL: expected CREATOR, got {routed}"

# ── Test 2: filename extraction ───────────────────────────────────────────────
from skills.creator import _extract_filename, _sanitize_filename, _get_workspace

raw = _extract_filename(test_msg)
safe = _sanitize_filename(raw)
print(f"Filename extract: {raw}")
print(f"Filename safe:    {safe}  (expected: structured_logger.py)")
assert safe == "structured_logger.py", f"FAIL: got {safe}"

# ── Test 3: workspace path ────────────────────────────────────────────────────
ws = _get_workspace()
print(f"Workspace:        {ws}")
assert ws.exists(), "FAIL: workspace dir doesn't exist"
assert "Jarvis" in str(ws), "FAIL: workspace not under C:\\Jarvis"

# ── Test 4: full execute() — writes the actual file ─────────────────────────
from skills.creator import execute
result = execute(test_msg)
print(f"\nexecute() output:\n{result}")

target = ws / "structured_logger.py"
assert target.exists(), f"FAIL: file not created at {target}"
content = target.read_text(encoding="utf-8")
assert len(content) > 100, "FAIL: file content too short"
print(f"\n✅ All tests passed! File: {target} ({len(content)} chars)")
