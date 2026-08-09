"""Teste pentru etva/risc_fiscal_store.py impotriva unui PostgreSQL REAL
local - verifica in special ca doua firme 'directe' diferite (client_id
NULL) NU se calca una pe alta cand evalueaza aceeasi `perioada` (coliziune
deloc teoretica: toate firmele directe raporteaza pe acelasi calendar
fiscal, ex. "2026-T3"). Acelasi bug ca la etva/cod_mappings.py (vezi
tests/test_cod_mappings_pg.py) - indexul unic vechi `idx_risc_fiscal_perioade_direct`
era doar (perioada) WHERE client_id IS NULL, fara firm_id, desi tabela e
partajata (RLS). Fixat prin idx_risc_fiscal_perioade_direct_firm (vezi
etva/pg_schema.sql), scopat pe firm_id.

Acelasi cluster local ca tests/test_migrare_pg.py (127.0.0.1:54329, rol
etva_app/etva_test, sablon etva_template cu schema din etva/pg_schema.sql
deja aplicata) - se sare automat, curat, daca nu e disponibil.
"""
import os

import psycopg
import pytest

from etva import dbcompat, risc_fiscal as rf
from etva import risc_fiscal_store as store

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
    nume = "etva_test_" + os.urandom(4).hex()
    with psycopg.connect(_ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {nume}")
        admin.execute(f"CREATE DATABASE {nume} TEMPLATE etva_template")
    yield f"postgresql://etva_app:etva_test@{PG_HOST}:{PG_PORT}/{nume}"
    with psycopg.connect(_ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {nume}")


@pytest.fixture
def conn(pg_dsn, monkeypatch):
    monkeypatch.setenv("ETVA_DB", "postgres")
    c = dbcompat.connect(pg_dsn)
    yield c
    c.close()


def _firma_directa(dsn, cui) -> int:
    with psycopg.connect(dsn) as raw:
        firm_id = raw.execute(
            "INSERT INTO firms(name, cui, tip, creat_la) VALUES(%s,%s,'direct', now()) "
            "RETURNING id", (f"Firma {cui}", cui)).fetchone()[0]
        raw.commit()
        return firm_id


def _financiar(**kw):
    base = {"capitaluri_proprii": 100.0, "datorii_totale": 10.0,
            "cifra_afaceri": 1000.0, "rezultat_net": 10.0}
    base.update(kw)
    return base


def test_doua_firme_directe_evalueaza_aceeasi_perioada_fara_sa_se_calce(conn, pg_dsn):
    f1 = _firma_directa(pg_dsn, "RO1")
    f2 = _firma_directa(pg_dsn, "RO2")
    scoped1 = dbcompat.firm_scope(conn, f1)
    scoped2 = dbcompat.firm_scope(conn, f2)

    financiar1 = _financiar(capitaluri_proprii=100.0)
    financiar2 = _financiar(capitaluri_proprii=-1.0)
    scor1 = rf.calculeaza_scor(rf.SIMPLU, financiar1)
    scor2 = rf.calculeaza_scor(rf.SIMPLU, financiar2)

    store.salveaza_perioada(scoped1, None, "2026-T3", "manual", financiar1,
                            scor1, username="sef1")
    store.salveaza_perioada(scoped2, None, "2026-T3", "manual", financiar2,
                            scor2, username="sef2")

    p1 = store.obtine_perioada(scoped1, None, "2026-T3")
    p2 = store.obtine_perioada(scoped2, None, "2026-T3")
    assert p1["capitaluri_proprii"] == 100.0 and p1["creat_de"] == "sef1"
    assert p2["capitaluri_proprii"] == -1.0 and p2["creat_de"] == "sef2"
    assert p1["clasificare"] == scor1.clasificare
    assert p2["clasificare"] == scor2.clasificare


def test_upsert_pe_firma_directa_ramane_scopat_pe_firma_proprie(conn, pg_dsn):
    """A doua salvare a firmei 2 pe aceeasi perioada trebuie sa actualizeze
    randul ei (un singur rand total pentru ea), fara sa atinga randul
    firmei 1 care a evaluat aceeasi perioada."""
    f1 = _firma_directa(pg_dsn, "RO1")
    f2 = _firma_directa(pg_dsn, "RO2")
    scoped1 = dbcompat.firm_scope(conn, f1)
    scoped2 = dbcompat.firm_scope(conn, f2)

    scor1 = rf.calculeaza_scor(rf.SIMPLU, _financiar())
    store.salveaza_perioada(scoped1, None, "2026-T3", "manual", _financiar(),
                            scor1, username="sef1")

    scor2a = rf.calculeaza_scor(rf.SIMPLU, _financiar(capitaluri_proprii=-1.0))
    rid2a = store.salveaza_perioada(
        scoped2, None, "2026-T3", "manual", _financiar(capitaluri_proprii=-1.0),
        scor2a, username="sef2")
    scor2b = rf.calculeaza_scor(rf.SIMPLU, _financiar(capitaluri_proprii=-2.0))
    rid2b = store.salveaza_perioada(
        scoped2, None, "2026-T3", "manual", _financiar(capitaluri_proprii=-2.0),
        scor2b, username="sef2b")

    assert rid2a == rid2b  # acelasi rand pentru firma 2, nu un duplicat
    assert len(store.lista_perioade(scoped2, None)) == 1
    p2 = store.obtine_perioada(scoped2, None, "2026-T3")
    assert p2["capitaluri_proprii"] == -2.0 and p2["creat_de"] == "sef2b"
    # firma 1 neschimbata de update-urile firmei 2
    p1 = store.obtine_perioada(scoped1, None, "2026-T3")
    assert p1["capitaluri_proprii"] == 100.0 and p1["creat_de"] == "sef1"


def test_firma_directa_si_client_real_coexista_pe_aceeasi_perioada(conn, pg_dsn):
    """Ramura client_id NOT NULL (deja scopata corect, client_id fiind unic
    global) coexista cu ramura NULL pentru aceeasi firma."""
    f1 = _firma_directa(pg_dsn, "RO1")
    scoped1 = dbcompat.firm_scope(conn, f1)
    with psycopg.connect(pg_dsn) as raw:
        raw.execute("SELECT set_config('app.firm_id', %s, false)", (str(f1),))
        client_id = raw.execute(
            "INSERT INTO clients(cui, name) VALUES('RO999','Client Unu') "
            "RETURNING id").fetchone()[0]
        raw.commit()

    scor_direct = rf.calculeaza_scor(rf.SIMPLU, _financiar(capitaluri_proprii=100.0))
    scor_client = rf.calculeaza_scor(rf.SIMPLU, _financiar(capitaluri_proprii=-1.0))
    store.salveaza_perioada(scoped1, None, "2026-T3", "manual",
                            _financiar(capitaluri_proprii=100.0), scor_direct,
                            username="sef1")
    store.salveaza_perioada(scoped1, client_id, "2026-T3", "manual",
                            _financiar(capitaluri_proprii=-1.0), scor_client,
                            username="sef1")

    assert store.obtine_perioada(scoped1, None, "2026-T3")["capitaluri_proprii"] == 100.0
    assert store.obtine_perioada(scoped1, client_id, "2026-T3")["capitaluri_proprii"] == -1.0
