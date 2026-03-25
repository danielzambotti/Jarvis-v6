"""
core/resilience/backup_orchestrator.py — Autonomous Backup & Recovery Engine
=============================================================================
Manages the full backup lifecycle for Jarvis v6.0 runtime data:
  - workspace/     (generated artefacts, SBOM, threat models)
  - memory/        (conversation history cache)
  - knowledge_base/ (Obsidian vault — single source of truth)

Complementary to skills/backup_manager.py (source-code snapshots).
This module backs up OPERATIONAL DATA as compressed ZIPs with
integrity validation and configurable RPO enforcement.

RECOVERY TIME OBJECTIVE (RTO):  < 60 s   (restore from latest ZIP)
RECOVERY POINT OBJECTIVE (RPO):  configurable, default 72 h (keep 3 days)
BACKUP INTERVAL:                 configurable via BACKUP_INTERVAL_HOURS env var

PIPELINE
--------
  _resilience_loop() in main.py  (async background task, runs every N hours)
      │
      ▼  create_backup(rpo_hours)   → timestamped .zip in /app/backups
      │   ├─ _pg_dump_hook()        → optional DB dump inside ZIP
      │   └─ validate_backup()      → CRC + manifest check on fresh archive
      │
      ▼  enforce_rpo(rpo_hours)     → deletes ZIPs older than RPO window

  restore_backup(zip_path)         → called manually for DR drills / incidents
      │
      ▼  validate_backup()          → CRC + required-member check
      │
      ▼  extract to target paths    → atomic rename, original preserved

Governed by: ADR-012-Backup-Recovery
Depends on:  ADR-007-Autonomous-GitOps (state to protect),
             ADR-008-Observability-Stack (metrics)
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

_APP_ROOT    = Path("/app")                         # container root
_BACKUP_DIR  = _APP_ROOT / "backups"
_JARVIS_ROOT = Path(__file__).resolve().parents[2]  # C:\Jarvis (host dev)

# Directories included in every data backup (relative to _APP_ROOT in container,
# or _JARVIS_ROOT on the host)
_BACKUP_TARGETS: list[str] = ["workspace", "memory", "knowledge_base"]

# Required ZIP members — validate_backup() checks at least one of these exists
_REQUIRED_MEMBERS: frozenset[str] = frozenset({"workspace/", "memory/"})

# ZIP compression
_COMPRESSION = zipfile.ZIP_DEFLATED
_COMPRESSLEVEL = 6  # balanced speed vs size

# Backup filename pattern
_BACKUP_PREFIX = "jarvis-backup-"
_BACKUP_SUFFIX = ".zip"


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class BackupResult:
    ok: bool
    zip_path: Optional[Path] = None
    size_bytes: int = 0
    files_archived: int = 0
    validation_passed: bool = False
    error: Optional[str] = None

    def __str__(self) -> str:
        if self.ok:
            mb = self.size_bytes / (1024 * 1024)
            return (
                f"BACKUP OK — {self.zip_path.name if self.zip_path else '?'} "
                f"({mb:.1f} MB, {self.files_archived} files, "
                f"validated={'YES' if self.validation_passed else 'NO'})"
            )
        return f"BACKUP FAILED — {self.error}"


# ── BackupManager ──────────────────────────────────────────────────────────────

class BackupManager:
    """
    Orchestrates backup creation, integrity validation, restore, and RPO enforcement.
    Instantiate once via get_backup_manager(); used by the _resilience_loop in main.py.
    """

    def __init__(
        self,
        backup_dir: Path = _BACKUP_DIR,
        app_root: Path = _APP_ROOT,
    ) -> None:
        # Resolve to host path when running outside Docker
        self._backup_dir = backup_dir if backup_dir.exists() else (_JARVIS_ROOT / "backups")
        self._app_root   = app_root   if app_root.exists()   else _JARVIS_ROOT
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        logger.info("[BACKUP] BackupManager initialised. backup_dir=%s", self._backup_dir)

    # ── 1. Create backup ──────────────────────────────────────────────────

    def create_backup(self, rpo_hours: int = 72) -> BackupResult:
        """
        Archive workspace/, memory/, and knowledge_base/ into a timestamped ZIP.
        Optionally appends a PostgreSQL dump if DATABASE_URL is set.
        Validates the archive immediately after creation.
        Calls enforce_rpo() automatically to prune old backups.

        Args:
            rpo_hours: Retention window for enforce_rpo() called post-creation.

        Returns:
            BackupResult with zip_path, size, file count, and validation status.
        """
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        zip_path = self._backup_dir / f"{_BACKUP_PREFIX}{ts}{_BACKUP_SUFFIX}"

        logger.info("[BACKUP] Starting backup → %s", zip_path.name)
        files_archived = 0

        try:
            with zipfile.ZipFile(zip_path, "w", _COMPRESSION, compresslevel=_COMPRESSLEVEL) as zf:

                # Archive each target directory
                for target_name in _BACKUP_TARGETS:
                    target = self._app_root / target_name
                    if not target.exists():
                        logger.debug("[BACKUP] Target not found, skipping: %s", target)
                        continue
                    for file_path in target.rglob("*"):
                        if file_path.is_file():
                            arc_name = file_path.relative_to(self._app_root)
                            try:
                                zf.write(file_path, arc_name)
                                files_archived += 1
                            except OSError as exc:
                                logger.warning("[BACKUP] Skipping unreadable file %s: %s", file_path, exc)

                # Optional: PostgreSQL dump hook
                pg_dump_path = self._pg_dump_hook()
                if pg_dump_path and pg_dump_path.exists():
                    zf.write(pg_dump_path, pg_dump_path.name)
                    files_archived += 1
                    try:
                        pg_dump_path.unlink()
                    except OSError:
                        pass

                # Write manifest
                manifest_lines = [
                    f"timestamp: {ts}",
                    f"targets: {', '.join(_BACKUP_TARGETS)}",
                    f"files_archived: {files_archived}",
                    f"rpo_hours: {rpo_hours}",
                    f"created_by: Jarvis v6.0 BackupManager",
                ]
                zf.writestr("MANIFEST.txt", "\n".join(manifest_lines))

        except Exception as exc:
            logger.error("[BACKUP] ZIP creation failed: %s", exc)
            # Clean up partial archive
            try:
                zip_path.unlink(missing_ok=True)
            except OSError:
                pass
            return BackupResult(ok=False, error=str(exc))

        size = zip_path.stat().st_size
        logger.info("[BACKUP] Archive created: %s (%d files, %.1f MB)", zip_path.name, files_archived, size / 1e6)

        # Validate immediately
        valid, detail = self.validate_backup(zip_path)
        if not valid:
            logger.error("[BACKUP] Post-creation validation FAILED: %s", detail)

        # Prune old backups
        self.enforce_rpo(rpo_hours)

        return BackupResult(
            ok=True,
            zip_path=zip_path,
            size_bytes=size,
            files_archived=files_archived,
            validation_passed=valid,
        )

    # ── 2. Validate backup ────────────────────────────────────────────────

    def validate_backup(self, zip_path: Path) -> tuple[bool, str]:
        """
        Verify backup archive integrity:
          1. ZIP file opens without exception (not corrupt).
          2. zipfile.testzip() returns None (all CRCs pass).
          3. At least one required member directory exists in the archive.
          4. MANIFEST.txt is present.

        Returns (True, detail) on pass; (False, reason) on fail.
        """
        if not zip_path.exists():
            return False, f"Archive not found: {zip_path}"

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:

                # CRC check — testzip() returns name of first bad file, or None
                bad_file = zf.testzip()
                if bad_file:
                    return False, f"CRC failure in member: {bad_file}"

                names = set(zf.namelist())

                # Required members check
                found = {m for m in _REQUIRED_MEMBERS if any(n.startswith(m) for n in names)}
                if not found:
                    return False, (
                        f"Required members missing. Expected one of: {_REQUIRED_MEMBERS}. "
                        f"Found top-level entries: {sorted({n.split('/')[0] for n in names})[:5]}"
                    )

                # Manifest check
                if "MANIFEST.txt" not in names:
                    return False, "MANIFEST.txt missing from archive."

        except zipfile.BadZipFile as exc:
            return False, f"Corrupt ZIP: {exc}"
        except Exception as exc:
            return False, f"Validation error: {exc}"

        size_kb = zip_path.stat().st_size // 1024
        detail = f"OK — CRC clean, required members present, manifest found ({size_kb} KB)"
        logger.info("[BACKUP] Validation PASS: %s — %s", zip_path.name, detail)
        return True, detail

    # ── 3. Restore backup ─────────────────────────────────────────────────

    def restore_backup(self, zip_path: Path) -> tuple[bool, str]:
        """
        Restore system state from a validated backup archive.

        Safety protocol:
          1. Validate archive before touching any live data.
          2. Extract to a temp staging directory first.
          3. For each target directory: rename live dir to <name>.pre-restore,
             then move staged dir into place.
          4. On any failure, roll back by restoring .pre-restore dirs.

        Args:
            zip_path: Path to the backup ZIP to restore.

        Returns:
            (True, detail) on success; (False, reason) on failure.
        """
        valid, reason = self.validate_backup(zip_path)
        if not valid:
            return False, f"Restore aborted — validation failed: {reason}"

        import tempfile
        staging = Path(tempfile.mkdtemp(prefix="jarvis-restore-"))
        renamed: list[tuple[Path, Path]] = []  # (original, pre-restore-name) for rollback

        try:
            # Extract everything to staging
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(staging)

            # Atomically swap each target
            for target_name in _BACKUP_TARGETS:
                live_dir    = self._app_root / target_name
                staged_dir  = staging / target_name
                pre_restore = live_dir.with_name(f"{target_name}.pre-restore")

                if not staged_dir.exists():
                    logger.debug("[RESTORE] No staged data for %s — skipping", target_name)
                    continue

                # Rename live → .pre-restore
                if live_dir.exists():
                    if pre_restore.exists():
                        shutil.rmtree(pre_restore)
                    live_dir.rename(pre_restore)
                    renamed.append((live_dir, pre_restore))

                # Move staged → live
                shutil.move(str(staged_dir), str(live_dir))

            logger.info("[RESTORE] Restore complete from %s", zip_path.name)
            return True, (
                f"Restore complete from {zip_path.name}. "
                f"Pre-restore snapshots preserved as *.pre-restore."
            )

        except Exception as exc:
            logger.error("[RESTORE] Restore failed: %s — attempting rollback", exc)
            # Rollback: move live dirs back to their pre-restore names
            for original, pre in reversed(renamed):
                try:
                    if original.exists():
                        shutil.rmtree(original)
                    pre.rename(original)
                except OSError as rb_exc:
                    logger.error("[RESTORE] Rollback error for %s: %s", original, rb_exc)
            return False, f"Restore failed (rolled back): {exc}"

        finally:
            try:
                shutil.rmtree(staging, ignore_errors=True)
            except OSError:
                pass

    # ── 4. RPO enforcement ────────────────────────────────────────────────

    def enforce_rpo(self, rpo_hours: int = 72) -> int:
        """
        Delete backup ZIPs older than `rpo_hours` hours from the backup directory.
        Prevents unbounded disk growth on autonomous backup schedules.

        Args:
            rpo_hours: Maximum age of retained backups in hours.

        Returns:
            Number of backups deleted.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=rpo_hours)
        deleted = 0

        if not self._backup_dir.exists():
            return 0

        for zip_file in self._backup_dir.glob(f"{_BACKUP_PREFIX}*{_BACKUP_SUFFIX}"):
            try:
                mtime = datetime.fromtimestamp(zip_file.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    zip_file.unlink()
                    deleted += 1
                    logger.info(
                        "[BACKUP] RPO purge: deleted %s (age: %.1f h, limit: %d h)",
                        zip_file.name,
                        (datetime.now(timezone.utc) - mtime).total_seconds() / 3600,
                        rpo_hours,
                    )
            except OSError as exc:
                logger.warning("[BACKUP] Could not check/delete %s: %s", zip_file, exc)

        if deleted:
            logger.info("[BACKUP] RPO enforcement complete: %d archive(s) pruned.", deleted)
        return deleted

    # ── DB dump hook ──────────────────────────────────────────────────────

    def _pg_dump_hook(self) -> Optional[Path]:
        """
        Dump PostgreSQL to a SQL file in the backup directory staging area.
        Returns the dump file path on success, None if DATABASE_URL is unset
        or pg_dump is unavailable.

        Production upgrade: replace pg_dump subprocess with pg_dump via
        psycopg2 COPY or a proper backup agent (pgBackRest, Barman).
        """
        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            logger.debug("[BACKUP] DATABASE_URL not set — skipping DB dump.")
            return None

        dump_path = self._backup_dir / "_pg_dump_staging.sql"
        try:
            proc = subprocess.run(
                ["pg_dump", dsn, f"--file={dump_path}"],
                capture_output=True, text=True, timeout=120,
            )
            if proc.returncode == 0:
                logger.info("[BACKUP] pg_dump OK → %s", dump_path.name)
                return dump_path
            logger.warning("[BACKUP] pg_dump failed (exit %d): %s", proc.returncode, proc.stderr[:200])
        except FileNotFoundError:
            logger.debug("[BACKUP] pg_dump binary not found — skipping DB dump.")
        except subprocess.TimeoutExpired:
            logger.warning("[BACKUP] pg_dump timed out (120 s).")
        except Exception as exc:
            logger.error("[BACKUP] pg_dump error: %s", exc)

        return None

    # ── Convenience: list backups ─────────────────────────────────────────

    def list_backups(self) -> list[dict]:
        """Return metadata for all backups, newest first."""
        archives = sorted(
            self._backup_dir.glob(f"{_BACKUP_PREFIX}*{_BACKUP_SUFFIX}"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        result = []
        for a in archives:
            stat = a.stat()
            result.append({
                "name":        a.name,
                "path":        str(a),
                "size_mb":     round(stat.st_size / 1e6, 2),
                "created_utc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
        return result


# ── Singleton ──────────────────────────────────────────────────────────────────

_instance: Optional[BackupManager] = None


def get_backup_manager() -> BackupManager:
    global _instance
    if _instance is None:
        _instance = BackupManager()
    return _instance
