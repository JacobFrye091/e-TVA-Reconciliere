"""Teste pentru portal/migrare_pg.py, impotriva unui PostgreSQL REAL local.

Nu ruleaza in suita implicita (`pytest -q`, 417 teste, doar SQLite) - au
nevoie de un cluster PostgreSQL local pe 127.0.0.1:54329, cu rolul
etva_app/etva_test si o baza sablon 'etva_template' avand deja schema din
etva/pg_schema.sql (vezi planning/migrare-postgres.md, sectiunea Testare).
Daca acel cluster nu e pornit, tot modulul se sare automat (skip curat,
nu eroare) - suita principala nu depinde niciodata de Postgres fiind
pornit pe masina care ruleaza testele.
"""
import os
from pathlib import Path

import psycopg
import pytest
from cryptography.fernet import InvalidToken
from psycopg.rows import dict_row

from etva import db as fdb
from portal import db as pdb
from portal import migrare_pg as mig
from portal import security as psec

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
def pg_dsn():
    """O baza Postgres noua si goala (clonata din sablonul etva_template
    care are deja schema aplicata), curatata dupa test."""
    nume = "etva_test_" + os.urandom(4).hex()
    with psycopg.connect(_ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {nume}")
        admin.execute(f"CREATE DATABASE {nume} TEMPLATE etva_template")
    yield f"postgresql://etva_app:etva_test@{PG_HOST}:{PG_PORT}/{nume}"
    with psycopg.connect(_ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {nume}")


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "date"
    (d / "firms").mkdir(parents=True)
    return str(d)


def _adauga_firma_cu_baza_proprie(data_dir_path, sconn, secret, *, cui, name,
                                  tip="contabilitate"):
    """Creeaza o firma in portal.db + fisierul ei firm_<id>.db, gata de
    scris date per-firma (clients/reconciliations/etc). Intoarce (firm_id, fc)."""
    cur = sconn.execute(
        "INSERT INTO firms(name, cui, tip, creat_la) VALUES(?,?,?,?)",
        (name, cui, tip, "2026-01-01T00:00:00+00:00"))
    firm_id = cur.lastrowid
    key = os.urandom(32)
    sconn.execute("INSERT INTO firm_keys(firm_id, wrapped_key) VALUES(?,?)",
                  (firm_id, psec.wrap_key(secret, key)))
    sconn.commit()
    fc = fdb.open_db(os.path.join(data_dir_path, "firms", f"firm_{firm_id}.db"), key)
    fdb.init_schema(fc)
    return firm_id, fc


def test_migreaza_refuses_when_postgres_already_has_data(data_dir, pg_dsn):
    sconn = pdb.open_db(os.path.join(data_dir, "portal.db"))
    sconn.execute("INSERT INTO firms(name, cui, tip, creat_la) VALUES(?,?,?,?)",
                  ("Firma", "RO1", "direct", "2026-01-01T00:00:00+00:00"))
    sconn.commit()
    sconn.close()
    with psycopg.connect(pg_dsn) as pg:
        pg.execute("INSERT INTO firms(name, cui) VALUES('Deja acolo', 'RO0')")
        pg.commit()

    with pytest.raises(SystemExit, match="deja randuri"):
        mig.migreaza(data_dir, pg_dsn)

    with psycopg.connect(pg_dsn) as pg:
        assert pg.execute("SELECT COUNT(*) FROM firms").fetchone()[0] == 1


def test_migreaza_copies_portal_tables_preserving_ids_and_booleans(data_dir, pg_dsn):
    sconn = pdb.open_db(os.path.join(data_dir, "portal.db"))
    firm_id = sconn.execute(
        "INSERT INTO firms(name, cui, tip, active, email_verificat, creat_la) "
        "VALUES(?,?,?,?,?,?)",
        ("Firma Unu SRL", "RO123", "direct", 1, 0, "2026-01-01T00:00:00+00:00")
    ).lastrowid
    user_id = sconn.execute(
        "INSERT INTO users(username, pw_hash, is_master, active) VALUES(?,?,?,?)",
        ("sef", "hash-oarecare", 1, 1)).lastrowid
    sconn.commit()
    sconn.close()

    mig.migreaza(data_dir, pg_dsn)

    with psycopg.connect(pg_dsn, row_factory=dict_row) as pg:
        firma = pg.execute("SELECT * FROM firms WHERE id=%s", (firm_id,)).fetchone()
        assert firma["id"] == firm_id  # id-ul portalului se pastreaza exact
        assert firma["name"] == "Firma Unu SRL"
        assert firma["active"] is True
        assert firma["email_verificat"] is False
        user = pg.execute("SELECT * FROM users WHERE id=%s", (user_id,)).fetchone()
        assert user["is_master"] is True
        assert user["active"] is True


def test_migreaza_remaps_client_ids_nulls_client_id_and_skips_orphans(data_dir, pg_dsn):
    """Fisierele SQLCipher separate aveau spatii de id suprapuse - id-urile
    per-firma NU pot fi pastrate, trebuie remapate. O alocare care refera un
    client inexistent (orfan real, vazut si in productie) trebuie sarita
    fara sa opreasca migrarea. O reconciliere a unei firme 'directe'
    (client_id NULL) trebuie sa ramana NULL, nu sa arunce KeyError."""
    sconn = pdb.open_db(os.path.join(data_dir, "portal.db"))
    secret = psec.load_secret(os.path.join(data_dir, "secret.key"))
    firm_id, fc = _adauga_firma_cu_baza_proprie(
        data_dir, sconn, secret, cui="RO999", name="Firma Contabilitate SRL")
    sconn.close()

    fc.execute("INSERT INTO clients(id, cui, name) VALUES(1, 'RO1', 'Client Unu')")
    fc.execute("INSERT INTO clients(id, cui, name) VALUES(2, 'RO2', 'Client Doi')")
    fc.execute("INSERT INTO client_assignments(username, client_id) VALUES('op', 1)")
    fc.execute(  # orfan: nu exista niciun client cu id 99
        "INSERT INTO client_assignments(username, client_id) VALUES('op', 99)")
    fc.execute(
        "INSERT INTO reconciliations(id, client_id, period, created_at, created_by) "
        "VALUES(10, 2, '2026-01', '2026-01-05T00:00:00+00:00', 'op')")
    fc.execute(  # firma 'directa': reconciliere fara client
        "INSERT INTO reconciliations(id, client_id, period, created_at, created_by) "
        "VALUES(11, NULL, '2026-02', '2026-02-05T00:00:00+00:00', 'op')")
    fc.execute(
        "INSERT INTO invoices_company(reconciliation_id, partner_cui, invoice_no, "
        "date, base, vat, category) VALUES(10, 'RO2', 'F1', '2026-01-10', 100, 19, 'S')")
    fc.commit()
    fc.close()

    mig.migreaza(data_dir, pg_dsn)

    with psycopg.connect(pg_dsn, row_factory=dict_row) as pg:
        pg.execute("SELECT set_config('app.firm_id', %s, false)", (str(firm_id),))
        clienti = {r["cui"]: r["id"] for r in
                  pg.execute("SELECT id, cui FROM clients").fetchall()}
        assert set(clienti) == {"RO1", "RO2"}

        n_alocari = pg.execute(
            "SELECT COUNT(*) AS n FROM client_assignments").fetchone()["n"]
        assert n_alocari == 1  # cea catre id 99 a fost sarita

        recs = {r["period"]: r["client_id"] for r in
               pg.execute("SELECT period, client_id FROM reconciliations").fetchall()}
        assert recs["2026-01"] == clienti["RO2"]  # id-ul NOU, remapat
        assert recs["2026-02"] is None

        assert pg.execute(
            "SELECT COUNT(*) AS n FROM invoices_company").fetchone()["n"] == 1


def test_migreaza_sets_sequences_past_existing_ids(data_dir, pg_dsn):
    sconn = pdb.open_db(os.path.join(data_dir, "portal.db"))
    firm_id = sconn.execute(
        "INSERT INTO firms(name, cui, tip, creat_la) VALUES(?,?,?,?)",
        ("Firma X", "RO500", "direct", "2026-01-01T00:00:00+00:00")).lastrowid
    sconn.commit()
    sconn.close()

    mig.migreaza(data_dir, pg_dsn)

    with psycopg.connect(pg_dsn) as pg:
        noul_id = pg.execute(
            "INSERT INTO firms(name, cui) VALUES('Firma Noua', 'RO501') "
            "RETURNING id").fetchone()[0]
        assert noul_id > firm_id


def test_raport_migrare_counts_without_writing_anything(data_dir, pg_dsn):
    """Cel mai important contract al raportului: e strict read-only - poate
    fi rulat in siguranta impotriva unui mediu real, chiar cu date reale,
    fara sa lase nicio urma pe Postgres."""
    sconn = pdb.open_db(os.path.join(data_dir, "portal.db"))
    secret = psec.load_secret(os.path.join(data_dir, "secret.key"))
    firm_id, fc = _adauga_firma_cu_baza_proprie(
        data_dir, sconn, secret, cui="RO1", name="Firma Test SRL")
    sconn.close()
    fc.execute("INSERT INTO clients(id, cui, name) VALUES(1, 'RO1', 'Client Unu')")
    fc.execute("INSERT INTO client_assignments(username, client_id) VALUES('op', 1)")
    fc.commit()
    fc.close()

    raport = mig.raport_migrare(data_dir, pg_dsn)

    assert raport["portal"]["firms"] == 1
    assert raport["firme"][f"{firm_id} (Firma Test SRL, RO1)"]["clients"] == 1
    assert raport["postgres"]["are_deja_date"] is False
    assert raport["postgres"]["schema_ok"] is True
    assert raport["postgres"]["gata_de_migrare"] is True
    assert raport["avertismente"] == []

    with psycopg.connect(pg_dsn) as pg:
        assert pg.execute("SELECT COUNT(*) FROM firms").fetchone()[0] == 0
        assert pg.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == 0


def test_raport_migrare_flags_orphaned_assignments_and_missing_firm_db(data_dir, pg_dsn):
    sconn = pdb.open_db(os.path.join(data_dir, "portal.db"))
    secret = psec.load_secret(os.path.join(data_dir, "secret.key"))
    firm_id, fc = _adauga_firma_cu_baza_proprie(
        data_dir, sconn, secret, cui="RO1", name="Firma Cu Orfan SRL")
    fc.execute("INSERT INTO client_assignments(username, client_id) VALUES('op', 99)")
    fc.commit()
    fc.close()
    # a doua firma, fara fisier firm_<id>.db propriu (ex: creata dar
    # niciodata folosita) - trebuie semnalata, nu sa opreasca raportul.
    firm_id_fara_baza = sconn.execute(
        "INSERT INTO firms(name, cui, tip, creat_la) VALUES(?,?,?,?)",
        ("Firma Fara Baza SRL", "RO2", "direct", "2026-01-01T00:00:00+00:00")
    ).lastrowid
    sconn.commit()
    sconn.close()

    raport = mig.raport_migrare(data_dir, pg_dsn)

    assert any("orfane" in a for a in raport["avertismente"])
    eticheta_fara_baza = f"{firm_id_fara_baza} (Firma Fara Baza SRL, RO2)"
    assert raport["firme"][eticheta_fara_baza] == "fara baza de date proprie - ar fi sarita"


def test_raport_migrare_detects_postgres_already_seeded(data_dir, pg_dsn):
    pdb.open_db(os.path.join(data_dir, "portal.db")).close()
    with psycopg.connect(pg_dsn) as pg:
        pg.execute("INSERT INTO firms(name, cui) VALUES('Deja acolo', 'RO0')")
        pg.commit()

    raport = mig.raport_migrare(data_dir, pg_dsn)

    assert raport["postgres"]["are_deja_date"] is True
    assert raport["postgres"]["gata_de_migrare"] is False


def test_raport_migrare_reports_connection_failure_without_raising(data_dir):
    pdb.open_db(os.path.join(data_dir, "portal.db")).close()
    raport = mig.raport_migrare(
        data_dir, f"postgresql://etva_app:gresit@{PG_HOST}:{PG_PORT}/nu_exista")
    assert "eroare_conectare" in raport["postgres"]


def test_migreaza_rolls_back_everything_on_error(data_dir, pg_dsn):
    """O cheie stricata (nu doar un fisier lipsa - acela e sarit deliberat,
    vezi ramura 'fara baza de date proprie') trebuie sa opreasca migrarea
    si sa lase Postgres exact cum era - o singura tranzactie, nu jumatati
    de date migrate."""
    sconn = pdb.open_db(os.path.join(data_dir, "portal.db"))
    firm_id = sconn.execute(
        "INSERT INTO firms(name, cui, tip, creat_la) VALUES(?,?,?,?)",
        ("Firma Buna", "RO1", "direct", "2026-01-01T00:00:00+00:00")).lastrowid
    sconn.execute("INSERT INTO firm_keys(firm_id, wrapped_key) VALUES(?,?)",
                  (firm_id, b"nu-e-un-token-fernet-valid"))
    sconn.commit()
    sconn.close()
    (Path(data_dir) / "firms" / f"firm_{firm_id}.db").write_bytes(b"irelevant")

    with pytest.raises(InvalidToken):
        mig.migreaza(data_dir, pg_dsn)

    with psycopg.connect(pg_dsn) as pg:
        assert pg.execute("SELECT COUNT(*) FROM firms").fetchone()[0] == 0
