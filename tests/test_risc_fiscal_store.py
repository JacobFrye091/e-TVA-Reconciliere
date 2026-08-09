import os
import pytest

from etva import db, risc_fiscal as rf, risc_fiscal_store as store


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(str(tmp_path / "a.db"), os.urandom(32))
    db.init_schema(c)
    yield c
    c.close()


def _financiar(**kw):
    base = {"capitaluri_proprii": 100.0, "datorii_totale": 10.0,
            "cifra_afaceri": 1000.0, "rezultat_net": 10.0}
    base.update(kw)
    return base


def test_salveaza_si_citeste_perioada_pentru_client(conn):
    scor = rf.calculeaza_scor(rf.SIMPLU, _financiar())
    rid = store.salveaza_perioada(
        conn, 7, "2026-T2", "manual", _financiar(), scor, username="sef")
    assert isinstance(rid, int)
    perioada = store.obtine_perioada(conn, 7, "2026-T2")
    assert perioada["scor_afisat"] == scor.scor_afisat
    assert perioada["clasificare"] == scor.clasificare
    assert perioada["creat_de"] == "sef"
    assert perioada["scor_detaliu"] == scor.detaliu


def test_upsert_actualizeaza_randul_existent(conn):
    scor1 = rf.calculeaza_scor(rf.SIMPLU, _financiar(capitaluri_proprii=100.0))
    rid1 = store.salveaza_perioada(
        conn, 7, "2026-T2", "manual", _financiar(capitaluri_proprii=100.0),
        scor1, username="sef")
    scor2 = rf.calculeaza_scor(rf.SIMPLU, _financiar(capitaluri_proprii=-1.0))
    rid2 = store.salveaza_perioada(
        conn, 7, "2026-T2", "manual", _financiar(capitaluri_proprii=-1.0),
        scor2, username="sef2")
    assert rid1 == rid2  # acelasi rand, nu un duplicat
    perioada = store.obtine_perioada(conn, 7, "2026-T2")
    assert perioada["clasificare"] == scor2.clasificare
    assert perioada["creat_de"] == "sef2"
    assert len(store.lista_perioade(conn, 7)) == 1


def test_scope_direct_client_id_none_izolat_de_alti_clienti(conn):
    scor = rf.calculeaza_scor(rf.SIMPLU, _financiar())
    store.salveaza_perioada(
        conn, None, "2026-T2", "manual", _financiar(), scor, username="sef")
    assert store.obtine_perioada(conn, None, "2026-T2") is not None
    assert store.obtine_perioada(conn, 7, "2026-T2") is None
    assert len(store.lista_perioade(conn, None)) == 1
    assert len(store.lista_perioade(conn, 7)) == 0


def test_perioade_diferite_pentru_acelasi_client_nu_se_calca(conn):
    scor = rf.calculeaza_scor(rf.SIMPLU, _financiar())
    store.salveaza_perioada(conn, 7, "2026-T1", "manual", _financiar(), scor,
                            username="sef")
    store.salveaza_perioada(conn, 7, "2026-T2", "manual", _financiar(), scor,
                            username="sef")
    perioade = [p["perioada"] for p in store.lista_perioade(conn, 7)]
    assert sorted(perioade) == ["2026-T1", "2026-T2"]


def test_flaguri_sectiune_b_persistate_si_decodate(conn):
    scor = rf.calculeaza_scor(
        rf.COMPLET, _financiar(), declaratii_nedepuse=0,
        obligatii_restante=False, flaguri_sectiune_b={"declarat_inactiv": True})
    store.salveaza_perioada(
        conn, 7, "2026-T2", "manual", _financiar(), scor,
        declaratii_nedepuse=0, obligatii_restante=False,
        flaguri_sectiune_b={"declarat_inactiv": True}, username="sef")
    perioada = store.obtine_perioada(conn, 7, "2026-T2")
    assert perioada["flaguri_sectiune_b"] == {"declarat_inactiv": True}
    assert perioada["clasificare"] == "ridicat"
