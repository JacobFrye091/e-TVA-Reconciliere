"""Teste pentru portal/export_date_firme.py, impotriva unui PostgreSQL REAL
local - vezi tests/test_migrare_pg.py pentru explicatia completa a
cluster-ului efemer necesar (127.0.0.1:54329); modulul se sare automat,
curat, daca acel cluster nu ruleaza."""
import os

import psycopg
import pytest

from etva import dbcompat
from portal import export_date_firme as export_mod

PG_HOST = "127.0.0.1"
PG_PORT = 54329
_ADMIN_DSN = f"postgresql://postgres@{PG_HOST}:{PG_PORT}/postgres"


def _pg_local_disponibil() -> bool:
    try:
        with psycopg.connect(_ADMIN_DSN, connect_timeout=1):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_local_disponibil(),
    reason="necesita un PostgreSQL local pe 127.0.0.1:54329 (vezi "
           "planning/migrare-postgres.md, sectiunea Testare)")


@pytest.fixture
def conn():
    nume = "etva_test_" + os.urandom(4).hex()
    with psycopg.connect(_ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {nume}")
        admin.execute(f"CREATE DATABASE {nume} TEMPLATE etva_template")
    dsn = f"postgresql://etva_app:etva_test@{PG_HOST}:{PG_PORT}/{nume}"
    c = dbcompat.connect(dsn)
    yield c
    c.close()
    with psycopg.connect(_ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {nume}")


def _firma(conn, cui, name="Firma Test SRL", active=True, arhivata_la=None):
    row = conn.execute(
        "INSERT INTO firms(name, cui, tip, active, arhivata_la, creat_la) "
        "VALUES(?,?,?,?,?,?) RETURNING id",
        (name, cui, "direct", active, arhivata_la, "2026-01-01T00:00:00+00:00")
    ).fetchone()
    conn.commit()
    return row["id"]


def _factura(conn, firm_id, numar=1, fgo_link_pdf=None):
    conn.execute(
        "INSERT INTO invoices(serie, numar, firm_id, firm_name, firm_cui, "
        "descriere, data_emiterii, valoare_neta, cota_tva, valoare_tva, "
        "valoare_totala, creat_de, creat_la, fgo_link_pdf) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("VML", numar, firm_id, "Firma Test SRL", "RO123", "Abonament",
         "2026-08-01T00:00:00+00:00", 100.0, 21.0, 21.0, 121.0, "sef",
         "2026-08-01T00:00:00+00:00", fgo_link_pdf))
    conn.commit()


def _contract(conn, firm_id, numar=1, stare="semnat", esemneaza_pdf=b"%PDF-fake"):
    conn.execute(
        "INSERT INTO contracts(firm_id, numar, ciclu_facturare, suma, "
        "beneficiar_denumire, beneficiar_cui, beneficiar_adresa, stare, "
        "creat_la, metoda_semnatura, esemneaza_document_pdf) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (firm_id, numar, "lunar", 100.0, "Firma Test SRL", "RO123",
         "Str. Test 1", stare, "2026-08-01T00:00:00+00:00", "esemneaza",
         esemneaza_pdf))
    conn.commit()


def test_exporta_skips_inactive_and_archived_firms(conn, tmp_path):
    activa = _firma(conn, "RO1", active=True)
    inactiva = _firma(conn, "RO2", active=False)
    arhivata = _firma(conn, "RO3", active=True, arhivata_la="2026-07-01T00:00:00+00:00")
    for numar, firm_id in enumerate((activa, inactiva, arhivata), start=1):
        _factura(conn, firm_id, numar=numar)

    rezumat = export_mod.exporta(conn, tmp_path)

    assert rezumat["firme"] == 1
    assert rezumat["facturi"] == 1
    assert list(tmp_path.iterdir()) != []
    (folder,) = tmp_path.iterdir()
    assert "RO1" in folder.name


def test_exporta_writes_xml_and_pdf_per_invoice(conn, tmp_path):
    firm_id = _firma(conn, "RO4")
    _factura(conn, firm_id, numar=1)

    rezumat = export_mod.exporta(conn, tmp_path)

    assert rezumat["facturi"] == 1
    assert rezumat["erori"] == []
    (folder,) = tmp_path.iterdir()
    fisiere = {p.name for p in (folder / "facturi").iterdir()}
    assert fisiere == {"factura-VML-1.xml", "factura-VML-1.pdf"}
    assert (folder / "facturi" / "factura-VML-1.pdf").read_bytes()[:4] == b"%PDF"


def test_exporta_only_includes_signed_contracts(conn, tmp_path):
    firm_id = _firma(conn, "RO5")
    _contract(conn, firm_id, numar=1, stare="semnat")
    _contract(conn, firm_id, numar=2, stare="in_asteptare")

    rezumat = export_mod.exporta(conn, tmp_path)

    assert rezumat["contracte"] == 1
    (folder,) = tmp_path.iterdir()
    fisiere = {p.name for p in (folder / "contracte").iterdir()}
    assert fisiere == {"contract-1.pdf"}


def test_exporta_skips_firm_with_no_documents(conn, tmp_path):
    _firma(conn, "RO6")

    rezumat = export_mod.exporta(conn, tmp_path)

    assert rezumat["firme"] == 0
    assert list(tmp_path.iterdir()) == []
