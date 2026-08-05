import gzip
import io
from datetime import datetime, timezone

import pytest

from portal import backup_pg


class _FisierFals:
    """Suficient de asemanator cu un werkzeug FileStorage pentru testele
    de aici: .read()/.seek() pentru magic-number, .save(path) pentru
    salvare, .filename controlabil separat (util pentru testul de
    path-traversal)."""

    def __init__(self, continut: bytes, filename: str = "orice.sql.gz"):
        self._buf = io.BytesIO(continut)
        self.filename = filename

    def read(self, n=-1):
        return self._buf.read(n)

    def seek(self, pos):
        self._buf.seek(pos)

    def save(self, dest):
        with open(dest, "wb") as f:
            f.write(self._buf.getvalue())


def test_list_local_backups_ignora_liniile_stricate(tmp_path):
    (tmp_path / backup_pg.MANIFEST_NAME).write_text(
        "2026-08-04|186666\nlinie-stricata\n2026-08-03|182401\n", encoding="utf-8")
    out = backup_pg.list_local_backups(str(tmp_path))
    assert [b["data"] for b in out] == ["2026-08-04", "2026-08-03"]
    assert out[0]["marime_mb"] == round(186666 / 1_048_576, 2)


def test_list_local_backups_fara_manifest_returneaza_lista_goala(tmp_path):
    assert backup_pg.list_local_backups(str(tmp_path)) == []


def test_list_local_backups_sorteaza_descrescator(tmp_path):
    (tmp_path / backup_pg.MANIFEST_NAME).write_text(
        "2026-08-01|100\n2026-08-04|100\n2026-08-02|100\n", encoding="utf-8")
    out = backup_pg.list_local_backups(str(tmp_path))
    assert [b["data"] for b in out] == ["2026-08-04", "2026-08-02", "2026-08-01"]


def test_manifest_updated_at_none_cand_lipseste(tmp_path):
    assert backup_pg.manifest_updated_at(str(tmp_path)) is None


def test_manifest_updated_at_citeste_mtime(tmp_path):
    (tmp_path / backup_pg.MANIFEST_NAME).write_text("2026-08-04|100\n", encoding="utf-8")
    la = backup_pg.manifest_updated_at(str(tmp_path))
    assert la is not None
    assert (datetime.now(timezone.utc) - la).total_seconds() < 60


def test_request_restore_scrie_doua_linii(tmp_path):
    backup_pg.request_restore(str(tmp_path), "local:2026-08-04")
    continut = (tmp_path / backup_pg.RESTORE_TRIGGER_NAME).read_text(encoding="utf-8")
    linii = continut.strip("\n").split("\n")
    assert len(linii) == 2
    datetime.fromisoformat(linii[0])
    assert linii[1] == "local:2026-08-04"


def test_request_restore_upload(tmp_path):
    backup_pg.request_restore(str(tmp_path), backup_pg.SURSA_UPLOAD)
    continut = (tmp_path / backup_pg.RESTORE_TRIGGER_NAME).read_text(encoding="utf-8")
    assert continut.strip("\n").split("\n")[1] == "upload"


@pytest.mark.parametrize("sursa", [
    "", "local:../../etc/shadow", "upload; rm -rf /",
    "local:2026-8-4", "LOCAL:2026-08-04", "local:2026-08-04 ",
])
def test_request_restore_refuza_sursa_invalida(tmp_path, sursa):
    with pytest.raises(backup_pg.RestoreError):
        backup_pg.request_restore(str(tmp_path), sursa)
    assert not (tmp_path / backup_pg.RESTORE_TRIGGER_NAME).exists()


def test_save_uploaded_dump_refuza_ce_nu_e_gzip(tmp_path):
    fisier = _FisierFals(b"nu sunt gzip")
    with pytest.raises(backup_pg.RestoreError):
        backup_pg.save_uploaded_dump(str(tmp_path), fisier)
    assert not (tmp_path / backup_pg.RESTORE_UPLOAD_NAME).exists()


def test_save_uploaded_dump_foloseste_nume_fix(tmp_path):
    continut = gzip.compress(b"-- PostgreSQL database dump")
    fisier = _FisierFals(continut, filename="../../evil.sql.gz")
    dest = backup_pg.save_uploaded_dump(str(tmp_path), fisier)
    assert dest == tmp_path / backup_pg.RESTORE_UPLOAD_NAME
    assert dest.read_bytes() == continut
    assert list(tmp_path.iterdir()) == [dest]


def test_sterge_incarcare_nu_crapa_daca_lipseste(tmp_path):
    backup_pg.sterge_incarcare(str(tmp_path))  # nu ridica nimic


def test_sterge_incarcare_sterge_fisierul(tmp_path):
    dest = tmp_path / backup_pg.RESTORE_UPLOAD_NAME
    dest.write_bytes(b"x")
    backup_pg.sterge_incarcare(str(tmp_path))
    assert not dest.exists()


def test_nume_baza_din_dsn():
    assert backup_pg.nume_baza(
        "postgresql://etva_app:parola@127.0.0.1:5432/etva_testare") == "etva_testare"


@pytest.mark.parametrize("dsn", [None, "", "nu-e-un-dsn-valid", "postgresql://host/"])
def test_nume_baza_fallback(dsn):
    assert backup_pg.nume_baza(dsn) == "etva_testare"
