"""Periodic + on-demand backups of a data_dir: portal.db, every firm's
firm_*.db, and the two key files that make them readable at all (secret.key
wraps each firm's data key; flask_secret.key only signs session cookies).

A backup is a single zip so restoring means unzipping it back into a fresh
data_dir - no reassembly step, no risk of pairing a portal.db with the wrong
secret.key. That same completeness is why a backup file is exactly as
sensitive as the data_dir itself: whoever holds it can decrypt every firm's
data, so it must never be emailed unencrypted or left on a shared drive.
"""
import pathlib
import re
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
