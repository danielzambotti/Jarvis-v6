import sys, traceback
sys.path.insert(0, 'C:\\Jarvis')

# Test 1: the exact sequence that caused the error in production
print("=== Test: KeyError env sequence ===")
try:
    from skills.os_controller import _build_search_script, _PS_FIND_IN_PATH_AND_DIRS

    # The old template is still in the file and still has the bug
    print("Old template test (direct .format):")
    try:
        formatted = _PS_FIND_IN_PATH_AND_DIRS.format(app_name="MCP-Windows")
        print("  No error (template might be fixed already)")
    except KeyError as e:
        print(f"  KeyError still present in _PS_FIND_IN_PATH_AND_DIRS: {e}")

    # The new _build_search_script should work
    print("New _build_search_script test:")
    start_menu, path_dirs = _build_search_script("MCP-Windows")
    print(f"  start_menu: {len(start_menu)} chars OK")
    print(f"  path_dirs: {len(path_dirs)} chars OK")

except Exception as e:
    traceback.print_exc()

# Test 2: the REAL bug from the log — router sends "instale MCP" to OS_COMMAND
# but the request needs WEB_SEARCH + then execute steps
print()
print("=== Test: Router for install command ===")
from router import route
msg = "Preciso que vc instale o MCP-Windows pro claude, procure como fazer na internet e execute"
result = route(msg)
print(f"route('{msg[:60]}...') = {result}")
print(f"Expected: WEB_SEARCH (since it needs to search first)")
