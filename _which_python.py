import sys
print("EXECUTABLE:", sys.executable)
print("VERSION:", sys.version)
try:
    import telegram
    print("PTB path:", telegram.__file__)
    import httpx
    print("httpx version:", httpx.__version__)
    print("httpx path:", httpx.__file__)
except ImportError as e:
    print("IMPORT ERROR:", e)
