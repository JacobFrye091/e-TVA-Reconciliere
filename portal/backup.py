"""Periodic + on-demand backups of a data_dir: portal.db, every firm's
firm_*.db, and the two key files that make them readable at all (secret.key
wraps each firm's data key; flask_secret.key only signs session cookies).

A backup is a single zip so restoring means unzipping it back into a fresh
data_dir - no reassembly step, no risk of pairing a portal.db with the wrong
secret.key. That same completeness is why a backup file is exactly as
sensitive as the data_dir itself: whoever holds it can decrypt every firm's
data, so it must never be emailed unencrypted or left on a shared drive.
"""
import os
import pathlib
import re
import tempfile
import threading
import time
import traceback
import zipfile
from datetime import datetime, timedelta, timezone

BACKUP_DIRNAME = "backups"
BACKUP_INTERVAL = timedelta(days=3)
RETRY_INTERVAL_SECONDS = 3600
KEEP_BACKUPS = 20

_NAME_RE = re.compile(r"^etva-backup-(\d{8}-\d{6})\.zip$")


class BackupError(Exception):
    pass


def _backups_dir(data_dir: str) -> pathlib.Path:
    d = pathlib.Path(data_dir) / BACKUP_DIRNAME
    d.mkdir(exist_ok=True)
    return d


def create_backup(data_dir: str) -> pathlib.Path:
    """Zips everything under data_dir except the backups folder itself.

    Called either from inside a request already holding the app's db_lock
    (the manual "creeaza backup acum" route) or from the scheduler thread,
    which must acquire that same lock itself - see start_scheduler. Without
    it, a backup could zip portal.db or a firm's db mid-write.
    """
    src = pathlib.Path(data_dir)
    out_dir = _backups_dir(data_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"etva-backup-{stamp}.zip"
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in src.rglob("*"):
            if path.is_dir() or out_dir in path.parents:
                continue
            zf.write(path, path.relative_to(src))
    return out_path


def validate_backup_zip(fisier) -> None:
    """Raises BackupError if `fisier` doesn't look like one of our own
    backups (no portal.db inside). Only reads the zip's directory listing -
    touches nothing on disk - so a caller can check this before deciding to
    commit to a destructive restore (e.g. before closing the app's live
    database connections, see restore_backup below). Rewinds `fisier` back
    to the start afterwards so it can be read again."""
    with zipfile.ZipFile(fisier) as zf:
        if "portal.db" not in zf.namelist():
            raise BackupError(
                "Fisierul nu pare un backup valid - lipseste portal.db.")
    fisier.seek(0)


def restore_backup(data_dir: str, fisier) -> None:
    """Overwrites data_dir's contents with a previously-downloaded backup
    zip - the inverse of create_backup. `fisier` is any file-like object
    zipfile can read (e.g. a Flask FileStorage from request.files).

    Extracts into a temp directory on the *same* filesystem as data_dir,
    then swaps each file into place with os.replace() - an atomic rename,
    never a truncate-in-place (which could hand a live connection a
    half-written page). This still requires portal.db and any firm_*.db
    to be closed by the caller first: POSIX allows renaming over an open
    file, but Windows' os.replace() raises PermissionError if the
    destination is open by the same process - so the caller (the
    /master/backup/restaureaza route) closes every connection before
    calling this, making "restart the server" a hard requirement
    everywhere, not a Windows-only workaround.
    """
    validate_backup_zip(fisier)
    dest_root = pathlib.Path(data_dir)
    with zipfile.ZipFile(fisier) as zf, \
         tempfile.TemporaryDirectory(dir=str(dest_root)) as tmp:
        tmp_path = pathlib.Path(tmp)
        zf.extractall(tmp_path)
        for extracted in tmp_path.rglob("*"):
            if extracted.is_dir():
                continue
            dest = dest_root / extracted.relative_to(tmp_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(extracted, dest)


def list_backups(data_dir: str) -> list[dict]:
    """Existing backups, most recent first."""
    out = []
    for path in _backups_dir(data_dir).glob("etva-backup-*.zip"):
        m = _NAME_RE.match(path.name)
        if not m:
            continue
        creat_la = datetime.strptime(
            m.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
        out.append({"nume": path.name, "creat_la": creat_la,
                    "marime_mb": round(path.stat().st_size / 1_048_576, 2)})
    out.sort(key=lambda b: b["creat_la"], reverse=True)
    return out


def prune_old_backups(data_dir: str, keep: int = KEEP_BACKUPS) -> None:
    for stale in list_backups(data_dir)[keep:]:
        (_backups_dir(data_dir) / stale["nume"]).unlink(missing_ok=True)


def backup_path(data_dir: str, nume: str) -> "pathlib.Path | None":
    """Resolves a backup filename to its path on disk, or None if it isn't
    an exact match for our own naming pattern - `nume` comes straight from
    a URL path segment, so this is what stands between it and path
    traversal (e.g. requesting "../secret.key")."""
    if not _NAME_RE.match(nume):
        return None
    path = _backups_dir(data_dir) / nume
    return path if path.exists() else None


def _seconds_until_due(data_dir: str) -> float:
    existing = list_backups(data_dir)
    if not existing:
        return 0.0
    due = existing[0]["creat_la"] + BACKUP_INTERVAL
    return max(0.0, (due - datetime.now(timezone.utc)).total_seconds())


def start_scheduler(data_dir: str, lock) -> None:
    """Starts a daemon thread that creates a backup every BACKUP_INTERVAL,
    timed from whenever the last backup on disk actually happened (not from
    process start) so a server restart never resets the clock or doubles
    up. A failed attempt (disk full, permissions) retries in an hour rather
    than waiting a full 3 days, but never crashes the thread outright."""
    def _loop():
        while True:
            time.sleep(_seconds_until_due(data_dir))
            try:
                with lock:
                    create_backup(data_dir)
                prune_old_backups(data_dir)
            except Exception:
                traceback.print_exc()
                time.sleep(RETRY_INTERVAL_SECONDS)

    threading.Thread(target=_loop, daemon=True).start()
