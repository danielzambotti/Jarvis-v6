import sys
sys.path.insert(0, 'C:\\Jarvis')
from skills.backup_manager import create_backup
ok, msg = create_backup('pre_v5_devarchitect')
print("Backup OK:", ok)
print("Path:", msg[-60:] if len(msg) > 60 else msg)
