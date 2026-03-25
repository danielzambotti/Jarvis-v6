from skills.structured_logger import StructuredLogger
s = StructuredLogger()
print("StructuredLogger import: OK")
print("Log file path:", s._log_file)

from skills.voice_handler import download_and_transcribe, format_voice_response
print("voice_handler import: OK")

print("All imports successful.")
