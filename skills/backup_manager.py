"""
skills/backup_manager.py — Automatic Backup Engine
====================================================
Implements the Self-Improvement Engine from the Jarvis Security System Prompt:

  "NEVER overwrite working code directly.
   ALWAYS create a backup: C:/Jarvis/backups/{timestamp}/"

WHEN BACKUPS ARE CREATED:
  - Before any code modification by the CREATOR skill
  - Before any self-improvement action by the INSPECTOR skill
  - On demand: "backup my project" / "crie um backup"

BACKUP STRUCTURE:
  C:\\Jarvis\\backups\\
    2026-03-17_19-45-00\\
      main.py
      router.py
      security.py
      config.py
      skills\\
        os_controller.py
        creator.py
        web_search.py
        conversational.py
        inspector.py
        fs_manager.py
        structured_logger.py
        notion_logger.py
        voice_handler.py
      manifest.json   ← metadata: timestamp, trigger, files backed up
"""

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_JARVIS_ROOT  = Path(__file__).resolve().parent.parent
_BACKUP_ROOT  = _JARVIS_ROOT / "backups"
_MAX_BACKUPS  = 10   # rotate: keep only the 10 most recent

# Files and dirs to include in every backup
_BACKUP_TARGETS = [
    "main.py", "router.py", "security.py", "config.py",
    "requirements.txt", "skills",
]


def _rotate_old_backups() -> None:
    """Keep only the _MAX_BACKUPS most recent backup directories."""
    if not _BACKUP_ROOT.exists():
        return
    backups = sorted(
        [d for d in _BACKUP_ROOT.iterdir() if d.is_dir()],
        key=lambda d: d.name
    )
    while len(backups) > _MAX_BACKUPS:
        oldest = backups.pop(0)
        try:
            shutil.rmtree(oldest)
            logger.info("[BACKUP] Rotated out old backup: %s", oldest.name)
        except OSError as e:
            logger.warning("[BACKUP] Could not remove old backup %s: %s", oldest, e)


def create_backup(trigger: str = "manual") -> tuple[bool, str]:
    """
    Create a timestamped snapshot of all Jarvis source files.

    Args:
        trigger: What caused the backup ("manual", "pre_edit", "pre_improve")

    Returns:
        (success: bool, message: str)
    """
    ts    = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    dest  = _BACKUP_ROOT / ts
    dest.mkdir(parents=True, exist_ok=True)

    backed_up = []
    errors    = []

    for target_name in _BACKUP_TARGETS:
        src = _JARVIS_ROOT / target_name
        if not src.exists():
            continue
        dst = dest / target_name
        try:
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
            backed_up.append(target_name)
        except OSError as e:
            errors.append(f"{target_name}: {e}")
            logger.error("[BACKUP] Failed to copy %s: %s", target_name, e)

    # Write manifest
    manifest = {
        "timestamp":  ts,
        "trigger":    trigger,
        "files":      backed_up,
        "errors":     errors,
        "backup_dir": str(dest),
    }
    try:
        (dest / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
    except OSError:
        pass

    _rotate_old_backups()

    if errors:
        msg = (
            f"⚠️ Backup criado com avisos em `{dest.name}`\n"
            f"Arquivos: {len(backed_up)} OK, {len(errors)} com erro\n"
            f"Erros: {'; '.join(errors[:3])}"
        )
        return True, msg

    logger.info("[BACKUP] Created backup: %s (%d files)", ts, len(backed_up))
    return True, (
        f"✅ **Backup criado com sucesso!**\n"
        f"📁 `{dest}`\n"
        f"📊 {len(backed_up)} arquivos salvos\n"
        f"🕐 Timestamp: `{ts}`"
    )


def list_backups() -> str:
    """Return a formatted list of all available backups."""
    if not _BACKUP_ROOT.exists() or not any(_BACKUP_ROOT.iterdir()):
        return "📭 Nenhum backup encontrado em `C:\\\\Jarvis\\\\backups\\\\`."

    backups = sorted(
        [d for d in _BACKUP_ROOT.iterdir() if d.is_dir()],
        reverse=True  # newest first
    )
    lines = [f"📦 **Backups disponíveis ({len(backups)}):**\n"]
    for b in backups[:10]:
        manifest_path = b / "manifest.json"
        trigger = "manual"
        n_files = 0
        if manifest_path.exists():
            try:
                data    = json.loads(manifest_path.read_text(encoding="utf-8"))
                trigger = data.get("trigger", "manual")
                n_files = len(data.get("files", []))
            except Exception:
                pass
        lines.append(f"  • `{b.name}` — {n_files} files — trigger: {trigger}")
    return "\n".join(lines)


def execute(user_input: str) -> str:
    """Skill entry point: create a backup on demand."""
    text_lower = user_input.lower()

    if any(k in text_lower for k in ["list", "liste", "mostrar", "show", "ver backup"]):
        return list_backups()

    ok, msg = create_backup(trigger="manual")
    return msg
