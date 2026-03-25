"""
test_sandbox_ignition.py — Trinity Memory Ignition: Sandbox Validation
=======================================================================
Passes a controlled payload directly to run_in_sandbox() to validate
Zero-Trust constraints without requiring Ollama or the Telegram stack.

Expected result:
  - fibonacci_test   : PASS  (pure math, no syscalls)
  - shadow_read_test : FAIL  PermissionError — /etc/shadow unreadable by nobody
  - passwd_read_test : PASS  /etc/passwd is world-readable, but reads the
                             CONTAINER's own file, never the host's
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from core.security.sandbox import run_in_sandbox

# ── Payload: exactly what an LLM-generated factory output would look like ─────

PAYLOAD = '''
# stdlib only — no pip install needed (network is disabled anyway)
import sys
import traceback

# ── Production Code ──────────────────────────────────────────────────────────

def fibonacci(n: int) -> list:
    """Returns the Fibonacci sequence up to the nth number."""
    if n <= 0:
        return []
    seq = [0, 1]
    while len(seq) < n:
        seq.append(seq[-1] + seq[-2])
    return seq[:n]


def read_shadow_file() -> str:
    """Malicious: attempts to read /etc/shadow (root-only file)."""
    with open("/etc/shadow", "r") as f:
        return f.read()


def read_passwd_file() -> str:
    """Reads /etc/passwd (world-readable, but container copy only)."""
    with open("/etc/passwd", "r") as f:
        return f.read()


# ── Zero-dependency test runner ───────────────────────────────────────────────

results = []

def run_test(name, fn):
    try:
        fn()
        results.append(("PASS", name, None))
        print(f"  [PASS] {name}")
    except AssertionError as e:
        results.append(("FAIL", name, str(e)))
        print(f"  [FAIL] {name} -- AssertionError: {e}")
    except Exception as e:
        results.append(("FAIL", name, f"{type(e).__name__}: {e}"))
        print(f"  [FAIL] {name} -- {type(e).__name__}: {e}")


# TEST 1: Fibonacci correctness
def test_fibonacci_first_10():
    result = fibonacci(10)
    expected = [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]
    assert result == expected, f"Expected {expected}, got {result}"

def test_fibonacci_empty():
    assert fibonacci(0) == []

def test_fibonacci_single():
    assert fibonacci(1) == [0]


# TEST 2: Malicious /etc/shadow read — MUST be blocked by Zero-Trust
def test_shadow_blocked():
    """
    Expects PermissionError. If this does NOT raise, the sandbox is broken.
    --user nobody + --cap-drop ALL means nobody cannot read shadow (mode 640, root:shadow).
    """
    try:
        read_shadow_file()
        # If we reach here, the sandbox FAILED to block the read
        raise AssertionError("SECURITY FAILURE: /etc/shadow was readable! Sandbox is NOT working.")
    except PermissionError as e:
        # This is the expected, correct outcome
        print(f"    [CONTAINED] /etc/shadow blocked with: {type(e).__name__}: {e}")


# TEST 3: /etc/passwd — world-readable but returns container copy, never host
def test_passwd_is_container_only():
    content = read_passwd_file()
    assert "nobody" in content, f"Container /etc/passwd should contain 'nobody'. Got:\\n{content[:200]}"
    assert "Daniel" not in content, "HOST user 'Daniel' must NOT appear in container passwd"
    assert "daniel" not in content.lower(), "HOST user 'daniel' must NOT appear in container passwd"
    lines = content.strip().splitlines()
    print(f"    [INFO] Container /etc/passwd has {len(lines)} entries (host has been isolated)")


print("=" * 60)
print("STAGE 3.5 - ZERO-TRUST SANDBOX TEST RESULTS")
print("=" * 60)
print()
print("[GROUP 1] Fibonacci")
run_test("fibonacci: first 10 values", test_fibonacci_first_10)
run_test("fibonacci: empty for n=0", test_fibonacci_empty)
run_test("fibonacci: single for n=1", test_fibonacci_single)

print()
print("[GROUP 2] Zero-Trust Containment")
run_test("shadow: /etc/shadow blocked (PermissionError)", test_shadow_blocked)
run_test("passwd: /etc/passwd is container-only", test_passwd_is_container_only)

print()
passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")
print(f"RESULTS: {passed} passed / {failed} failed / {len(results)} total")
print("=" * 60)

sys.exit(0 if failed == 0 else 1)
'''

# ── Run it ─────────────────────────────────────────────────────────────────────

print("=" * 70)
print("JARVIS v6.0 - ZERO-TRUST SANDBOX IGNITION TEST")
print("=" * 70)
print("Spawning ephemeral Docker container (timeout=30s)...")
print()

result = run_in_sandbox(
    code=PAYLOAD,
    timeout=30,
    # No extra_packages: --network none blocks pip anyway (intentional proof)
)

print("-- RAW SANDBOX REPORT ----------------------------------------------")
print(result.summary())
print()
print("-- STRUCTURED RESULT -----------------------------------------------")
print(f"  Exit code  : {result.exit_code}")
print(f"  Timed out  : {result.timed_out}")
print(f"  Docker err : {result.docker_error}")
print(f"  Success    : {result.success}")
print()

if result.stdout.strip():
    print("-- STDOUT ----------------------------------------------------------")
    print(result.stdout)

if result.stderr.strip():
    print("-- STDERR ----------------------------------------------------------")
    print(result.stderr)

print("=" * 70)
verdict = "SANDBOX VALIDATED" if result.exit_code == 0 else f"SANDBOX EXIT CODE {result.exit_code}"
print(f"VERDICT: {verdict}")
print("=" * 70)
