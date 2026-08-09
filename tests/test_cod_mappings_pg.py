"""Teste pentru etva/cod_mappings.py impotriva unui PostgreSQL REAL local -
verifica in special ca doua firme 'directe' diferite (client_id NULL) NU se
calca una pe alta cand salveaza acelasi (direction, cod). Bug real: indexul
unic vechi `idx_cod_mappings_direct` era (direction, cod) WHERE client_id IS
NULL, fara firm_id, desi cod_mappings e o tabela partajata intre firme (RLS,
politica izolare_firma) - a doua firma directa care salva acelasi cod lovea
tinta ON CONFLICT a primeia si primea o eroare RLS (InsufficientPrivilege)
de la Postgres pe update-ul randului altei firme, in loc sa salveze un rand
propriu. Fixat prin idx_cod_mappings_direct_firm (vezi etva/pg_schema.sql),
scopat pe firm_id.

Acelasi cluster local ca tests/test_migrare_pg.py (127.0.0.1:54329, rol
etva_app/etva_test, sablon etva_template cu schema din etva/pg_schema.sql
deja aplicata) - se sare automat, curat, daca nu e disponibil.
"""
import os

import psycopg
import pytest

from etva import cod_mappings, dbcompat

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


def test_doua_firme_directe_salveaza_acelasi_cod_fara_sa_se_calce(conn, pg_dsn):
    f1 = _firma_directa(pg_dsn, "RO1")
    f2 = _firma_directa(pg_dsn, "RO2")
    scoped1 = dbcompat.firm_scope(conn, f1)
    scoped2 = dbcompat.firm_scope(conn, f2)

    cod_mappings.save_mapping(scoped1, None, "vanzari", "17", "14+15", "sef1")
    cod_mappings.save_mapping(scoped2, None, "vanzari", "17", "1", "sef2")

    assert cod_mappings.load_for_client(scoped1, None) == {("vanzari", "17"): "14+15"}
    assert cod_mappings.load_for_client(scoped2, None) == {("vanzari", "17"): "1"}


def test_upsert_pe_firma_directa_ramane_scopat_pe_firma_proprie(conn, pg_dsn):
    """Update-ul ulterior al firmei 2 (aceeasi (direction, cod) ca firma 1)
    trebuie sa ramana un singur rand, al firmei 2 - nu creeaza un al doilea
    rand si nu atinge randul firmei 1."""
    f1 = _firma_directa(pg_dsn, "RO1")
    f2 = _firma_directa(pg_dsn, "RO2")
    scoped1 = dbcompat.firm_scope(conn, f1)
    scoped2 = dbcompat.firm_scope(conn, f2)

    cod_mappings.save_mapping(scoped1, None, "vanzari", "17", "14+15", "sef1")
    cod_mappings.save_mapping(scoped2, None, "vanzari", "17", "1", "sef2")
    cod_mappings.save_mapping(scoped2, None, "vanzari", "17", "9", "sef2b")

    assert cod_mappings.load_for_client(scoped2, None) == {("vanzari", "17"): "9"}
    assert len(cod_mappings.list_for_client(scoped2, None)) == 1
    # firma 1 neschimbata de update-ul firmei 2
    assert cod_mappings.load_for_client(scoped1, None) == {("vanzari", "17"): "14+15"}


def test_firma_directa_si_client_real_coexista_pe_acelasi_cod(conn, pg_dsn):
    """Cei doi indecsi unici partiali (client_id NULL vs NOT NULL) raman
    independenti - ramura pentru client real nu era afectata de bug, dar
    trebuie sa continue sa functioneze dupa scoparea ramurii NULL pe firm_id."""
    f1 = _firma_directa(pg_dsn, "RO1")
    scoped1 = dbcompat.firm_scope(conn, f1)
    with psycopg.connect(pg_dsn) as raw:
        raw.execute("SELECT set_config('app.firm_id', %s, false)", (str(f1),))
        client_id = raw.execute(
            "INSERT INTO clients(cui, name) VALUES('RO999','Client Unu') "
            "RETURNING id").fetchone()[0]
        raw.commit()

    cod_mappings.save_mapping(scoped1, None, "vanzari", "17", "14+15", "sef1")
    cod_mappings.save_mapping(scoped1, client_id, "vanzari", "17", "1", "sef1")

    assert cod_mappings.load_for_client(scoped1, None) == {("vanzari", "17"): "14+15"}
    assert cod_mappings.load_for_client(scoped1, client_id) == {("vanzari", "17"): "1"}
