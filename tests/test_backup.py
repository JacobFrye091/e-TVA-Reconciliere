import zipfile
from datetime import datetime, timedelta, timezone

from portal import backup


def _seed_data_dir(tmp_path):
    (tmp_path / "portal.db").write_bytes(b"portal-db-bytes")
    (tmp_path / "secret.key").write_bytes(b"secret-key-bytes")
    (tmp_path / "flask_secret.key").write_bytes(b"flask-secret-bytes")
    firms = tmp_path / "firms"
    firms.mkdir()
    (firms / "firm_1.db").write_bytes(b"firm-1-encrypted-bytes")


def _touch_backup(tmp_path, stamp: str) -> None:
    d = tmp_path / backup.BACKUP_DIRNAME
    d.mkdir(exist_ok=True)
    with zipfile.ZipFile(d / f"etva-backup-{stamp}.zip", "w"):
        pass


def test_create_backup_includes_portal_and_firm_files(tmp_path):
    _seed_data_dir(tmp_path)
    path = backup.create_backup(str(tmp_path))
    assert path.exists()
    with zipfile.ZipFile(path) as zf:
        names = {n.replace("\\", "/") for n in zf.namelist()}
    assert "portal.db" in names
    assert "secret.key" in names
    assert "flask_secret.key" in names
    assert "firms/firm_1.db" in names


def test_create_backup_does_not_include_previous_backups(tmp_path):
    _seed_data_dir(tmp_path)
    first = backup.create_backup(str(tmp_path))
    second = backup.create_backup(str(tmp_path))
    with zipfile.ZipFile(second) as zf:
        names = zf.namelist()
    assert not any(first.name in n for n in names)
    assert not any(n.startswith(backup.BACKUP_DIRNAME) for n in names)


def test_list_backups_sorted_most_recent_first(tmp_path):
    _touch_backup(tmp_path, "20260101-000000")
    _touch_backup(tmp_path, "20260103-000000")
    _touch_backup(tmp_path, "20260102-000000")
    backups = backup.list_backups(str(tmp_path))
    assert [b["nume"] for b in backups] == [
        "etva-backup-20260103-000000.zip",
        "etva-backup-20260102-000000.zip",
        "etva-backup-20260101-000000.zip",
    ]


def test_list_backups_ignores_unrelated_files(tmp_path):
    d = tmp_path / backup.BACKUP_DIRNAME
    d.mkdir()
    (d / "not-a-backup.zip").write_bytes(b"x")
    (d / "etva-backup-bad-name.zip").write_bytes(b"x")
    assert backup.list_backups(str(tmp_path)) == []


def test_prune_old_backups_keeps_most_recent_n(tmp_path):
    for i in range(1, 6):
        _touch_backup(tmp_path, f"2026010{i}-000000")
    backup.prune_old_backups(str(tmp_path), keep=2)
    remaining = {b["nume"] for b in backup.list_backups(str(tmp_path))}
    assert remaining == {"etva-backup-20260105-000000.zip",
                         "etva-backup-20260104-000000.zip"}


def test_backup_path_rejects_traversal_and_bad_names(tmp_path):
    _touch_backup(tmp_path, "20260101-000000")
    assert backup.backup_path(str(tmp_path), "../secret.key") is None
    assert backup.backup_path(str(tmp_path), "..\\secret.key") is None
    assert backup.backup_path(str(tmp_path), "portal.db") is None
    assert backup.backup_path(str(tmp_path), "etva-backup-20261231-000000.zip") is None
    resolved = backup.backup_path(str(tmp_path), "etva-backup-20260101-000000.zip")
    assert resolved is not None and resolved.exists()


def test_seconds_until_due_zero_when_no_backups_exist(tmp_path):
    assert backup._seconds_until_due(str(tmp_path)) == 0.0


def test_seconds_until_due_counts_down_from_last_backup(tmp_path):
    stamp = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%d-%H%M%S")
    _touch_backup(tmp_path, stamp)
    remaining = backup._seconds_until_due(str(tmp_path))
    expected = (backup.BACKUP_INTERVAL - timedelta(days=1)).total_seconds()
    assert abs(remaining - expected) < 5


def test_seconds_until_due_zero_when_overdue(tmp_path):
    stamp = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y%m%d-%H%M%S")
    _touch_backup(tmp_path, stamp)
    assert backup._seconds_until_due(str(tmp_path)) == 0.0
