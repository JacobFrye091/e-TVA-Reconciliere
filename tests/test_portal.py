import os
import re

import pytest
from portal.app import create_app
from portal import security as psec
from portal import backup_pg
from etva import anaf_cui
from etva import esemneaza
from etva import fgo

# ETVA_TEST_PG=1 ruleaza intreaga suita din acest fisier impotriva unui
# Postgres local real, in loc de SQLite - vezi planning/migrare-postgres.md.
# Cere acelasi cluster local (127.0.0.1:54329, rol etva_app, sablon
# etva_template) ca tests/test_migrare_pg.py; fara acel cluster, aceasta
# variabila nu trebuie setata (suita implicita ramane pe SQLite).
_TEST_PG_HOST, _TEST_PG_PORT = "127.0.0.1", 54329
_TEST_PG_ADMIN_DSN = f"postgresql://postgres@{_TEST_PG_HOST}:{_TEST_PG_PORT}/postgres"

# Backup/restaurare ramane exclusiv SQLite pana la Faza 5 (pg_dump - vezi
# planning/migrare-postgres.md) - pe Postgres, restaureaza_backup() refuza
# explicit orice incercare inainte sa ajunga la logica pe care o testeaza
# aceste cazuri, iar zipul creat de create_backup() nu are ce sa contina
# (nu exista portal.db pe disc).
doar_sqlite = pytest.mark.skipif(
    os.environ.get("ETVA_TEST_PG") == "1",
    reason="backup/restaurare ramane exclusiv SQLite pana la Faza 5 (pg_dump)")


@pytest.fixture
def app(tmp_path, monkeypatch):
    nume_pg = None
    if os.environ.get("ETVA_TEST_PG") == "1":
        import psycopg
        nume_pg = "etva_test_" + os.urandom(4).hex()
        with psycopg.connect(_TEST_PG_ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {nume_pg}")
            admin.execute(f"CREATE DATABASE {nume_pg} TEMPLATE etva_template")
        monkeypatch.setenv("ETVA_DB", "postgres")
        monkeypatch.setenv(
            "DATABASE_URL",
            f"postgresql://etva_app:etva_test@{_TEST_PG_HOST}:{_TEST_PG_PORT}/{nume_pg}")

    a = create_app(str(tmp_path))
    a.config["TESTING"] = True
    # Practica standard flask-wtf pentru teste: restul suitei posteaza
    # formulare fara sa obtina intai un token dintr-un GET, ca sa nu
    # trebuiasca rescrise sute de teste existente. Protectia CSRF reala e
    # verificata separat, cu WTF_CSRF_ENABLED=True explicit - vezi
    # test_csrf_*.
    a.config["WTF_CSRF_ENABLED"] = False
    # Contractele sunt dezactivate implicit in productie (vezi
    # app_module.CONTRACTE_ACTIVE) - dar codul ramane complet, asa ca
    # majoritatea testelor il verifica activ; comportamentul "dezactivat"
    # (implicit real) are propriile teste, vezi test_contracte_dezactivate_*.
    import portal.app as app_module
    monkeypatch.setattr(app_module, "CONTRACTE_ACTIVE", True)
    # Plata e oprita temporar implicit in productie (vezi app_module.
    # PLATA_ACTIVA) - dar codul ramane complet, asa ca majoritatea testelor
    # o verifica activa; comportamentul "dezactivat" (implicit real) are
    # propriile teste, vezi test_plata_dezactivata_*.
    monkeypatch.setattr(app_module, "PLATA_ACTIVA", True)
    yield a

    if nume_pg:
        import psycopg
        # Conexiunea de rezerva a aplicatiei (portal_conn) si toate
        # conexiunile din pool-ul per-cerere trebuie inchise explicit -
        # altfel DROP DATABASE esueaza cu "is being accessed by other
        # users", fiindca raman deschise pana la garbage collection.
        a.portal_conn.close()
        a.db_pool.close()
        with psycopg.connect(_TEST_PG_ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {nume_pg}")


@pytest.fixture(autouse=True)
def _mock_anaf_cui(monkeypatch):
    """Tests don't hit the real ANAF service: default to "CUI exists"."""
    def _fake(cui, on_date=None):
        return {"cui": anaf_cui.normalize_cui(cui), "denumire": "Firma Test",
                "adresa": "", "stare_inregistrare": "INREGISTRAT",
                "scpTVA": True}
    monkeypatch.setattr(anaf_cui, "verify_cui", _fake)


@pytest.fixture(autouse=True)
def _mock_anaf_bilant(monkeypatch):
    """Serviciul public de bilant al ANAF nu e atins niciodata din teste.
    Implicit: "firma n-are bilant depus" (None) - testele care au nevoie de
    date reale suprascriu explicit, ca sa fie evident in fiecare test de
    unde vin cifrele."""
    from etva import anaf_bilant
    monkeypatch.setattr(anaf_bilant, "extrage_bilant", lambda cui, **kw: None)
    monkeypatch.setattr(anaf_bilant, "extrage_istoric", lambda cui, **kw: [])


@pytest.fixture(autouse=True)
def _mock_esemneaza(monkeypatch):
    """Contractele se semneaza prin eSemneaza.ro (nu mai exista semnatura
    desenata cu mouse-ul) - testele nu ating serviciul real. Implicit,
    orice cerere de semnare e imediat raportata ca aplicata (APPLIED) la
    prima verificare, ca fluxul de plata/facturare sa poata fi testat fara
    sa depinda de un webhook sau de asteptare reala - teste specifice
    (vezi test_semneaza_contract_esemneaza_*) suprascriu comportamentul
    pentru a verifica starile de asteptare/refuz. Modulul insusi (apeluri
    HTTP reale mockuite) are propriile teste in tests/test_esemneaza.py."""
    import portal.app as app_module
    monkeypatch.setattr(app_module, "ESEMNEAZA_API_KEY", "test-key")
    monkeypatch.setattr(esemneaza, "upload_document",
                        lambda *a, **kw: "fake-file.pdf")
    monkeypatch.setattr(esemneaza, "create_sign_request",
                        lambda *a, **kw: {"id": "fake-request-id",
                                          "status": "IN_PROGRESS"})
    monkeypatch.setattr(esemneaza, "get_sign_request", lambda *a, **kw: {
        "status": "COMPLETED",
        "recipients": [
            {"order": 1, "sigStatus": esemneaza.SIGSTATUS_APPLIED},
            {"order": 2, "sigStatus": esemneaza.SIGSTATUS_APPLIED}]})
    monkeypatch.setattr(esemneaza, "get_completed_document_url",
                        lambda *a, **kw: {"docUrl": "https://fake/doc"})
    monkeypatch.setattr(esemneaza, "get_certificate_download_url",
                        lambda *a, **kw: {"certificateUrl": "https://fake/cert"})
    monkeypatch.setattr(esemneaza, "fetch_url_bytes",
                        lambda url: b"%PDF-fake-signed-bytes")
    monkeypatch.setattr(esemneaza, "cancel_sign_request",
                        lambda *a, **kw: {"status": "CANCELLED"})


@pytest.fixture(autouse=True)
def _mock_fgo(monkeypatch):
    """Facturarea proprie (creeaza_factura/valideaza_plata) trece prin FGO
    (etva/fgo.py) - testele nu ating serviciul real. Raspuns structural
    valid, cu Numar incrementat per apel (fiecare test porneste cu propriul
    app/DB, deci nu conteaza consistenta intre teste). Teste specifice care
    vor sa verifice tratarea erorii (fgo.FgoError) suprascriu explicit
    fgo.emite_factura."""
    contor = {"n": 0}

    def _fake_emite_factura(cod_unic, cheie_privata, platforma_url, mediu, *,
                            serie, **kw):
        contor["n"] += 1
        return {"Numar": str(contor["n"]).zfill(4), "Serie": serie,
                "Link": f"https://fgo.testuat/n/p/fake-{contor['n']}",
                "LinkPlata": None}
    monkeypatch.setattr(fgo, "emite_factura", _fake_emite_factura)


def inregistreaza(c, name="Firma Unu SRL", cui="RO111", tip="contabilitate",
                  email="test@exemplu.ro", reconcilieri_estimate=None):
    data = {
        "name": name, "cui": cui, "tip": tip, "email": email,
        "password": "ParolaLunga123!", "accept_termeni": "on"}
    # Firmele directe declara obligatoriu numarul minim estimat de
    # reconcilieri lunare (sta la baza tarifarii cu pachete extra).
    if reconcilieri_estimate is None and tip == "direct":
        reconcilieri_estimate = 10
    if reconcilieri_estimate is not None:
        data["reconcilieri_estimate"] = str(reconcilieri_estimate)
    return c.post("/inregistrare", data=data, follow_redirects=False)


def test_register_redirects_to_app(app):
    c = app.test_client()
    r = inregistreaza(c)
    assert r.status_code == 302 and "/app" in r.headers["Location"]
    r = c.get("/panou")
    assert "Firma Unu SRL".encode() in r.data


def test_register_duplicate_cui(app):
    c = app.test_client()
    inregistreaza(c)
    r = inregistreaza(c, name="Alta Firma", cui="RO111")
    assert b"CUI" in r.data


def test_login_wrong_password(app):
    c = app.test_client()
    inregistreaza(c)
    c.get("/iesire")
    r = c.post("/autentificare",
               data={"cui": "RO111", "password": "gresit"})
    assert "incorecta".encode() in r.data


def test_login_locks_out_after_max_failed_attempts(app):
    from portal import db as pdb
    c = app.test_client()
    inregistreaza(c, cui="RO120")
    c.get("/iesire")
    for _ in range(pdb.LOGIN_MAX_INCERCARI):
        c.post("/autentificare", data={"cui": "RO120", "password": "gresit"})
    r = c.post("/autentificare",
              data={"cui": "RO120", "password": "ParolaLunga123!"},
              follow_redirects=False)
    # parola corecta, dar contul e blocat - nu trebuie sa se autentifice
    assert "Prea multe incercari".encode() in r.data
    assert r.status_code == 200


def test_login_failed_attempts_below_threshold_still_allow_correct_password(app):
    from portal import db as pdb
    c = app.test_client()
    inregistreaza(c, cui="RO121")
    c.get("/iesire")
    for _ in range(pdb.LOGIN_MAX_INCERCARI - 1):
        c.post("/autentificare", data={"cui": "RO121", "password": "gresit"})
    r = c.post("/autentificare",
              data={"cui": "RO121", "password": "ParolaLunga123!"},
              follow_redirects=False)
    assert r.status_code == 302 and "/app" in r.headers["Location"]


def test_login_success_resets_failed_attempt_counter(app):
    """Un login reusit chiar sub pragul de blocare trebuie sa reseteze
    contorul - altfel un singur esec ulterior s-ar aduna peste incercarile
    vechi si ar bloca prematur un cont folosit normal."""
    from portal import db as pdb
    c = app.test_client()
    inregistreaza(c, cui="RO122")
    c.get("/iesire")
    for _ in range(pdb.LOGIN_MAX_INCERCARI - 1):
        c.post("/autentificare", data={"cui": "RO122", "password": "gresit"})
    c.post("/autentificare", data={"cui": "RO122", "password": "ParolaLunga123!"})
    c.get("/iesire")
    c.post("/autentificare", data={"cui": "RO122", "password": "gresit"})
    r = c.post("/autentificare",
              data={"cui": "RO122", "password": "ParolaLunga123!"},
              follow_redirects=False)
    assert r.status_code == 302 and "/app" in r.headers["Location"]


def test_login_lockout_is_per_identifier(app):
    from portal import db as pdb
    c1 = app.test_client()
    inregistreaza(c1, cui="RO123")
    c1.get("/iesire")
    for _ in range(pdb.LOGIN_MAX_INCERCARI):
        c1.post("/autentificare", data={"cui": "RO123", "password": "gresit"})

    c2 = app.test_client()
    r = inregistreaza(c2, cui="RO124")
    assert r.status_code == 302 and "/app" in r.headers["Location"]


def test_api_me_returns_identity(app):
    c = app.test_client()
    inregistreaza(c)
    r = c.get("/api/me")
    assert r.status_code == 200
    body = r.get_json()
    assert body["role"] == "admin" and body["firm_name"] == "Firma Unu SRL"
    assert "rapoarte.export" in body["permissions"]


def test_migrate_adds_firm_tip_column_defaulting_to_contabilitate(tmp_path):
    import sqlite3
    from portal import db as pdb

    path = str(tmp_path / "portal.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE firms(id INTEGER PRIMARY KEY, name TEXT, "
                "cui TEXT UNIQUE, active INTEGER DEFAULT 1)")
    conn.execute("INSERT INTO firms(name, cui) VALUES('Firma Veche SRL', 'RO777')")
    conn.commit()
    conn.close()

    reopened = pdb.open_db(path)
    row = reopened.execute("SELECT * FROM firms WHERE cui='RO777'").fetchone()
    assert row["tip"] == "contabilitate"


def test_migrate_adds_onboarding_flag_defaulting_to_unseen(tmp_path):
    import sqlite3
    from portal import db as pdb

    path = str(tmp_path / "portal.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, "
                "pw_hash TEXT, is_master INTEGER DEFAULT 0, active INTEGER DEFAULT 1)")
    conn.execute("INSERT INTO users(username, pw_hash) VALUES('vechi', 'x')")
    conn.commit()
    conn.close()

    reopened = pdb.open_db(path)
    row = reopened.execute("SELECT * FROM users WHERE username='vechi'").fetchone()
    assert row["onboarding_completat"] == 0


def test_migrate_adds_users_email_column(tmp_path):
    import sqlite3
    from portal import db as pdb

    path = str(tmp_path / "portal.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, "
                "pw_hash TEXT, is_master INTEGER DEFAULT 0, active INTEGER DEFAULT 1)")
    conn.execute("INSERT INTO users(username, pw_hash) VALUES('vechi', 'x')")
    conn.commit()
    conn.close()

    reopened = pdb.open_db(path)
    row = reopened.execute("SELECT * FROM users WHERE username='vechi'").fetchone()
    assert row["email"] is None  # coloana noua exista, contul vechi n-avea email


def test_migrate_adds_firms_verificare_trial_defaulting_to_already_verified(tmp_path):
    """Firmele care exista deja inainte de aceasta cerinta nu trebuie
    blocate retroactiv - email_verificat trebuie sa porneasca 1, nu 0."""
    import sqlite3
    from portal import db as pdb

    path = str(tmp_path / "portal.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE firms(id INTEGER PRIMARY KEY, name TEXT, "
                "cui TEXT UNIQUE, tip TEXT DEFAULT 'contabilitate', active INTEGER DEFAULT 1)")
    conn.execute("INSERT INTO firms(name, cui) VALUES('Firma Veche SRL', 'RO888')")
    conn.commit()
    conn.close()

    reopened = pdb.open_db(path)
    row = reopened.execute("SELECT * FROM firms WHERE cui='RO888'").fetchone()
    assert row["email_verificat"] == 1
    assert row["email_verificare_token"] is None
    assert row["ciclu_facturare"] is None


def test_migrate_contracts_drops_pdf_columns_and_backfills_beneficiar_from_firms(tmp_path):
    """Randuri dintr-o forma veche (cu continut/pdf_semnat, inainte ca
    aceste coloane sa fie eliminate - fisiere mari, inutile cand pot fi
    regenerate din date) trebuie migrate fara sa piarda identitatea
    beneficiarului - denumire/cui vin din firms (nu mai exista in randul
    vechi separat de textul inghetat), adresa nu poate fi reconstituita si
    e marcata explicit ca atare."""
    import sqlite3
    from portal import db as pdb

    path = str(tmp_path / "portal.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE firms(id INTEGER PRIMARY KEY, name TEXT, "
                "cui TEXT UNIQUE, tip TEXT DEFAULT 'contabilitate', active INTEGER DEFAULT 1)")
    conn.execute(
        "INSERT INTO firms(id, name, cui) VALUES(1, 'Firma Veche Contract SRL', 'RO999')")
    conn.execute(
        "CREATE TABLE contracts(id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "firm_id INTEGER NOT NULL, numar INTEGER NOT NULL UNIQUE, "
        "ciclu_facturare TEXT NOT NULL, suma REAL NOT NULL, "
        "continut TEXT NOT NULL, stare TEXT NOT NULL DEFAULT 'in_asteptare', "
        "creat_la TEXT NOT NULL, metoda_semnatura TEXT, pdf_semnat BLOB, "
        "semnatura_verificata INTEGER NOT NULL DEFAULT 0, "
        "semnatura_detalii TEXT, semnat_la TEXT, "
        "reziliere_solicitata_la TEXT, reziliat_la TEXT, reziliat_de TEXT, "
        "ramburs_procent REAL)")
    conn.execute(
        "INSERT INTO contracts(firm_id, numar, ciclu_facturare, suma, "
        "continut, stare, creat_la) VALUES(1, 1, 'lunar', 59.0, "
        "'text vechi inghetat', 'semnat', '2026-01-01T00:00:00+00:00')")
    conn.commit()
    conn.close()

    reopened = pdb.open_db(path)
    cols = {r["name"] for r in reopened.execute("PRAGMA table_info(contracts)")}
    assert "continut" not in cols and "pdf_semnat" not in cols
    row = reopened.execute("SELECT * FROM contracts WHERE numar=1").fetchone()
    assert row["beneficiar_denumire"] == "Firma Veche Contract SRL"
    assert row["beneficiar_cui"] == "RO999"
    assert "nepastrata" in row["beneficiar_adresa"]
    assert row["stare"] == "semnat"  # restul datelor supravietuiesc neschimbate


def test_migrate_seeds_planuri_facturare_on_first_run(tmp_path):
    from portal import db as pdb

    path = str(tmp_path / "portal.db")
    conn = pdb.open_db(path)
    preturi = pdb.get_preturi(conn)
    assert preturi["direct"] == {"lunar": 59, "6luni": 49, "an": 39}
    assert preturi["contabilitate"] == {"lunar": 25, "6luni": 20, "an": 15}


def test_migrate_does_not_reseed_planuri_facturare_after_master_edit(tmp_path):
    """Un master care a modificat deja un pret nu trebuie sa-l vada resetat
    la valoarea istorica la urmatoarea pornire a serverului."""
    from portal import db as pdb

    path = str(tmp_path / "portal.db")
    conn = pdb.open_db(path)
    pdb.set_pret(conn, "direct", "lunar", 99, "sef")
    conn.close()

    reopened = pdb.open_db(path)
    assert pdb.get_preturi(reopened)["direct"]["lunar"] == 99


def test_migrate_seeds_cota_tva_on_first_run(tmp_path):
    from portal import db as pdb

    path = str(tmp_path / "portal.db")
    conn = pdb.open_db(path)
    assert pdb.get_cota_tva(conn) == 21


def test_migrate_does_not_reseed_cota_tva_after_master_edit(tmp_path):
    """Un master care a corectat deja cota de TVA (ex: dupa o schimbare de
    lege) nu trebuie sa o vada resetata la valoarea initiala la restart."""
    from portal import db as pdb

    path = str(tmp_path / "portal.db")
    conn = pdb.open_db(path)
    pdb.set_cota_tva(conn, 23, "sef")
    conn.close()

    reopened = pdb.open_db(path)
    assert pdb.get_cota_tva(reopened) == 23


def test_migrate_setari_tva_converts_old_single_row_shape(tmp_path):
    """setari_tva a inceput ca un singur rand fixat (id=1, fara marcator
    activa) - o baza veche cu acel rand trebuie migrata, nu sterasa, in
    noua forma cu istoric."""
    import sqlite3
    from portal import db as pdb

    path = str(tmp_path / "portal.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE setari_tva(id INTEGER PRIMARY KEY CHECK (id = 1), "
        "cota_procent REAL NOT NULL, actualizat_de TEXT, actualizat_la TEXT)")
    conn.execute(
        "INSERT INTO setari_tva(id, cota_procent, actualizat_de, actualizat_la) "
        "VALUES (1, 19, 'sistem', '2025-01-01T00:00:00+00:00')")
    conn.commit()
    conn.close()

    migrated = pdb.open_db(path)
    assert pdb.get_cota_tva(migrated) == 19
    istoric = pdb.listeaza_cote_tva(migrated)
    assert len(istoric) == 1
    assert istoric[0]["activa"] == 1


def test_migrate_add_contract_prestator_semnare_adds_columns(tmp_path):
    import sqlite3
    from portal import db as pdb
    path = str(tmp_path / "old_portal.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE contracts(id INTEGER PRIMARY KEY, firm_id INTEGER, "
        "numar INTEGER, ciclu_facturare TEXT, suma REAL, "
        "beneficiar_denumire TEXT, beneficiar_cui TEXT, beneficiar_adresa TEXT, "
        "stare TEXT, creat_la TEXT, esemneaza_request_id TEXT, "
        "esemneaza_document_pdf BLOB, esemneaza_certificate_pdf BLOB)")
    conn.commit()
    conn.close()

    reopened = pdb.open_db(path)
    cols = {r["name"] for r in reopened.execute("PRAGMA table_info(contracts)")}
    assert "prestator_semnat_la" in cols
    assert "contract_xml_final" in cols


def test_setari_tva_unique_index_rejects_two_active_rows(tmp_path):
    """Indexul unic partial garanteaza la nivel de baza de date ca cel mult
    o cota poate fi activa - nu doar prin disciplina din set_cota_tva."""
    import sqlite3
    from portal import db as pdb

    path = str(tmp_path / "portal.db")
    conn = pdb.open_db(path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO setari_tva(cota_procent, activa, actualizat_de, actualizat_la) "
            "VALUES (25, TRUE, 'test', '2026-01-01T00:00:00+00:00')")


def test_set_cota_tva_keeps_history_with_only_latest_active(app):
    from portal import db as pdb
    pdb.set_cota_tva(app.portal_conn, 22, "sef")
    pdb.set_cota_tva(app.portal_conn, 23, "sef")
    istoric = pdb.listeaza_cote_tva(app.portal_conn)
    procente_active = [r["cota_procent"] for r in istoric if r["activa"]]
    assert procente_active == [23]
    assert {r["cota_procent"] for r in istoric} == {21, 22, 23}
    assert pdb.get_cota_tva(app.portal_conn) == 23


def test_activeaza_cota_tva_reactivates_old_rate(app):
    from portal import db as pdb
    id_initial = pdb.listeaza_cote_tva(app.portal_conn)[0]["id"]
    pdb.set_cota_tva(app.portal_conn, 23, "sef")
    assert pdb.get_cota_tva(app.portal_conn) == 23

    assert pdb.activeaza_cota_tva(app.portal_conn, id_initial, "sef") is True
    assert pdb.get_cota_tva(app.portal_conn) == 21
    procente_active = [r["cota_procent"] for r in pdb.listeaza_cote_tva(app.portal_conn)
                      if r["activa"]]
    assert procente_active == [21]


def test_activeaza_cota_tva_returns_false_for_missing_id(app):
    from portal import db as pdb
    assert pdb.activeaza_cota_tva(app.portal_conn, 9999, "sef") is False


def test_migrate_stops_firms_from_reusing_a_soft_deleted_id(tmp_path):
    """Reproduces the real crash: a firm gets soft-deleted (firms/user_firms
    rows removed, firm_keys kept on purpose so the encrypted database stays
    recoverable - see sterge_toate_firmele.py), then a brand new firm gets
    handed that same id back by plain INTEGER PRIMARY KEY reuse and collides
    with the still-there firm_keys row."""
    import sqlite3
    from portal import db as pdb
    from etva import dbcompat

    path = str(tmp_path / "portal.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE firms(id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                "cui TEXT UNIQUE NOT NULL, tip TEXT NOT NULL DEFAULT 'contabilitate', "
                "active INTEGER NOT NULL DEFAULT 1)")
    conn.execute("CREATE TABLE firm_keys(firm_id INTEGER PRIMARY KEY, wrapped_key BLOB NOT NULL)")
    conn.execute("CREATE TABLE user_firms(user_id INTEGER NOT NULL, firm_id INTEGER NOT NULL, "
                "role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, "
                "PRIMARY KEY (user_id, firm_id))")
    conn.execute("INSERT INTO firms(id, name, cui) VALUES (1, 'Firma Veche SRL', 'RO111')")
    conn.execute("INSERT INTO firm_keys(firm_id, wrapped_key) VALUES (1, ?)", (b"cheie",))
    conn.commit()
    conn.execute("DELETE FROM firms WHERE id=1")  # sterge_toate_firmele.py: cheia ramane
    conn.commit()
    conn.close()

    reopened = pdb.open_db(path)
    id_nou = dbcompat.insert_id(
        reopened,
        "INSERT INTO firms(name, cui) VALUES ('Firma Noua SRL', 'RO222')")
    assert id_nou != 1
    reopened.execute(  # nu mai pica cu UNIQUE constraint failed: firm_keys.firm_id
        "INSERT INTO firm_keys(firm_id, wrapped_key) VALUES (?, ?)", (id_nou, b"cheie noua"))
    reopened.commit()


def test_api_me_returns_firm_tip(app):
    c = app.test_client()
    inregistreaza(c, tip="contabilitate")
    assert c.get("/api/me").get_json()["firm_tip"] == "contabilitate"


def test_api_me_returns_onboarding_completat_false_for_new_account(app):
    c = app.test_client()
    inregistreaza(c)
    assert c.get("/api/me").get_json()["onboarding_completat"] is False


def test_onboarding_completat_endpoint_marks_it_done(app):
    c = app.test_client()
    inregistreaza(c)
    r = c.post("/api/onboarding/completat")
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert c.get("/api/me").get_json()["onboarding_completat"] is True


def test_register_auto_renames_a_duplicate_username_instead_of_rejecting(app):
    # Numele intern (folosit doar pentru afisare/audit, nu la logare) e
    # derivat din denumirea firmei - doua firme cu aceeasi denumire ciocnesc
    # exact ca inainte doi useri cu acelasi nume ales manual.
    c1 = app.test_client()
    inregistreaza(c1, name="Andrei", cui="RO111")
    c2 = app.test_client()
    r2 = inregistreaza(c2, name="Andrei", cui="RO222")
    assert r2.status_code == 302 and "/app" in r2.headers["Location"]
    assert c2.get("/api/me").get_json()["username"] == "andrei2"
    # contul original nu e afectat
    assert c1.get("/api/me").get_json()["username"] == "andrei"


def test_register_keeps_auto_renaming_through_several_collisions(app):
    inregistreaza(app.test_client(), name="Andrei", cui="RO111")
    inregistreaza(app.test_client(), name="Andrei", cui="RO222")
    c3 = app.test_client()
    r3 = inregistreaza(c3, name="Andrei", cui="RO333")
    assert c3.get("/api/me").get_json()["username"] == "andrei3"


def test_add_member_auto_renames_a_duplicate_username(app):
    c = app.test_client()
    inregistreaza(c, name="Andrei")
    r = c.post("/panou/utilizatori", data={"username": "andrei",
                                           "password": "ParolaMembru123!",
                                           "role": "junior"})
    assert r.status_code == 302
    assert "andrei2" in r.headers["Location"]
    row = app.portal_conn.execute(
        "SELECT username FROM users WHERE username != 'andrei'").fetchone()
    assert row["username"] == "andrei2"
    c.get("/iesire")
    r2 = c.post("/autentificare", data={"cui": "RO111",
                                        "password": "ParolaMembru123!"})
    assert r2.status_code == 302 and "/app" in r2.headers["Location"]


def test_login_by_cui_resolves_the_right_teammate_by_password(app):
    """Colegii aceleiasi firme impart CUI-ul la autentificare - doar
    parola ii distinge, deci fiecare trebuie sa ajunga in contul lui,
    cu rolul lui."""
    c = app.test_client()
    inregistreaza(c, name="Firma Echipa", cui="RO444")
    c.post("/panou/utilizatori", data={"username": "colega",
                                       "password": "ParolaColega123!",
                                       "role": "contabil"})
    c.get("/iesire")

    r_admin = c.post("/autentificare", data={"cui": "RO444",
                                             "password": "ParolaLunga123!"})
    assert r_admin.status_code == 302 and "/app" in r_admin.headers["Location"]
    assert c.get("/api/me").get_json()["role"] == "admin"
    c.get("/iesire")

    r_coleg = c.post("/autentificare", data={"cui": "RO444",
                                             "password": "ParolaColega123!"})
    assert r_coleg.status_code == 302 and "/app" in r_coleg.headers["Location"]
    assert c.get("/api/me").get_json()["role"] == "contabil"


def test_add_member_rejects_a_password_already_used_in_the_same_firm(app):
    c = app.test_client()
    inregistreaza(c)  # parola admin: ParolaLunga123!
    r = c.post("/panou/utilizatori", data={"username": "coleg",
                                           "password": "ParolaLunga123!",
                                           "role": "junior"},
              follow_redirects=True)
    assert "deja folosita de un alt cont".encode() in r.data
    assert app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 1


def test_master_still_logs_in_by_username_not_cui(app):
    """Master nu are firma (deci nu are CUI) - ramane singurul cont care
    foloseste numele lui de utilizator in campul de autentificare."""
    conn = app.portal_conn
    conn.execute(
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,TRUE)",
        ("sef", psec.hash_password("ParolaMaster123!")))
    conn.commit()
    c = app.test_client()
    r = c.post("/autentificare", data={"cui": "sef",
                                       "password": "ParolaMaster123!"})
    assert r.status_code == 302 and "/master" in r.headers["Location"]


def test_anaf_denumire_endpoint_returns_name_for_valid_cui(app):
    c = app.test_client()
    r = c.get("/api/anaf/denumire?cui=RO111")
    body = r.get_json()
    assert body["denumire"] == "Firma Test" and body["eroare"] is None


def test_anaf_denumire_endpoint_surfaces_unknown_cui(app, monkeypatch):
    monkeypatch.setattr(anaf_cui, "verify_cui", lambda cui, **kw: None)
    c = app.test_client()
    r = c.get("/api/anaf/denumire?cui=RO999")
    body = r.get_json()
    assert body["denumire"] is None
    assert "nu a fost gasit la ANAF" in body["eroare"]


def test_register_rejects_missing_tip(app):
    c = app.test_client()
    r = c.post("/inregistrare", data={
        "name": "Firma X SRL", "cui": "RO555",
        "password": "ParolaLunga123!"})
    assert r.status_code == 200
    assert "obligatorii".encode() in r.data
    assert not app.portal_conn.execute(
        "SELECT 1 FROM firms WHERE cui='RO555'").fetchone()


def test_register_rejects_without_accepting_terms(app):
    c = app.test_client()
    r = c.post("/inregistrare", data={
        "name": "Firma X SRL", "cui": "RO556", "tip": "contabilitate",
        "password": "ParolaLunga123!"})
    assert r.status_code == 200
    assert "Termenii".encode() in r.data
    assert not app.portal_conn.execute(
        "SELECT 1 FROM firms WHERE cui='RO556'").fetchone()


def test_legal_pages_are_served(app):
    c = app.test_client()
    for path in ("/termeni.html", "/confidentialitate.html", "/cookie-uri.html"):
        r = c.get(path)
        assert r.status_code == 200
        assert b"e-TVA Reconciliere" in r.data


def test_direct_firm_has_no_clients_at_all(app):
    """O firma directa (PFA/SRL care isi face singura calculele) nu are
    cum sa se adauge pe sine ca si client - reconciliaza direct, ca firma,
    fara niciun client implicat."""
    c = app.test_client()
    inregistreaza(c, tip="direct")
    assert c.get("/api/clients").get_json() == []


def test_contabilitate_firm_starts_with_no_clients(app):
    c = app.test_client()
    inregistreaza(c, tip="contabilitate")
    assert c.get("/api/clients").get_json() == []


def test_add_firm_direct_also_has_no_clients(app):
    c = app.test_client()
    inregistreaza(c, tip="contabilitate")
    c.post("/panou/firme",
          data={"name": "PFA Ionescu", "cui": "RO222", "tip": "direct", "reconcilieri_estimate": "10"})
    # add_firm() comuta automat pe firma noua
    assert c.get("/api/clients").get_json() == []


def test_direct_firm_rejects_adding_a_client(app):
    c = app.test_client()
    inregistreaza(c, tip="direct")
    r = c.post("/api/clients", json={"cui": "RO999", "name": "Alta Firma", "gdpr_confirmat": True})
    assert r.status_code == 403
    assert c.get("/api/clients").get_json() == []


def test_direct_firm_rejects_client_assignment(app):
    c = app.test_client()
    inregistreaza(c, tip="direct")
    r = c.post("/api/assignments", json={"username": "cineva", "client_id": 1})
    assert r.status_code == 403


def test_direct_firm_reconciles_without_a_client(app):
    """Fara client_id deloc - firma reconciliaza ca ea insasi."""
    c = app.test_client()
    inregistreaza(c, tip="direct")
    r = c.post("/api/reconciliations", data={
        "period": "2026-01",
        "company_file": (_csv(_journal()), "j.csv"),
        "anaf_file": (_csv(_journal()), "a.csv"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    rid = r.get_json()["id"]
    r2 = c.get(f"/api/reconciliations/{rid}/export")
    assert r2.status_code == 200 and r2.data[:2] == b"PK"


def test_firm_key_persists_across_app_restart(tmp_path):
    data_dir = str(tmp_path)
    app1 = create_app(data_dir)
    app1.config["WTF_CSRF_ENABLED"] = False
    c1 = app1.test_client()
    inregistreaza(c1)
    cid = c1.post("/api/clients",
                 json={"cui": "RO9", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    assert cid

    app2 = create_app(data_dir)  # simulates a server restart
    app2.config["WTF_CSRF_ENABLED"] = False
    c2 = app2.test_client()
    c2.post("/autentificare", data={"cui": "RO111",
                                    "password": "ParolaLunga123!"})
    vis = c2.get("/api/clients").get_json()
    assert [x["cui"] for x in vis] == ["RO9"]


def test_flask_secret_key_persists_across_restarts(tmp_path):
    data_dir = str(tmp_path)
    app1 = create_app(data_dir)
    app2 = create_app(data_dir)  # simulates a server restart
    assert app1.secret_key == app2.secret_key


def test_login_cookie_is_permanent_with_a_long_lifetime(app):
    c = app.test_client()
    r = inregistreaza(c)
    set_cookie = r.headers.get("Set-Cookie", "")
    assert "session=" in set_cookie
    assert "Expires=" in set_cookie or "Max-Age" in set_cookie


def test_login_session_survives_a_server_restart(tmp_path):
    """The whole point of persisting the Flask secret key: a session cookie
    issued before a restart must still be accepted after one, so a user
    isn't logged out just because the server process was restarted -
    only opening a different browser (no cookie at all) should re-prompt."""
    data_dir = str(tmp_path)
    app1 = create_app(data_dir)
    app1.config["WTF_CSRF_ENABLED"] = False
    c1 = app1.test_client()
    r = inregistreaza(c1)
    session_cookie = r.headers["Set-Cookie"].split("session=")[1].split(";")[0]

    app2 = create_app(data_dir)  # simulates a server restart
    app2.config["WTF_CSRF_ENABLED"] = False
    c2 = app2.test_client()
    c2.set_cookie("session", session_cookie)
    r2 = c2.get("/api/me")
    assert r2.status_code == 200
    assert r2.get_json()["firm_name"] == "Firma Unu SRL"


def test_member_roles_and_permissions(app):
    c = app.test_client()
    inregistreaza(c)
    c.post("/panou/utilizatori", data={"username": "junior1",
                                       "password": "ParolaJunior123!",
                                       "role": "junior"})
    c.get("/iesire")
    c.post("/autentificare", data={"cui": "RO111",
                                   "password": "ParolaJunior123!"})
    body = c.get("/api/me").get_json()
    assert body["role"] == "junior"
    assert "rapoarte.export" not in body["permissions"]


def test_master_dashboard_and_firm_toggle(app):
    conn = app.portal_conn
    conn.execute(
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,TRUE)",
        ("sef", psec.hash_password("ParolaMaster123!")))
    conn.commit()
    c_firma = app.test_client()
    inregistreaza(c_firma)

    c_master = app.test_client()
    r = c_master.post("/autentificare", data={"cui": "sef",
                                              "password": "ParolaMaster123!"})
    assert "/master" in r.headers["Location"]
    assert b"Firma Unu SRL" in c_master.get("/master").data

    firm_id = conn.execute("SELECT id FROM firms").fetchone()["id"]
    c_master.post(f"/master/firma/{firm_id}/comutare")

    assert c_firma.get("/api/me").status_code == 401


def test_master_page_warns_when_server_is_stale(app, monkeypatch):
    import portal.app as app_module
    monkeypatch.setattr(app_module.pipeline, "running_vs_current", lambda: {
        "started_commit": "abc123", "started_subject": "Old feature",
        "started_at": "2026-01-01 00:00 UTC", "current_commit": "def456",
        "stale": True})
    conn = app.portal_conn
    conn.execute(
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,TRUE)",
        ("sef", psec.hash_password("ParolaMaster123!")))
    conn.commit()
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef",
                                   "password": "ParolaMaster123!"})
    text = c.get("/master").data.decode()
    assert "repornește serverul" in text
    assert "abc123" in text and "def456" in text


def test_master_page_shows_up_to_date_server(app, monkeypatch):
    import portal.app as app_module
    monkeypatch.setattr(app_module.pipeline, "running_vs_current", lambda: {
        "started_commit": "abc123", "started_subject": "Latest feature",
        "started_at": "2026-01-01 00:00 UTC", "current_commit": "abc123",
        "stale": False})
    conn = app.portal_conn
    conn.execute(
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,TRUE)",
        ("sef", psec.hash_password("ParolaMaster123!")))
    conn.commit()
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef",
                                   "password": "ParolaMaster123!"})
    text = c.get("/master").data.decode()
    assert "Server la zi" in text
    assert "repornește serverul" not in text


def test_master_uses_app_via_internal_test_firm(app):
    """Cerut explicit: super-adminul (master) trebuie sa poata testa
    reconcilierile nelimitat. Primeste o identitate pe firma interna de
    testare - creata lenes, fara trial si fara ciclu de facturare, deci in
    afara oricarei facturari/arhivari - cu toate permisiunile."""
    conn = app.portal_conn
    conn.execute(
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,TRUE)",
        ("sef", psec.hash_password("ParolaMaster123!")))
    conn.commit()
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef",
                                   "password": "ParolaMaster123!"})
    me = c.get("/api/me").get_json()
    assert me["username"] == "sef"
    assert me["este_master"] is True
    assert me["firm_name"] == "Testare interna (master)"
    assert "reconciliere.creare" in me["permissions"]
    # firma interna nu intra in trial/facturare (fara trial_expira_la, fara
    # ciclu) si nu e legata de niciun user in user_firms
    firma = conn.execute(
        "SELECT * FROM firms WHERE cui='TESTARE-MASTER'").fetchone()
    assert firma["trial_expira_la"] is None
    assert firma["ciclu_facturare"] is None
    assert bool(firma["email_verificat"])
    assert conn.execute(
        "SELECT 1 FROM user_firms WHERE firm_id=?", (firma["id"],)).fetchone() is None
    # /app se serveste direct, fara redirect spre login/plan
    r = c.get("/app")
    assert r.status_code == 200
    # ordinea conteaza (regresie reala din productie, 2026-07-30): intai o
    # cerere DOAR de citire pe scope-ul firmei (teardown-ul face rollback,
    # care pe Postgres anula si set_config-ul de app.firm_id), abia apoi
    # scrierea - cu cache-ul vechi din dbcompat, POST-ul crapa cu
    # ''::int in politica RLS.
    assert c.get("/api/clients").get_json() == []
    # masterul poate crea clienti de test si rula prin acelasi API
    cid = c.post("/api/clients", json={
        "cui": "RO7777", "name": "Client Test Master",
        "gdpr_confirmat": True}).get_json()["id"]
    assert cid
    # un utilizator obisnuit de firma nu e afectat
    assert c.get("/api/me").get_json()["este_master"] is True


def test_master_backup_onedrive_scrie_trigger(app, tmp_path):
    """Butonul de backup la cerere: masterul scrie fisierul-semnal in
    data_dir (serviciul root il preia de acolo) si actiunea se logheaza;
    starea ultimului backup (scrisa de serviciul root) apare in panou."""
    conn = app.portal_conn
    conn.execute(
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,TRUE)",
        ("sef", psec.hash_password("ParolaMaster123!")))
    conn.commit()
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef",
                                   "password": "ParolaMaster123!"})
    r = c.post("/master/backup-onedrive", follow_redirects=True)
    assert "Backupul a pornit".encode() in r.data
    assert (tmp_path / "backup-onedrive.trigger").exists()
    actiune = conn.execute(
        "SELECT actiune FROM master_actions ORDER BY id DESC LIMIT 1"
    ).fetchone()["actiune"]
    assert actiune == "backup.onedrive_solicitat"
    # starea scrisa de serviciul root e afisata inapoi in panou
    (tmp_path / "backup-onedrive.status").write_text(
        "ok|2026-07-30T19:31:15Z|Ambele baze au fost incarcate in OneDrive.",
        encoding="utf-8")
    r = c.get("/master")
    assert "Ambele baze au fost incarcate in OneDrive.".encode() in r.data
    assert "Reușit".encode() in r.data


def test_master_backup_onedrive_doar_pentru_master(app, tmp_path):
    c = app.test_client()
    inregistreaza(c, cui="RO781")
    r = c.post("/master/backup-onedrive", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]
    assert not (tmp_path / "backup-onedrive.trigger").exists()


def test_register_rejects_unknown_cui(app, monkeypatch):
    monkeypatch.setattr(anaf_cui, "verify_cui", lambda cui, **kw: None)
    c = app.test_client()
    r = inregistreaza(c)
    assert r.status_code == 200
    assert "nu a fost gasit la ANAF".encode() in r.data
    assert not app.portal_conn.execute("SELECT 1 FROM firms").fetchone()


def test_register_surfaces_anaf_unreachable(app, monkeypatch):
    def _boom(cui, **kw):
        raise anaf_cui.AnafCuiError("timeout")
    monkeypatch.setattr(anaf_cui, "verify_cui", _boom)
    c = app.test_client()
    r = inregistreaza(c)
    assert r.status_code == 200
    assert "Nu am putut verifica CUI-ul".encode() in r.data


def test_user_can_add_second_firm_and_switch(app):
    c = app.test_client()
    inregistreaza(c)
    r = c.post("/panou/firme",
              data={"name": "Firma Doi PFA", "cui": "RO222", "tip": "direct", "reconcilieri_estimate": "10"},
              follow_redirects=True)
    assert b"Firma Doi PFA" in r.data
    # a doua firma devine activa automat
    me = c.get("/api/me").get_json()
    assert me["firm_name"] == "Firma Doi PFA"

    firm1_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO111'").fetchone()["id"]
    c.post("/panou/comutare-firma", data={"firm_id": str(firm1_id)})
    me = c.get("/api/me").get_json()
    assert me["firm_name"] == "Firma Unu SRL"


def test_add_firm_rejects_unknown_cui(app, monkeypatch):
    c = app.test_client()
    inregistreaza(c)
    monkeypatch.setattr(anaf_cui, "verify_cui", lambda cui, **kw: None)
    r = c.post("/panou/firme",
              data={"name": "Firma Fantoma", "cui": "RO333", "tip": "contabilitate"},
              follow_redirects=True)
    assert "nu a fost gasit la ANAF".encode() in r.data
    assert not app.portal_conn.execute(
        "SELECT 1 FROM firms WHERE cui='RO333'").fetchone()


def test_concurrent_registrations_do_not_corrupt_portal_db(app):
    """portal.db is one sqlite3 connection shared across request threads
    (see portal/app.py). Without serializing requests around it, two
    /inregistrare calls landing near-simultaneously can interleave their
    INSERT firms / INSERT user_firms / INSERT firm_keys sequences on the
    shared connection - producing orphaned user_firms rows (referencing
    a user_id/firm_id from a different request) and UNIQUE constraint
    crashes from lastrowid races."""
    import threading

    n = 16
    barrier = threading.Barrier(n)
    results = []
    results_lock = threading.Lock()

    def register_one(i):
        barrier.wait()
        c = app.test_client()
        r = inregistreaza(c, name=f"Firma {i}", cui=f"RO9{i:03d}")
        with results_lock:
            results.append(r.status_code)

    threads = [threading.Thread(target=register_one, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == [302] * n, results

    conn = app.portal_conn
    assert conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == n
    assert conn.execute("SELECT COUNT(*) AS n FROM firms").fetchone()["n"] == n
    assert conn.execute("SELECT COUNT(*) AS n FROM user_firms").fetchone()["n"] == n

    orphans = conn.execute(
        "SELECT uf.user_id, uf.firm_id FROM user_firms uf "
        "LEFT JOIN users u ON u.id = uf.user_id "
        "LEFT JOIN firms f ON f.id = uf.firm_id "
        "WHERE u.id IS NULL OR f.id IS NULL").fetchall()
    assert orphans == []


def test_add_firm_rejects_duplicate_cui(app):
    c = app.test_client()
    inregistreaza(c)
    r = c.post("/panou/firme",
              data={"name": "Alta Denumire", "cui": "RO111", "tip": "contabilitate"},
              follow_redirects=True)
    assert "Exista deja o firma".encode() in r.data


def test_add_firm_rejected_when_existing_firm_is_direct(app):
    """O firma/PFA directa reprezinta o singura entitate - nu are sens sa
    mai adauge alte firme pe cont (spre deosebire de o firma de
    contabilitate, care gestioneaza mai multi clienti)."""
    c = app.test_client()
    inregistreaza(c, tip="direct")
    r = c.post("/panou/firme",
              data={"name": "Firma Doi PFA", "cui": "RO222", "tip": "direct", "reconcilieri_estimate": "10"},
              follow_redirects=True)
    assert "firma/PFA directa".encode() in r.data
    assert not app.portal_conn.execute(
        "SELECT 1 FROM firms WHERE cui='RO222'").fetchone()


def test_panou_hides_add_firm_card_for_direct_firms(app):
    c = app.test_client()
    inregistreaza(c, tip="direct")
    r = c.get("/panou")
    assert "Adauga o firma".encode() not in r.data


def test_panou_shows_add_firm_card_for_contabilitate_firms(app):
    c = app.test_client()
    inregistreaza(c, tip="contabilitate")
    r = c.get("/panou")
    assert "Adauga o firma".encode() in r.data


# ---------- dev/testare/productie pipeline (master dashboard) ----------

from portal import pipeline as pl


def _seed_master(app, username="sef", password="ParolaMaster123!"):
    conn = app.portal_conn
    conn.execute(
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,TRUE)",
        (username, psec.hash_password(password)))
    conn.commit()


def test_pipeline_dashboard_requires_master(app):
    c = app.test_client()
    r = c.get("/master/pipeline")
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_pipeline_dashboard_and_promote(app, monkeypatch):
    _seed_master(app)
    # Not dependent on the real repo's disk layout (siblings may not exist
    # on whatever machine runs the suite) - explicit like the rest of this test.
    monkeypatch.setattr(pl, "local_pipeline_available", lambda: True)
    monkeypatch.setattr(pl, "branch_info", lambda env: {
        "env": env, "branch": pl.ENVIRONMENTS[env]["branch"], "exists": True,
        "path": "x", "commit": "abcd123", "subject": "test", "date": "2026-07-22"})
    monkeypatch.setattr(pl, "ahead_count", lambda s, t: 1)
    monkeypatch.setattr(pl, "can_promote", lambda s, t: True)
    monkeypatch.setattr(pl, "promote", lambda s, t: {
        "commit": "deadbeef", "pushed": True, "push_error": None})

    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c.get("/master/pipeline")
    assert r.status_code == 200 and b"Promoveaza" in r.data

    r2 = c.post("/master/pipeline/promoveaza",
               data={"source": "dev", "target": "testare"}, follow_redirects=True)
    assert "deadbeef".encode() in r2.data
    assert "GitHub".encode() in r2.data
    hist = pl.history(app.portal_conn)
    assert hist[0]["commit_hash"] == "deadbeef" and hist[0]["promoted_by"] == "sef"


def test_pipeline_dashboard_degrades_gracefully_on_a_vps_deploy(app, monkeypatch):
    """A VPS deployment is a single standalone checkout, never a sibling
    worktree of the other two environments - the dashboard must show a
    plain current-environment summary instead of the local 3-worktree
    table/promotion UI (vezi task #185)."""
    _seed_master(app)
    monkeypatch.setattr(pl, "local_pipeline_available", lambda: False)
    monkeypatch.setattr(pl, "own_environment", lambda: "productie")
    monkeypatch.setattr(pl, "running_vs_current", lambda: {
        "started_commit": "abcd123", "started_subject": "test", "started_at": "t",
        "current_commit": "abcd123", "stale": False})

    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c.get("/master/pipeline")
    assert r.status_code == 200
    assert "Productie".encode() in r.data
    assert "abcd123".encode() in r.data
    assert "Promoveaza".encode() not in r.data
    assert "Folderul nu exista".encode() not in r.data


def test_pipeline_promote_reports_when_push_fails(app, monkeypatch):
    _seed_master(app)
    monkeypatch.setattr(pl, "promote", lambda s, t: {
        "commit": "deadbeef", "pushed": False, "push_error": "no network"})
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c.post("/master/pipeline/promoveaza",
              data={"source": "dev", "target": "testare"}, follow_redirects=True)
    assert "no network".encode() in r.data
    assert "promovat local".encode() in r.data
    # local promotion still happened, so it must still be logged
    hist = pl.history(app.portal_conn)
    assert hist[0]["commit_hash"] == "deadbeef"


def test_pipeline_promote_surfaces_error(app, monkeypatch):
    _seed_master(app)
    def _boom(source, target):
        raise pl.PipelineError("nu se poate promova acum")
    monkeypatch.setattr(pl, "promote", _boom)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c.post("/master/pipeline/promoveaza",
              data={"source": "dev", "target": "testare"}, follow_redirects=True)
    assert "nu se poate promova acum".encode() in r.data
    assert pl.history(app.portal_conn) == []


def test_pipeline_promote_requires_master(app):
    c = app.test_client()
    r = c.post("/master/pipeline/promoveaza",
              data={"source": "dev", "target": "testare"})
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


# ---------- master: restart-server button (task #91) ----------

def test_restart_server_requires_master(app):
    c = app.test_client()
    r = c.post("/master/server/restart")
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_restart_server_writes_trigger_and_logs_action(app, monkeypatch, tmp_path):
    monkeypatch.setattr(pl, "own_environment", lambda: "testare")
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c.post("/master/server/restart", follow_redirects=True)
    assert r.status_code == 200
    assert "Repornire solicitata".encode() in r.data
    trigger = tmp_path / pl.RESTART_TRIGGER_NAME
    assert trigger.exists() and trigger.read_text().strip() != ""
    log = app.portal_conn.execute(
        "SELECT actiune, detalii FROM master_actions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert log["actiune"] == "server_repornire_solicitata"
    assert log["detalii"] == "testare"


def test_restart_server_blocked_outside_testare_productie(app, monkeypatch, tmp_path):
    """Only a VPS deploy has the root-owned .path unit watching for the
    trigger file - on the local dev machine (or when own_environment()
    can't be determined) writing it would silently do nothing, so the
    route refuses instead."""
    monkeypatch.setattr(pl, "own_environment", lambda: None)
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c.post("/master/server/restart", follow_redirects=True)
    assert r.status_code == 200
    assert "nu e disponibila in acest mediu".encode() in r.data
    assert not (tmp_path / pl.RESTART_TRIGGER_NAME).exists()
    assert app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM master_actions").fetchone()["n"] == 0


def test_master_page_shows_restart_button_only_in_testare_productie(app, monkeypatch):
    monkeypatch.setattr(pl, "running_vs_current", lambda: {
        "started_commit": "abc123", "started_subject": "test",
        "started_at": "2026-01-01 00:00 UTC", "current_commit": "abc123",
        "stale": False})
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    monkeypatch.setattr(pl, "own_environment", lambda: "productie")
    r = c.get("/master")
    assert "Repornește serverul".encode() in r.data

    monkeypatch.setattr(pl, "own_environment", lambda: None)
    r2 = c.get("/master")
    assert "Repornește serverul".encode() not in r2.data


# ---------- product API (in-browser app) ----------

import io
import pandas as pd


def _csv(df):
    return io.BytesIO(df.to_csv(index=False).encode())


def _journal():
    return pd.DataFrame({"cui_partener": ["RO1"], "nr_factura": ["F1"],
                         "data": ["2026-01-10"], "baza": ["100"],
                         "tva": ["19"], "categorie": ["livrari_interne"]})


def test_app_requires_login(app):
    c = app.test_client()
    assert c.get("/api/me").status_code == 401
    r = c.get("/app")
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_product_flow_in_browser(app):
    c = app.test_client()
    inregistreaza(c)
    assert c.get("/app").status_code == 200
    me = c.get("/api/me").get_json()
    assert me["firm_name"] == "Firma Unu SRL"
    cid = c.post("/api/clients",
                 json={"cui": "RO9", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    anaf = _journal(); anaf.loc[0, "baza"] = "150"
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-01",
        "company_file": (_csv(_journal()), "j.csv"),
        "anaf_file": (_csv(anaf), "a.csv"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    assert body["differences"][0]["diff_type"] == "suma_diferita"
    r = c.get(f"/api/reconciliations/{body['id']}/export")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    audit_rows = c.get("/api/audit").get_json()
    actions = [a["action"] for a in audit_rows]
    assert "reconciliere.creare" in actions and "raport.export" in actions


def test_junior_limited_in_product(app):
    c = app.test_client()
    inregistreaza(c)
    c.post("/panou/utilizatori", data={"username": "jr",
                                       "password": "ParolaJunior123!",
                                       "role": "junior"})
    c.get("/iesire")
    c.post("/autentificare", data={"cui": "RO111",
                                   "password": "ParolaJunior123!"})
    assert c.get("/api/reconciliations/1/export").status_code == 403
    assert c.post("/api/clients",
                  json={"cui": "RO2", "name": "Y", "gdpr_confirmat": True}).status_code == 403
    assert c.get("/api/clients").get_json() == []  # nimic alocat inca


def test_assignment_gives_visibility(app):
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO9", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    c.post("/panou/utilizatori", data={"username": "cont1",
                                       "password": "ParolaContabil123!",
                                       "role": "contabil"})
    c.post("/api/assignments", json={"username": "cont1", "client_id": cid})
    c.get("/iesire")
    c.post("/autentificare", data={"cui": "RO111",
                                   "password": "ParolaContabil123!"})
    vis = c.get("/api/clients").get_json()
    assert [x["cui"] for x in vis] == ["RO9"]


def test_deactivated_firm_blocks_product(app):
    conn = app.portal_conn
    conn.execute(
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,TRUE)",
        ("sef", psec.hash_password("ParolaMaster123!")))
    conn.commit()
    c = app.test_client()
    inregistreaza(c)
    firm_id = conn.execute("SELECT id FROM firms").fetchone()["id"]
    conn.execute("UPDATE firms SET active=FALSE WHERE id=?", (firm_id,))
    conn.commit()
    assert c.get("/api/me").status_code == 401


# ---------- D300-line reconciliation (real SAGA journal + ANAF PDF) ----------

import io as _io
from etva.importer.anaf_p300 import AnafP300


def _saga_vanzari_bytes():
    rows = [
        ["Exemplu Test SRL  c.f. RO111  r.c. J40/1/2026"] + [None] * 10,
        [None] * 11, [None] * 11, [None] * 11,
        [None, None, "JURNAL PENTRU VANZARI"] + [None] * 8,
        [None, None, None, None, "2026-06-01", "--", "2026-06-30"] + [None] * 4,
        [None] * 11,
        ["Nr. crt.", "Document", None, "Client/beneficiar", None, None, None,
         "Total document (inclusiv TVA)", "Baza  impozitare", "Valoare T.V.A.",
         "Referinta cod *)"],
        [None, "Data", "Numar", None, "Denumire", "Cod fiscal", None, None,
         None, None, None],
        [1, "2026-06-01", "F1", "Client X", None, "RO999", None, 1210, 1000,
         210, "2-3"],
        [None, "Intocmit", None, "Verificat", None, None, "Total", 1210, 1000,
         210, None],
        [None] * 11,
        ["Referinta cod *)", None, None, None, None, None, None,
         "Total document (inclusiv TVA)", None, "Baza  impozitare",
         "Valoare T.V.A."],
        [None, None, None, None, "Referinta"] + [None] * 6,
        [None] * 11,
        ["2-3", "Bunuri/servicii taxabile cu cota 21%", None, None, None,
         None, None, 1210, 1000, 210, None],
        [None] * 11,
        ["Pagina 1/1  SAGA C"] + [None] * 10,
    ]
    import pandas as pd
    buf = _io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, header=False, index=False, engine="openpyxl")
    buf.seek(0)
    return buf


def test_new_reconciliation_rejects_empty_period(app):
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "   ",
        "company_file": (_saga_vanzari_bytes(), "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(b"%PDF-fake"), "decont.pdf"),
    }, content_type="multipart/form-data")
    assert r.status_code == 400
    assert "perioada" in r.get_json()["errors"][0].lower()


def test_d300_line_reconciliation_via_pdf_and_saga(app, monkeypatch):
    import portal.app as app_module
    monkeypatch.setattr(app_module, "parse_p300_pdf", lambda path: AnafP300(
        company_cui="RO111", company_name="Exemplu Test SRL", period="2026-06",
        lines={"9": {"base": 1000.0, "vat": 210.0}}))

    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]

    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "company_file": (_saga_vanzari_bytes(), "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(b"%PDF-fake"), "decont.pdf"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    assert body["mode"] == "d300_lines"
    assert body["differences"] == []
    assert body["totals_company"]["9"] == {"base": 1000.0, "vat": 210.0}

    rid = body["id"]
    r2 = c.get(f"/api/reconciliations/{rid}")
    assert r2.get_json()["mode"] == "d300_lines"

    r3 = c.get(f"/api/reconciliations/{rid}/export")
    assert r3.status_code == 200 and r3.data[:2] == b"PK"


def test_d300_line_reconciliation_via_anaf_json_and_saga(app):
    import json as _json

    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]

    anaf_json = _json.dumps({
        "CIF": "111", "AN": 2026, "LUNA": 6,
        "RD9_VAL": 1000.0, "RD9_TVA": 210.0,
    }).encode()

    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "company_file": (_saga_vanzari_bytes(), "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(anaf_json), "decont.json"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    assert body["mode"] == "d300_lines"
    assert body["differences"] == []
    assert body["totals_anaf"]["9"] == {"base": 1000.0, "vat": 210.0}


def test_facturi_endpoint_returns_company_invoice_for_direct_line(app, monkeypatch):
    import portal.app as app_module
    monkeypatch.setattr(app_module, "parse_p300_pdf", lambda path: AnafP300(
        company_cui="RO111", company_name="Exemplu Test SRL", period="2026-06",
        lines={"9": {"base": 1000.0, "vat": 210.0}}))

    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "company_file": (_saga_vanzari_bytes(), "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(b"%PDF-fake"), "decont.pdf"),
    }, content_type="multipart/form-data")
    rid = r.get_json()["id"]

    rf = c.get(f"/api/reconciliations/{rid}/facturi?linie=9")
    assert rf.status_code == 200
    facturi = rf.get_json()
    assert facturi == [{"date": "2026-06-01", "invoice_no": "F1",
                        "partner_cui": "RO999", "base": 1000.0, "vat": 210.0,
                        "candidat": False, "aproximativ": False}]

    # O linie D300 valida, dar fara nicio factura clasificata pe ea.
    assert c.get(f"/api/reconciliations/{rid}/facturi?linie=24").get_json() == []
    # Linie D300 inexistenta.
    assert c.get(f"/api/reconciliations/{rid}/facturi?linie=999").status_code == 400


def test_facturi_endpoint_marks_matching_invoice_as_candidat(app):
    import json as _json
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]

    # Doua facturi pe linia 9 (cota 21%) - ANAF are precompletata doar
    # prima (100/21), a doua (60.5/12.705) e exact deltul - trebuie
    # marcata candidat, prima nu.
    company_file = _model_bytes("vanzari", [
        ("2026-06-01", "F1", "Client A", "RO111",
         100.0, 21.0, _eticheta_model("vanzari", "9")),
        ("2026-06-02", "F2", "Client B", "RO222",
         60.5, 12.705, _eticheta_model("vanzari", "9")),
    ])
    anaf_json = _json.dumps({"CIF": "111", "AN": 2026, "LUNA": 6,
                            "RD9_VAL": 100.0, "RD9_TVA": 21.0}).encode()
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "format_jurnal": "model",
        "company_file": (company_file, "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(anaf_json), "decont.json"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    rid = r.get_json()["id"]

    facturi = c.get(f"/api/reconciliations/{rid}/facturi?linie=9").get_json()
    by_doc = {f["invoice_no"]: f for f in facturi}
    assert by_doc["F1"]["candidat"] is False
    assert by_doc["F2"]["candidat"] is True


def test_facturi_suspecte_aduna_candidatii_din_toate_liniile(app):
    """Lista consolidata raspunde direct la "care facturi am de verificat",
    fara sa fie nevoie sa deschizi linie cu linie."""
    import json as _json
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    company_file = _model_bytes("vanzari", [
        ("2026-06-01", "F1", "Client A", "RO111",
         100.0, 21.0, _eticheta_model("vanzari", "9")),
        ("2026-06-02", "F2", "Client B", "RO222",
         60.5, 12.705, _eticheta_model("vanzari", "9")),
    ])
    anaf_json = _json.dumps({"CIF": "111", "AN": 2026, "LUNA": 6,
                            "RD9_VAL": 100.0, "RD9_TVA": 21.0}).encode()
    rid = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06", "format_jurnal": "model",
        "company_file": (company_file, "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(anaf_json), "decont.json"),
    }, content_type="multipart/form-data").get_json()["id"]

    d = c.get(f"/api/reconciliations/{rid}/facturi-suspecte").get_json()
    docs = {f["invoice_no"]: f for f in d["facturi"]}
    # Doar factura care explica diferenta apare, nu si cea corecta.
    assert set(docs) == {"F2"}
    assert docs["F2"]["motiv"] == "candidat"
    # Fiecare factura stie de pe ce linie vine, ca sa fie gasita in decont.
    assert docs["F2"]["line_no"] == "9"


def test_facturi_suspecte_explica_liniile_unde_anaf_are_mai_mult(app):
    """Cazul care altfel ar trece tacut: cand ANAF are mai mult decat
    jurnalul, heuristica nici nu cauta (vinovatul lipseste din jurnal), deci
    linia trebuie sa apara cu explicatie, nu sa dispara ca "fara probleme"."""
    import json as _json
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    company_file = _model_bytes("vanzari", [
        ("2026-06-01", "F1", "Client A", "RO111",
         100.0, 21.0, _eticheta_model("vanzari", "9")),
    ])
    # ANAF are 500/105, firma doar 100/21 -> delta negativa.
    anaf_json = _json.dumps({"CIF": "111", "AN": 2026, "LUNA": 6,
                            "RD9_VAL": 500.0, "RD9_TVA": 105.0}).encode()
    rid = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06", "format_jurnal": "model",
        "company_file": (company_file, "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(anaf_json), "decont.json"),
    }, content_type="multipart/form-data").get_json()["id"]

    d = c.get(f"/api/reconciliations/{rid}/facturi-suspecte").get_json()
    assert d["facturi"] == []
    assert len(d["linii_neelucidate"]) == 1
    linie = d["linii_neelucidate"][0]
    assert linie["line_no"] == "9"
    assert linie["motiv"] == "lipsa_in_jurnal"
    assert "lipsa din jurnal" in linie["explicatie"]


def test_facturi_suspecte_gol_cand_totul_corespunde(app):
    import json as _json
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    company_file = _model_bytes("vanzari", [
        ("2026-06-01", "F1", "Client A", "RO111",
         100.0, 21.0, _eticheta_model("vanzari", "9")),
    ])
    anaf_json = _json.dumps({"CIF": "111", "AN": 2026, "LUNA": 6,
                            "RD9_VAL": 100.0, "RD9_TVA": 21.0}).encode()
    rid = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06", "format_jurnal": "model",
        "company_file": (company_file, "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(anaf_json), "decont.json"),
    }, content_type="multipart/form-data").get_json()["id"]

    d = c.get(f"/api/reconciliations/{rid}/facturi-suspecte").get_json()
    assert d == {"facturi": [], "linii_neelucidate": []}


def test_facturi_suspecte_refuza_modul_pe_categorii(app):
    """In modul pe categorii diferentele sunt deja per factura, deci lista
    consolidata n-are rost - frontend-ul trateaza 404 ca "nu se aplica"."""
    import pandas as _pd
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    randuri = _pd.DataFrame({
        "cui_partener": ["RO999"], "nr_factura": ["F1"], "data": ["2026-06-01"],
        "baza": [100.0], "tva": [19.0], "categorie": ["livrari_interne"]})
    company_buf, anaf_buf = _io.BytesIO(), _io.BytesIO()
    randuri.to_csv(company_buf, index=False); company_buf.seek(0)
    randuri.to_csv(anaf_buf, index=False); anaf_buf.seek(0)

    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "company_file": (company_buf, "j.csv"),
        "anaf_file": (anaf_buf, "a.csv"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["mode"] == "invoices"
    rid = r.get_json()["id"]
    assert c.get(f"/api/reconciliations/{rid}/facturi-suspecte").status_code == 404


def test_facturi_endpoint_marks_three_invoices_as_candidat(app):
    import json as _json
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]

    # Patru facturi pe linia 9 (cota 21%) - ANAF are precompletata doar
    # prima (100/21, care NU face parte din delta - a fost aleasa deliberat
    # diferita ca suma de trio, ca sa nu se potriveasca ea insasi din
    # intamplare); celelalte trei (20/4.2, 25/5.25, 45/9.45) insumeaza
    # exact deltul (90/18.9) - toate trei trebuie marcate candidat, prima nu.
    company_file = _model_bytes("vanzari", [
        ("2026-06-01", "F1", "Client A", "RO111",
         100.0, 21.0, _eticheta_model("vanzari", "9")),
        ("2026-06-02", "F2", "Client B", "RO222",
         20.0, 4.2, _eticheta_model("vanzari", "9")),
        ("2026-06-03", "F3", "Client C", "RO333",
         25.0, 5.25, _eticheta_model("vanzari", "9")),
        ("2026-06-04", "F4", "Client D", "RO444",
         45.0, 9.45, _eticheta_model("vanzari", "9")),
    ])
    anaf_json = _json.dumps({"CIF": "111", "AN": 2026, "LUNA": 6,
                            "RD9_VAL": 100.0, "RD9_TVA": 21.0}).encode()
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "format_jurnal": "model",
        "company_file": (company_file, "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(anaf_json), "decont.json"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    rid = r.get_json()["id"]

    facturi = c.get(f"/api/reconciliations/{rid}/facturi?linie=9").get_json()
    by_doc = {f["invoice_no"]: f for f in facturi}
    assert by_doc["F1"]["candidat"] is False
    assert by_doc["F2"]["candidat"] is True
    assert by_doc["F3"]["candidat"] is True
    assert by_doc["F4"]["candidat"] is True
    # O potrivire confirmata exista deja - nu mai are sens sa mai ghicim
    # si "cea mai apropiata" pe deasupra.
    assert all(f["aproximativ"] is False for f in facturi)


def test_facturi_endpoint_marks_closest_when_no_exact_combination(app):
    import json as _json
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]

    # Cazul real din conversatie: delta 4214.96/885.48, nicio factura sau
    # combinatie de facturi nu explica exact diferenta, dar F2
    # (4214.86/885.12, la 0.10/0.36 distanta) e vizibil mai aproape decat
    # F1 - trebuie marcata "aproximativ", nu "candidat".
    company_file = _model_bytes("vanzari", [
        ("2026-06-01", "F1", "Client A", "RO111",
         360.0, 75.6, _eticheta_model("vanzari", "9")),
        ("2026-06-02", "F2", "Client B", "RO222",
         4214.86, 885.12, _eticheta_model("vanzari", "9")),
    ])
    anaf_json = _json.dumps({"CIF": "111", "AN": 2026, "LUNA": 6,
                            "RD9_VAL": 359.90, "RD9_TVA": 75.24}).encode()
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "format_jurnal": "model",
        "company_file": (company_file, "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(anaf_json), "decont.json"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    diff = next(d for d in body["differences"] if d["line_no"] == "9")
    assert diff["delta_base"] == 4214.96 and diff["delta_vat"] == 885.48
    rid = body["id"]

    facturi = c.get(f"/api/reconciliations/{rid}/facturi?linie=9").get_json()
    by_doc = {f["invoice_no"]: f for f in facturi}
    assert by_doc["F1"]["candidat"] is False and by_doc["F1"]["aproximativ"] is False
    assert by_doc["F2"]["candidat"] is False and by_doc["F2"]["aproximativ"] is True


def test_facturi_endpoint_marks_closest_pair_when_no_single_is_close(app):
    import json as _json
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]

    # Nicio factura singura nu e aproape de diferenta (100/21), dar F1+F2
    # insumeaza 100.3/21.1 - o pereche clar mai aproape decat orice alta
    # combinatie. Ambele trebuie marcate "aproximativ", F3 (filler) nu.
    company_file = _model_bytes("vanzari", [
        ("2026-06-01", "F1", "Client A", "RO111",
         40.0, 8.4, _eticheta_model("vanzari", "9")),
        ("2026-06-02", "F2", "Client B", "RO222",
         60.3, 12.7, _eticheta_model("vanzari", "9")),
        ("2026-06-03", "F3", "Client C", "RO333",
         500.0, 0.0, _eticheta_model("vanzari", "9")),
    ])
    anaf_json = _json.dumps({"CIF": "111", "AN": 2026, "LUNA": 6,
                            "RD9_VAL": 500.3, "RD9_TVA": 0.1}).encode()
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "format_jurnal": "model",
        "company_file": (company_file, "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(anaf_json), "decont.json"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    diff = next(d for d in body["differences"] if d["line_no"] == "9")
    assert diff["delta_base"] == 100.0 and diff["delta_vat"] == 21.0
    rid = body["id"]

    facturi = c.get(f"/api/reconciliations/{rid}/facturi?linie=9").get_json()
    by_doc = {f["invoice_no"]: f for f in facturi}
    assert by_doc["F1"]["candidat"] is False and by_doc["F1"]["aproximativ"] is True
    assert by_doc["F2"]["candidat"] is False and by_doc["F2"]["aproximativ"] is True
    assert by_doc["F3"]["candidat"] is False and by_doc["F3"]["aproximativ"] is False


def test_facturi_endpoint_resolves_derived_line(app):
    import json as _json
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]

    # RD20_1_VAL/TVA nenule - altfel invoices_anaf n-ar primi niciun rand
    # pentru aceasta reconciliere, iar _reconciliation_mode ar cadea pe
    # implicitul "invoices" (fara nicio linie ANAF, nu poate distinge
    # modurile) - o reconciliere reala are intotdeauna un decont cu
    # continut, deci acest caz nu apare in productie.
    anaf_json = _json.dumps({"CIF": "111", "AN": 2026, "LUNA": 6,
                            "RD20_1_VAL": 500.0, "RD20_1_TVA": 0.0}).encode()
    company_file = _model_bytes("cumparari", [
        ("2026-06-02", "FZ1", "Furnizor UE", "IE9999999X", 500, 0,
         _eticheta_model("cumparari", "20.1")),
    ])
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "format_jurnal": "model",
        "company_file": (company_file, "cumparari.xlsx"),
        "anaf_file": (_io.BytesIO(anaf_json), "decont.json"),
    }, content_type="multipart/form-data")
    rid = r.get_json()["id"]

    # Factura reala e persistata cu category="20.1" - "Vezi facturile" pe
    # oricare din liniile derivate (5, 5.1, 20) trebuie s-o gaseasca, prin
    # inchiderea tranzitiva din resolve_invoice_lines.
    for linie in ("20.1", "5.1", "20", "5"):
        facturi = c.get(f"/api/reconciliations/{rid}/facturi?linie={linie}").get_json()
        assert len(facturi) == 1 and facturi[0]["invoice_no"] == "FZ1"


def test_facturi_endpoint_404_pentru_modul_invoices(app):
    import pandas as pd
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]

    randuri = pd.DataFrame({
        "cui_partener": ["RO999"], "nr_factura": ["F1"], "data": ["2026-06-01"],
        "baza": [100.0], "tva": [19.0], "categorie": ["livrari_interne"]})
    company_buf = _io.BytesIO()
    randuri.to_csv(company_buf, index=False)
    company_buf.seek(0)
    anaf_buf = _io.BytesIO()
    randuri.to_csv(anaf_buf, index=False)
    anaf_buf.seek(0)

    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "company_file": (company_buf, "j.csv"),
        "anaf_file": (anaf_buf, "a.csv"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["mode"] == "invoices"
    rid = r.get_json()["id"]
    rf = c.get(f"/api/reconciliations/{rid}/facturi?linie=9")
    assert rf.status_code == 404


def test_d300_unmapped_codes_are_surfaced(app, monkeypatch):
    import portal.app as app_module
    monkeypatch.setattr(app_module, "parse_p300_pdf", lambda path: AnafP300(
        company_cui="RO111", company_name="Exemplu Test SRL", period="2026-06",
        lines={}))

    def _fake_saga(path):
        from etva.importer.saga import SagaJournal
        return SagaJournal(direction="vanzari", company_name="Exemplu Test SRL",
                           company_cui="RO111", entries=[],
                           legend={"99": {"label": "Cod ambiguu neclasificat",
                                          "base": 42.0, "vat": 0.0}})
    monkeypatch.setattr(app_module, "parse_saga_journal", _fake_saga)

    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "company_file": (_io.BytesIO(b"placeholder"), "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(b"%PDF-fake"), "decont.pdf"),
    }, content_type="multipart/form-data")
    body = r.get_json()
    assert body["unmapped"] == [{"cod": "99", "label": "Cod ambiguu neclasificat",
                                 "base": 42.0, "vat": 0.0, "direction": "vanzari"}]
    assert "9" in body["valid_lines"]["vanzari"]
    assert "9" not in body["valid_lines"]["cumparari"]


def test_cod_mapping_cross_section_is_rejected(app, monkeypatch):
    # Recreeaza bug-ul raportat: un cod din jurnalul de CUMPARARI mapat
    # (prin campul liber cod_mapping) pe o linie exclusiv de VANZARI -
    # trebuie respins cu eroare clara, nu acceptat tacit in total.
    import portal.app as app_module
    monkeypatch.setattr(app_module, "parse_p300_pdf", lambda path: AnafP300(
        company_cui="RO111", company_name="Exemplu Test SRL", period="2026-06",
        lines={}))

    def _fake_saga(path):
        from etva.importer.saga import SagaJournal
        return SagaJournal(direction="cumparari", company_name="Exemplu Test SRL",
                           company_cui="RO111", entries=[],
                           legend={"14": {"label": "AIC neimpozabile",
                                          "base": 662.0, "vat": 0.0}})
    monkeypatch.setattr(app_module, "parse_saga_journal", _fake_saga)

    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "company_file": (_io.BytesIO(b"placeholder"), "cumparari.xlsx"),
        "anaf_file": (_io.BytesIO(b"%PDF-fake"), "decont.pdf"),
        "cod_mapping": '{"14": "14+15"}',
    }, content_type="multipart/form-data")
    assert r.status_code == 400
    assert "14+15" in r.get_json()["errors"][0]


def test_cod_mapping_persisted_via_picker_applies_on_next_run(app, monkeypatch):
    import portal.app as app_module
    monkeypatch.setattr(app_module, "parse_p300_pdf", lambda path: AnafP300(
        company_cui="RO111", company_name="Exemplu Test SRL", period="2026-06",
        lines={}))

    def _fake_saga(path):
        from etva.importer.saga import SagaJournal
        return SagaJournal(direction="vanzari", company_name="Exemplu Test SRL",
                           company_cui="RO111", entries=[],
                           legend={"17": {"label": "Livrari scutite fara drept de deducere",
                                          "base": 60.5, "vat": 0.0}})
    monkeypatch.setattr(app_module, "parse_saga_journal", _fake_saga)

    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]

    # Prima rulare: codul "17" ramane neclasificat.
    r1 = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "company_file": (_io.BytesIO(b"placeholder"), "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(b"%PDF-fake"), "decont.pdf"),
    }, content_type="multipart/form-data")
    assert r1.get_json()["unmapped"][0]["cod"] == "17"

    # Confirma maparea prin picker - fara cod_mapping in cerere.
    rmap = c.post("/api/cod-mapari", json={
        "client_id": cid, "direction": "vanzari", "cod": "17", "line_no": "14+15"})
    assert rmap.status_code == 200

    # A doua rulare, FARA cod_mapping: maparea persistata se aplica automat.
    r2 = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "company_file": (_io.BytesIO(b"placeholder"), "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(b"%PDF-fake"), "decont.pdf"),
    }, content_type="multipart/form-data")
    body2 = r2.get_json()
    assert "unmapped" not in body2 or body2["unmapped"] == []
    assert body2["totals_company"]["14+15"] == {"base": 60.5, "vat": 0.0}


def test_run_scoped_cod_mapping_does_not_persist(app, monkeypatch):
    # Campul liber cod_mapping castiga DOAR pentru rularea curenta - nu
    # trebuie sa scrie nimic in cod_mappings (asta a cauzat bug-ul
    # original: o mapare gresita, ghicita, ramasa "permanenta" ar fi fost
    # si mai grav decat una gresita o singura data).
    import portal.app as app_module
    monkeypatch.setattr(app_module, "parse_p300_pdf", lambda path: AnafP300(
        company_cui="RO111", company_name="Exemplu Test SRL", period="2026-06",
        lines={}))

    def _fake_saga(path):
        from etva.importer.saga import SagaJournal
        return SagaJournal(direction="vanzari", company_name="Exemplu Test SRL",
                           company_cui="RO111", entries=[],
                           legend={"17": {"label": "Livrari scutite fara drept de deducere",
                                          "base": 60.5, "vat": 0.0}})
    monkeypatch.setattr(app_module, "parse_saga_journal", _fake_saga)

    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "company_file": (_io.BytesIO(b"placeholder"), "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(b"%PDF-fake"), "decont.pdf"),
        "cod_mapping": '{"17": "14+15"}',
    }, content_type="multipart/form-data")

    assert c.get(f"/api/cod-mapari?client_id={cid}").get_json() == []


def test_cod_mapari_get_and_delete(app):
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    r = c.post("/api/cod-mapari", json={
        "client_id": cid, "direction": "vanzari", "cod": "17", "line_no": "14+15"})
    assert r.status_code == 200

    lista = c.get(f"/api/cod-mapari?client_id={cid}").get_json()
    assert len(lista) == 1 and lista[0]["cod"] == "17"

    r_del = c.delete(f"/api/cod-mapari/{lista[0]['id']}")
    assert r_del.status_code == 200
    assert c.get(f"/api/cod-mapari?client_id={cid}").get_json() == []


def test_cod_mapari_rejects_cross_section_line(app):
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    r = c.post("/api/cod-mapari", json={
        "client_id": cid, "direction": "cumparari", "cod": "14", "line_no": "14+15"})
    assert r.status_code == 400


# ---------- Model e-TVA journal format (alternativa la SAGA) ----------

def _model_bytes(directie, randuri):
    from etva.importer.model import build_model_template, FIRST_DATA_ROW_EXCEL
    wb = build_model_template(directie)
    ws = wb["Jurnal"]
    for i, rand in enumerate(randuri):
        for c, val in enumerate(rand):
            ws.cell(row=FIRST_DATA_ROW_EXCEL + i, column=c + 1, value=val)
    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _eticheta_model(directie, linie):
    from etva.importer.model import TIPURI_OPERATIUNE
    for et, ln in TIPURI_OPERATIUNE[directie]:
        if ln == linie:
            return et
    raise KeyError(linie)


def test_descarca_sablon_jurnal_ambele_directii(app):
    c = app.test_client()
    inregistreaza(c)
    for directie in ("vanzari", "cumparari"):
        r = c.get(f"/api/sabloane/jurnal/{directie}")
        assert r.status_code == 200
        assert r.data[:2] == b"PK"
        assert (f"model_jurnal_{directie}_etva.xlsx"
               in r.headers["Content-Disposition"])


def test_descarca_sablon_jurnal_directie_invalida(app):
    c = app.test_client()
    inregistreaza(c)
    r = c.get("/api/sabloane/jurnal/altceva")
    assert r.status_code == 404


def test_descarca_sablon_jurnal_neautentificat(app):
    c = app.test_client()
    r = c.get("/api/sabloane/jurnal/vanzari")
    assert r.status_code == 401


def test_d300_reconciliation_cu_model_vanzari(app):
    import json as _json
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]

    anaf_json = _json.dumps({
        "CIF": "111", "AN": 2026, "LUNA": 6,
        "RD9_VAL": 1000.0, "RD9_TVA": 210.0,
    }).encode()
    company_file = _model_bytes("vanzari", [
        ("2026-06-01", "F1", "Client X", "RO999", 1000, 210,
         _eticheta_model("vanzari", "9")),
    ])

    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "format_jurnal": "model",
        "company_file": (company_file, "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(anaf_json), "decont.json"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    assert body["mode"] == "d300_lines"
    assert body["totals_company"]["9"] == {"base": 1000.0, "vat": 210.0}
    assert body["differences"] == []


def test_d300_reconciliation_cu_model_cumparari_linii_derivate(app):
    import json as _json
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]

    anaf_json = _json.dumps({"CIF": "111", "AN": 2026, "LUNA": 6}).encode()
    company_file = _model_bytes("cumparari", [
        ("2026-06-02", "FZ1", "Furnizor UE", "IE9999999X", 500, 0,
         _eticheta_model("cumparari", "20.1")),
    ])

    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "format_jurnal": "model",
        "company_file": (company_file, "cumparari.xlsx"),
        "anaf_file": (_io.BytesIO(anaf_json), "decont.json"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    for linie in ("20.1", "5.1", "20", "5"):
        assert body["totals_company"][linie]["base"] == 500.0


def test_saga_incarcat_cu_format_model_da_eroare_prietenoasa(app):
    import json as _json
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]

    anaf_json = _json.dumps({"CIF": "111", "AN": 2026, "LUNA": 6}).encode()
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "format_jurnal": "model",
        "company_file": (_saga_vanzari_bytes(), "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(anaf_json), "decont.json"),
    }, content_type="multipart/form-data")
    assert r.status_code == 400
    assert "MODEL e-TVA" in r.get_json()["errors"][0]


def test_fisier_necitibil_cu_format_implicit_da_hint_model(app):
    import json as _json
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]

    anaf_json = _json.dumps({"CIF": "111", "AN": 2026, "LUNA": 6}).encode()
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "company_file": (_io.BytesIO(b"nu e un fisier excel"), "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(anaf_json), "decont.json"),
    }, content_type="multipart/form-data")
    assert r.status_code == 400
    errors = r.get_json()["errors"]
    assert len(errors) == 2
    assert "Alt program de contabilitate" in errors[1]


def test_format_model_cu_fisier_anaf_xlsx_vechi_da_eroare(app):
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    company_file = _model_bytes("vanzari", [
        ("2026-06-01", "F1", "Client X", "RO999", 1000, 210,
         _eticheta_model("vanzari", "9")),
    ])

    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "format_jurnal": "model",
        "company_file": (company_file, "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(b"cui_partener,nr_factura\n"), "anaf.xlsx"),
    }, content_type="multipart/form-data")
    assert r.status_code == 400


def test_eticheta_libera_model_apare_in_unmapped(app):
    import json as _json
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    company_file = _model_bytes("vanzari", [
        ("2026-06-01", "F1", "Client X", "RO999", 400, 84,
         "Operatiune neclara, de verificat manual"),
    ])

    anaf_json = _json.dumps({"CIF": "111", "AN": 2026, "LUNA": 6}).encode()
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "format_jurnal": "model",
        "company_file": (company_file, "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(anaf_json), "decont.json"),
    }, content_type="multipart/form-data")
    body = r.get_json()
    assert any(u["cod"] == "Operatiune neclara, de verificat manual"
              for u in body["unmapped"])


def test_master_users_page_requires_master(app):
    c = app.test_client()
    inregistreaza(c)
    r = c.get("/master/utilizatori", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_master_users_shows_everything_about_each_account(app, monkeypatch):
    import re
    import portal.app as app_module
    monkeypatch.setattr(app_module, "parse_p300_pdf", lambda path: AnafP300(
        company_cui="RO111", company_name="Exemplu Test SRL", period="2026-06",
        lines={"9": {"base": 1000.0, "vat": 210.0}}))

    conn = app.portal_conn
    conn.execute(
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,TRUE)",
        ("sef", psec.hash_password("ParolaMaster123!")))
    conn.commit()

    c = app.test_client()
    inregistreaza(c, name="Firma1", cui="RO111", tip="contabilitate")
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    c.post("/api/assignments", json={"username": "firma1", "client_id": cid})
    c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "company_file": (_saga_vanzari_bytes(), "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(b"%PDF-fake"), "decont.pdf"),
    }, content_type="multipart/form-data")

    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef",
                                          "password": "ParolaMaster123!"})
    r = c_master.get("/master/utilizatori")
    assert r.status_code == 200
    text = r.data.decode()

    assert "sef" in text and "Master" in text
    assert "Cont administrator platforma" in text

    assert "firma1" in text
    assert re.search(r"<b>1</b>\s*firma", text)
    assert re.search(r"<b>1</b>\s*reconcilieri create", text)

    row = re.search(r"<tr>.*?Firma1.*?</tr>", text, re.S).group(0)
    assert "RO111" in row and "Contabilitate" in row and "admin" in row
    assert row.count("<td>1</td>") == 1  # clienti alocati
    assert re.search(r'class="val">1</span>', row)  # reconcilieri (microbar)


def test_master_users_direct_firm_has_no_manual_client_but_gets_reconciliations(app, monkeypatch):
    import re
    import portal.app as app_module
    monkeypatch.setattr(app_module, "parse_p300_pdf", lambda path: AnafP300(
        company_cui="RO111", company_name="Firma Unu SRL", period="2026-06",
        lines={"9": {"base": 1000.0, "vat": 210.0}}))

    conn = app.portal_conn
    conn.execute(
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,TRUE)",
        ("sef", psec.hash_password("ParolaMaster123!")))
    conn.commit()

    c = app.test_client()
    inregistreaza(c, name="Pfa1", cui="RO111", tip="direct")

    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef",
                                          "password": "ParolaMaster123!"})
    text = c_master.get("/master/utilizatori").data.decode()
    assert "pfa1" in text and "Firma/PFA directa" in text
    assert re.search(r"<b>0</b>\s*reconcilieri create", text)


def test_master_users_kpis_and_charts(app, monkeypatch):
    import portal.app as app_module
    monkeypatch.setattr(app_module, "parse_p300_pdf", lambda path: AnafP300(
        company_cui="RO111", company_name="Exemplu Test SRL", period="2026-06",
        lines={"9": {"base": 1000.0, "vat": 210.0}}))

    conn = app.portal_conn
    conn.execute(
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,TRUE)",
        ("sef", psec.hash_password("ParolaMaster123!")))
    conn.commit()

    c1 = app.test_client()
    inregistreaza(c1, name="Firma1", cui="RO111", tip="contabilitate")
    cid = c1.post("/api/clients",
                  json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    c1.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "company_file": (_saga_vanzari_bytes(), "vanzari.xlsx"),
        "anaf_file": (_io.BytesIO(b"%PDF-fake"), "decont.pdf"),
    }, content_type="multipart/form-data")

    c2 = app.test_client()
    inregistreaza(c2, name="Pfa1", cui="RO333", tip="direct")

    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef",
                                          "password": "ParolaMaster123!"})
    text = c_master.get("/master/utilizatori").data.decode()

    assert 'class="val">2</div>' in text  # total conturi
    assert 'class="val">2</div>' in text  # firme active (same value, 2)
    assert 'class="val">1</div>' in text  # total reconcilieri
    assert 'class="val">0.5</div>' in text  # medie / cont

    assert "Contabilitate — <b>1</b> (50%)" in text
    assert "Firma/PFA directa — <b>1</b> (50%)" in text

    rank = text[text.index("Reconcilieri per cont"):text.index("Firme dupa tip")]
    assert "firma1" in rank and "pfa1" in rank


def test_master_user_history_requires_master(app):
    c = app.test_client()
    inregistreaza(c)
    r = c.get("/master/utilizatori/1/istoric", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_master_user_history_lists_actions_across_firms_and_exports_xml(app):
    conn = app.portal_conn
    conn.execute(
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,TRUE)",
        ("sef", psec.hash_password("ParolaMaster123!")))
    conn.commit()

    c = app.test_client()
    inregistreaza(c, name="Firma Unu SRL", cui="RO111")
    c.post("/api/clients", json={"cui": "RO9", "name": "Client X", "gdpr_confirmat": True})
    user_id = conn.execute(
        "SELECT id FROM users WHERE username='firma-unu-srl'").fetchone()["id"]

    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef",
                                          "password": "ParolaMaster123!"})

    r = c_master.get(f"/master/utilizatori/{user_id}/istoric")
    assert r.status_code == 200
    assert b"client.creare" in r.data

    r_xml = c_master.get(f"/master/utilizatori/{user_id}/istoric.xml")
    assert r_xml.status_code == 200
    assert r_xml.mimetype == "application/xml"
    assert b"attachment" in r_xml.headers["Content-Disposition"].encode()
    assert b'<istoric_utilizator utilizator="firma-unu-srl">' in r_xml.data
    assert b"<tip>client.creare</tip>" in r_xml.data
    assert b"<firma>Firma Unu SRL</firma>" in r_xml.data


# ---------- anunturi in mediul de productie (master) ----------

def _fmt_local(dt):
    return dt.strftime("%Y-%m-%dT%H:%M")


def test_master_anunturi_page_requires_master(app):
    c = app.test_client()
    inregistreaza(c)
    r = c.get("/master/anunturi", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_creaza_anunt_requires_master(app):
    from datetime import datetime, timedelta
    c = app.test_client()
    inregistreaza(c)
    now = datetime.now()
    r = c.post("/master/anunturi", data={
        "mesaj": "Test", "tip": "informativ",
        "incepe_la": _fmt_local(now), "se_termina_la": _fmt_local(now + timedelta(hours=1))},
        follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]
    assert app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM announcements").fetchone()["n"] == 0


def test_master_creates_and_lists_an_announcement(app):
    from datetime import datetime, timedelta
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    now = datetime.now()
    r = c.post("/master/anunturi", data={
        "mesaj": "Mentenanta programata diseara.", "tip": "mentenanta",
        "incepe_la": _fmt_local(now), "se_termina_la": _fmt_local(now + timedelta(hours=1))},
        follow_redirects=True)
    assert r.status_code == 200
    assert "Mentenanta programata diseara.".encode() in r.data
    assert "Mentenanță".encode() in r.data
    row = app.portal_conn.execute("SELECT * FROM announcements").fetchone()
    assert row["tip"] == "mentenanta" and row["creat_de"] == "sef" and row["activ"] == 1


def test_creaza_anunt_rejects_invalid_tip(app):
    from datetime import datetime, timedelta
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    now = datetime.now()
    r = c.post("/master/anunturi", data={
        "mesaj": "Test", "tip": "nu-exista",
        "incepe_la": _fmt_local(now), "se_termina_la": _fmt_local(now + timedelta(hours=1))},
        follow_redirects=True)
    assert "obligatorii".encode() in r.data
    assert app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM announcements").fetchone()["n"] == 0


def test_creaza_anunt_rejects_end_before_start(app):
    from datetime import datetime, timedelta
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    now = datetime.now()
    r = c.post("/master/anunturi", data={
        "mesaj": "Test", "tip": "informativ",
        "incepe_la": _fmt_local(now), "se_termina_la": _fmt_local(now - timedelta(hours=1))},
        follow_redirects=True)
    assert "Sfarsitul trebuie sa fie dupa inceput".encode() in r.data
    assert app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM announcements").fetchone()["n"] == 0


def test_anunt_activ_api_returns_null_when_nothing_active(app):
    c = app.test_client()
    inregistreaza(c)
    r = c.get("/api/anunt-activ")
    assert r.status_code == 200 and r.get_json() is None


def test_anunt_activ_api_returns_the_announcement_within_its_window(app):
    from datetime import datetime, timedelta
    _seed_master(app)
    conn = app.portal_conn
    now = datetime.now()
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post("/master/anunturi", data={
        "mesaj": "Incident in curs de investigare.", "tip": "incident",
        "incepe_la": _fmt_local(now - timedelta(minutes=5)),
        "se_termina_la": _fmt_local(now + timedelta(hours=1))})

    c = app.test_client()
    inregistreaza(c, cui="RO444")
    r = c.get("/api/anunt-activ")
    body = r.get_json()
    assert body["tip"] == "incident"
    assert body["mesaj"] == "Incident in curs de investigare."
    assert body["eticheta"] == "Incident"


def test_anunt_activ_api_ignores_announcements_outside_their_window(app):
    from datetime import datetime, timedelta
    _seed_master(app)
    now = datetime.now()
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post("/master/anunturi", data={
        "mesaj": "Lansare viitoare.", "tip": "lansare",
        "incepe_la": _fmt_local(now + timedelta(days=1)),
        "se_termina_la": _fmt_local(now + timedelta(days=2))})

    c = app.test_client()
    inregistreaza(c, cui="RO555")
    assert c.get("/api/anunt-activ").get_json() is None


def test_dezactiveaza_anunt_hides_it_immediately(app):
    from datetime import datetime, timedelta
    _seed_master(app)
    now = datetime.now()
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post("/master/anunturi", data={
        "mesaj": "Se dezactiveaza.", "tip": "informativ",
        "incepe_la": _fmt_local(now - timedelta(minutes=5)),
        "se_termina_la": _fmt_local(now + timedelta(hours=1))})
    anunt_id = app.portal_conn.execute("SELECT id FROM announcements").fetchone()["id"]

    c = app.test_client()
    inregistreaza(c, cui="RO666")
    assert c.get("/api/anunt-activ").get_json() is not None

    c_master.post(f"/master/anunturi/{anunt_id}/dezactivare")
    assert c.get("/api/anunt-activ").get_json() is None
    text = c_master.get("/master/anunturi").data.decode()
    assert "Dezactivat" in text


def test_panou_shows_the_active_announcement_banner(app):
    from datetime import datetime, timedelta
    _seed_master(app)
    now = datetime.now()
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post("/master/anunturi", data={
        "mesaj": "Lansare noua functionalitate.", "tip": "lansare",
        "incepe_la": _fmt_local(now - timedelta(minutes=5)),
        "se_termina_la": _fmt_local(now + timedelta(hours=1))})

    c = app.test_client()
    inregistreaza(c, cui="RO777")
    text = c.get("/panou").data.decode()
    assert "Lansare noua functionalitate." in text
    assert "Lansare" in text


# ---------- formular de contact ----------

def test_contact_page_served(app):
    c = app.test_client()
    r = c.get("/contact.html")
    assert r.status_code == 200
    assert b"formContact" in r.data


def test_index_html_alias_resolves(app):
    """Every secondary docs page (contact.html included) links back to the
    homepage as a relative 'index.html', not '/' - that only resolves if
    Flask also serves that literal path alongside '/'."""
    c = app.test_client()
    r = c.get("/index.html")
    assert r.status_code == 200
    assert b"e-TVA Reconciliere" in r.data


def test_trimite_contact_saves_message(app):
    c = app.test_client()
    r = c.post("/api/contact", json={
        "nume": "Ion Popescu", "email": "ion@exemplu.ro",
        "tip": "general", "mesaj": "O intrebare generala."})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    row = app.portal_conn.execute("SELECT * FROM contact_messages").fetchone()
    assert row["nume"] == "Ion Popescu" and row["email"] == "ion@exemplu.ro"
    assert row["tip"] == "general" and row["citit"] == 0
    assert row["trimis_de"] is None and row["firma"] is None


def test_trimite_contact_rejects_missing_fields(app):
    c = app.test_client()
    r = c.post("/api/contact", json={
        "nume": "Ion", "email": "ion@exemplu.ro", "tip": "general", "mesaj": ""})
    assert r.status_code == 400
    assert "obligatorii" in r.get_json()["eroare"]
    assert app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM contact_messages").fetchone()["n"] == 0


def test_trimite_contact_rejects_invalid_email(app):
    c = app.test_client()
    r = c.post("/api/contact", json={
        "nume": "Ion", "email": "nu-e-un-email", "tip": "general", "mesaj": "Test"})
    assert r.status_code == 400
    assert "email" in r.get_json()["eroare"]


def test_trimite_contact_rejects_invalid_tip(app):
    c = app.test_client()
    r = c.post("/api/contact", json={
        "nume": "Ion", "email": "ion@exemplu.ro", "tip": "nu-exista", "mesaj": "Test"})
    assert r.status_code == 400
    assert app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM contact_messages").fetchone()["n"] == 0


def test_trimite_contact_captures_logged_in_user_and_firm(app):
    c = app.test_client()
    inregistreaza(c)
    r = c.post("/api/contact", json={
        "nume": "Ion Popescu", "email": "ion@exemplu.ro",
        "tip": "gdpr", "mesaj": "Vreau o copie a datelor mele."})
    assert r.status_code == 200
    row = app.portal_conn.execute("SELECT * FROM contact_messages").fetchone()
    assert row["trimis_de"] == "firma-unu-srl" and row["firma"] == "Firma Unu SRL"


def test_master_mesaje_requires_master(app):
    c = app.test_client()
    inregistreaza(c)
    r = c.get("/master/mesaje", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_master_mesaje_lists_messages(app):
    _seed_master(app)
    c = app.test_client()
    c.post("/api/contact", json={
        "nume": "Ion Popescu", "email": "ion@exemplu.ro",
        "tip": "facturare", "mesaj": "O intrebare despre facturare."})
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    text = c_master.get("/master/mesaje").data.decode()
    assert "Ion Popescu" in text and "O intrebare despre facturare." in text
    assert "Facturare" in text and "Necitit" in text


def test_marcheaza_mesaj_citit_toggles_message(app):
    _seed_master(app)
    c = app.test_client()
    c.post("/api/contact", json={
        "nume": "Ion Popescu", "email": "ion@exemplu.ro",
        "tip": "altele", "mesaj": "Test."})
    mesaj_id = app.portal_conn.execute(
        "SELECT id FROM contact_messages").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post(f"/master/mesaje/{mesaj_id}/citit")
    row = app.portal_conn.execute(
        "SELECT citit FROM contact_messages WHERE id=?", (mesaj_id,)).fetchone()
    assert row["citit"] == 1
    text = c_master.get("/master/mesaje").data.decode()
    assert "Necitit" not in text


# ---------- istoric XML (admin + per-firma) si cereri de stergere ----------

def test_master_firma_istoric_xml_requires_master(app):
    c = app.test_client()
    inregistreaza(c)
    firm_id = app.portal_conn.execute("SELECT id FROM firms").fetchone()["id"]
    r = c.get(f"/master/firme/{firm_id}/istoric.xml", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_master_firma_istoric_xml_contains_firm_actions(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, name="Firma Unu SRL", cui="RO111")
    c.post("/api/logout")
    firm_id = app.portal_conn.execute("SELECT id FROM firms").fetchone()["id"]

    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c_master.get(f"/master/firme/{firm_id}/istoric.xml")
    assert r.status_code == 200
    assert b'<istoric_firma firma="Firma Unu SRL" cui="RO111">' in r.data
    assert b"<tip>logout</tip>" in r.data


def test_master_istoric_propriu_requires_master(app):
    c = app.test_client()
    inregistreaza(c)
    r = c.get("/master/istoric.xml", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_master_istoric_propriu_logs_firma_toggle(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, name="Firma Unu SRL", cui="RO111")
    firm_id = app.portal_conn.execute("SELECT id FROM firms").fetchone()["id"]

    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post(f"/master/firma/{firm_id}/comutare")
    text = c_master.get("/master/istoric").data.decode()
    assert "firma.comutare" in text and "Firma Unu SRL" in text
    r_xml = c_master.get("/master/istoric.xml")
    assert b"<tip>firma.comutare</tip>" in r_xml.data


def test_master_istoric_propriu_includes_pipeline_promotions(app):
    _seed_master(app)
    pl.log_promotion(app.portal_conn, "dev", "testare", "abc1234", "sef")
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r_xml = c_master.get("/master/istoric.xml")
    assert b"<tip>pipeline.promovare</tip>" in r_xml.data
    assert b"dev -&gt; testare" in r_xml.data or b"dev -> testare" in r_xml.data


def test_cerere_stergere_requires_accept_checkbox(app):
    c = app.test_client()
    inregistreaza(c)
    r = c.post("/panou/cerere-stergere", data={}, follow_redirects=True)
    assert "acordul".encode() in r.data
    assert app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM deletion_requests").fetchone()["n"] == 0


def test_cerere_stergere_creates_pending_request_with_30_day_deadline(app):
    from datetime import datetime, timedelta
    c = app.test_client()
    inregistreaza(c, name="Firma Unu SRL")
    r = c.post("/panou/cerere-stergere", data={"accept": "on"}, follow_redirects=True)
    assert r.status_code == 200
    row = app.portal_conn.execute("SELECT * FROM deletion_requests").fetchone()
    assert row["stare"] == "in_asteptare" and row["firm_name"] == "Firma Unu SRL"
    creat = datetime.fromisoformat(row["creat_la"])
    termen = datetime.fromisoformat(row["termen_la"])
    assert abs((termen - creat) - timedelta(days=30)) < timedelta(seconds=5)


def test_cerere_stergere_rejects_duplicate_while_pending(app):
    c = app.test_client()
    inregistreaza(c)
    c.post("/panou/cerere-stergere", data={"accept": "on"})
    c.post("/panou/cerere-stergere", data={"accept": "on"})
    assert app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM deletion_requests").fetchone()["n"] == 1


def test_panou_shows_pending_deletion_request_status(app):
    c = app.test_client()
    inregistreaza(c)
    c.post("/panou/cerere-stergere", data={"accept": "on"})
    text = c.get("/panou").data.decode()
    assert "cerere de ștergere înregistrată" in text


def test_master_cereri_stergere_requires_master(app):
    c = app.test_client()
    inregistreaza(c)
    r = c.get("/master/cereri-stergere", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_master_cereri_stergere_lists_request(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, name="Firma Unu SRL")
    c.post("/panou/cerere-stergere", data={"accept": "on"})
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    text = c_master.get("/master/cereri-stergere").data.decode()
    assert "firma-unu-srl" in text and "Firma Unu SRL" in text
    assert "In asteptare" in text


def test_finalizeaza_cerere_stergere_anonymizes_account_and_blocks_login(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO222")
    c.post("/panou/cerere-stergere", data={"accept": "on"})
    cerere_id = app.portal_conn.execute(
        "SELECT id FROM deletion_requests").fetchone()["id"]

    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post(f"/master/cereri-stergere/{cerere_id}/finalizare")

    row = app.portal_conn.execute(
        "SELECT * FROM deletion_requests WHERE id=?", (cerere_id,)).fetchone()
    assert row["stare"] == "finalizata" and row["procesat_de"] == "sef"
    user_row = app.portal_conn.execute(
        "SELECT * FROM users WHERE username LIKE 'utilizator-sters-%'").fetchone()
    assert user_row is not None and user_row["active"] == 0
    membership = app.portal_conn.execute(
        "SELECT active FROM user_firms WHERE user_id=?", (user_row["id"],)).fetchone()
    assert membership["active"] == 0

    r = app.test_client().post(
        "/autentificare", data={"cui": "RO222", "password": "ParolaLunga123!"})
    assert "incorecta".encode() in r.data


def test_anuleaza_cerere_stergere_marks_cancelled(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c)
    c.post("/panou/cerere-stergere", data={"accept": "on"})
    cerere_id = app.portal_conn.execute(
        "SELECT id FROM deletion_requests").fetchone()["id"]

    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post(f"/master/cereri-stergere/{cerere_id}/anulare")

    row = app.portal_conn.execute(
        "SELECT * FROM deletion_requests WHERE id=?", (cerere_id,)).fetchone()
    assert row["stare"] == "anulata" and row["procesat_de"] == "sef"
    text = c_master.get("/master/cereri-stergere").data.decode()
    assert "Anulata" in text


# ---------- ANAF OAuth2 (decontul precompletat) ----------

from portal import app as portal_app_module
from etva import anaf_oauth


def _stare_anaf(client, code="one-time"):
    """Parcurge /panou/anaf/autorizare -> /api/anaf/callback cu state-ul
    corect capturat din redirect, ca un test dublu pentru fluxul complet."""
    autorizare = client.get("/panou/anaf/autorizare", follow_redirects=False)
    state = autorizare.headers["Location"].split("state=")[1].split("&")[0]
    return client.get(f"/api/anaf/callback?code={code}&state={state}",
                      follow_redirects=True)


def test_anaf_oauth_autorizare_requires_login(app):
    c = app.test_client()
    r = c.get("/panou/anaf/autorizare", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_anaf_oauth_autorizare_requires_admin_role(app):
    c = app.test_client()
    inregistreaza(c, cui="RO111")
    c.post("/panou/utilizatori", data={
        "username": "junior1", "password": "AltaParola456!", "role": "junior"})
    c2 = app.test_client()
    c2.post("/autentificare", data={"cui": "RO111", "password": "AltaParola456!"})
    r = c2.get("/panou/anaf/autorizare", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_anaf_oauth_autorizare_requires_configured_credentials(app, monkeypatch):
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_ID", None)
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_SECRET", None)
    c = app.test_client()
    inregistreaza(c)
    r = c.get("/panou/anaf/autorizare", follow_redirects=True)
    assert "nu este configurata".encode() in r.data


def test_anaf_oauth_autorizare_redirects_to_anaf_when_configured(app, monkeypatch):
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_SECRET", "test-secret")
    c = app.test_client()
    inregistreaza(c)
    r = c.get("/panou/anaf/autorizare", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].startswith(
        "https://logincert.anaf.ro/anaf-oauth2/v1/authorize")
    assert "client_id=test-client-id" in r.headers["Location"]
    assert "state=" in r.headers["Location"]


def test_anaf_oauth_callback_rejects_without_pending_state(app):
    c = app.test_client()
    inregistreaza(c)
    r = c.get("/api/anaf/callback?code=abc&state=xyz", follow_redirects=True)
    assert "esuat sau a expirat".encode() in r.data
    assert app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM anaf_oauth_tokens").fetchone()["n"] == 0


def test_anaf_oauth_callback_rejects_mismatched_state(app, monkeypatch):
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_SECRET", "test-secret")
    c = app.test_client()
    inregistreaza(c)
    c.get("/panou/anaf/autorizare")
    r = c.get("/api/anaf/callback?code=abc&state=gresit", follow_redirects=True)
    assert "esuat sau a expirat".encode() in r.data
    assert app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM anaf_oauth_tokens").fetchone()["n"] == 0


def test_anaf_oauth_callback_exchanges_code_and_stores_tokens(app, monkeypatch):
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(anaf_oauth, "exchange_code_for_tokens",
                        lambda *a, **kw: {"access_token": "AAA", "refresh_token": "BBB"})
    c = app.test_client()
    inregistreaza(c)
    r = _stare_anaf(c)
    assert "autorizat cu succes".encode() in r.data
    firm_id = app.portal_conn.execute("SELECT id FROM firms").fetchone()["id"]
    row = app.portal_conn.execute(
        "SELECT * FROM anaf_oauth_tokens WHERE firm_id=?", (firm_id,)).fetchone()
    assert row is not None and row["autorizat_de"] == "firma-unu-srl"


def test_get_valid_anaf_access_token_returns_none_when_not_authorized(app):
    c = app.test_client()
    inregistreaza(c)
    firm_id = app.portal_conn.execute("SELECT id FROM firms").fetchone()["id"]
    assert app.get_valid_anaf_access_token(firm_id) is None


def test_get_valid_anaf_access_token_returns_stored_token_when_fresh(app, monkeypatch):
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(anaf_oauth, "exchange_code_for_tokens",
                        lambda *a, **kw: {"access_token": "FRESH", "refresh_token": "R1"})
    c = app.test_client()
    inregistreaza(c)
    _stare_anaf(c)
    firm_id = app.portal_conn.execute("SELECT id FROM firms").fetchone()["id"]
    assert app.get_valid_anaf_access_token(firm_id) == "FRESH"


def test_get_valid_anaf_access_token_refreshes_when_expired(app, monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(anaf_oauth, "exchange_code_for_tokens",
                        lambda *a, **kw: {"access_token": "OLD", "refresh_token": "R1"})
    c = app.test_client()
    inregistreaza(c)
    _stare_anaf(c)
    firm_id = app.portal_conn.execute("SELECT id FROM firms").fetchone()["id"]

    app.portal_conn.execute(
        "UPDATE anaf_oauth_tokens SET expira_la=? WHERE firm_id=?",
        ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), firm_id))
    app.portal_conn.commit()

    monkeypatch.setattr(anaf_oauth, "refresh_access_token",
                        lambda *a, **kw: {"access_token": "REFRESHED", "refresh_token": "R2"})
    assert app.get_valid_anaf_access_token(firm_id) == "REFRESHED"
    row = app.portal_conn.execute(
        "SELECT * FROM anaf_oauth_tokens WHERE firm_id=?", (firm_id,)).fetchone()
    assert row["expira_la"] > datetime.now().isoformat()
    assert row["autorizat_de"] == "firma-unu-srl"


def test_reconciliation_auto_fetch_requires_anaf_authorization(app):
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06", "anaf_sursa": "auto",
        "company_file": (_saga_vanzari_bytes(), "vanzari.xlsx"),
    }, content_type="multipart/form-data")
    assert r.status_code == 400
    assert "nu are acces ANAF autorizat".encode() in r.data


def test_reconciliation_auto_fetch_rejects_bad_period_format(app, monkeypatch):
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(anaf_oauth, "exchange_code_for_tokens",
                        lambda *a, **kw: {"access_token": "AAA", "refresh_token": "BBB"})
    c = app.test_client()
    inregistreaza(c)
    _stare_anaf(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "iunie-2026", "anaf_sursa": "auto",
        "company_file": (_saga_vanzari_bytes(), "vanzari.xlsx"),
    }, content_type="multipart/form-data")
    assert r.status_code == 400
    assert "formatul AAAA-LL".encode() in r.data


def test_reconciliation_auto_fetch_uses_stored_anaf_token(app, monkeypatch):
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(anaf_oauth, "exchange_code_for_tokens",
                        lambda *a, **kw: {"access_token": "AAA", "refresh_token": "BBB"})
    c = app.test_client()
    inregistreaza(c)
    _stare_anaf(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]

    captura = {}
    def _fake_fetch_decont(access_token, cui, an, luna):
        captura["access_token"] = access_token
        captura["cui"] = cui
        captura["an"] = an
        captura["luna"] = luna
        return {"CIF": cui, "AN": an, "LUNA": luna,
                "RD9_VAL": 1000.0, "RD9_TVA": 210.0}
    monkeypatch.setattr(anaf_oauth, "fetch_decont", _fake_fetch_decont)

    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06", "anaf_sursa": "auto",
        "company_file": (_saga_vanzari_bytes(), "vanzari.xlsx"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    assert body["mode"] == "d300_lines"
    assert body["totals_anaf"]["9"] == {"base": 1000.0, "vat": 210.0}
    assert captura["access_token"] == "AAA"
    assert captura["an"] == 2026 and captura["luna"] == 6


def test_reconciliation_auto_fetch_surfaces_anaf_oauth_errors(app, monkeypatch):
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(anaf_oauth, "exchange_code_for_tokens",
                        lambda *a, **kw: {"access_token": "AAA", "refresh_token": "BBB"})
    c = app.test_client()
    inregistreaza(c)
    _stare_anaf(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]

    def _boom(*a, **kw):
        raise anaf_oauth.AnafOAuthError("Serviciul ANAF nu a putut fi contactat: boom")
    monkeypatch.setattr(anaf_oauth, "fetch_decont", _boom)

    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06", "anaf_sursa": "auto",
        "company_file": (_saga_vanzari_bytes(), "vanzari.xlsx"),
    }, content_type="multipart/form-data")
    assert r.status_code == 502
    assert "boom".encode() in r.data


def test_reconciliation_auto_fetch_rejects_malformed_decont(app, monkeypatch):
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(anaf_oauth, "exchange_code_for_tokens",
                        lambda *a, **kw: {"access_token": "AAA", "refresh_token": "BBB"})
    c = app.test_client()
    inregistreaza(c)
    _stare_anaf(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]
    monkeypatch.setattr(anaf_oauth, "fetch_decont", lambda *a, **kw: {"nu": "e decont"})

    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06", "anaf_sursa": "auto",
        "company_file": (_saga_vanzari_bytes(), "vanzari.xlsx"),
    }, content_type="multipart/form-data")
    assert r.status_code == 400


def test_reconciliation_without_anaf_file_returns_clean_error(app):
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X", "gdpr_confirmat": True}).get_json()["id"]

    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-06",
        "company_file": (_saga_vanzari_bytes(), "vanzari.xlsx"),
    }, content_type="multipart/form-data")

    assert r.status_code == 400
    assert "decont" in r.get_json()["errors"][0].lower()


def test_me_reports_anaf_autorizat_flag(app, monkeypatch):
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_SECRET", "test-secret")
    c = app.test_client()
    inregistreaza(c)
    assert c.get("/api/me").get_json()["anaf_autorizat"] is False

    monkeypatch.setattr(anaf_oauth, "exchange_code_for_tokens",
                        lambda *a, **kw: {"access_token": "AAA", "refresh_token": "BBB"})
    _stare_anaf(c)
    assert c.get("/api/me").get_json()["anaf_autorizat"] is True


# ---------- facturare (master) ----------

def test_master_facturi_requires_master(app):
    c = app.test_client()
    inregistreaza(c)
    r = c.get("/master/facturi", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_creeaza_factura_requires_master(app):
    c = app.test_client()
    inregistreaza(c, cui="RO111")
    firm_id = app.portal_conn.execute("SELECT id FROM firms").fetchone()["id"]
    r = c.post("/master/facturi", data={
        "firm_id": firm_id, "descriere": "Test", "valoare_neta": "100",
        "cota_tva": "19"}, follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]
    assert app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM invoices").fetchone()["n"] == 0


def test_creeaza_factura_rejects_missing_fields(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO111")
    firm_id = app.portal_conn.execute("SELECT id FROM firms").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c_master.post("/master/facturi", data={
        "firm_id": firm_id, "descriere": "", "valoare_neta": "100", "cota_tva": "19"},
        follow_redirects=True)
    assert "obligatorii".encode() in r.data
    assert app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM invoices").fetchone()["n"] == 0


def test_creeaza_factura_computes_totals_and_increments_numbering(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, name="Firma Unu SRL", cui="RO111")
    firm_id = app.portal_conn.execute("SELECT id FROM firms").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    c_master.post("/master/facturi", data={
        "firm_id": firm_id, "descriere": "Abonament iunie",
        "valoare_neta": "100", "cota_tva": "19", "judet": "Bucuresti"})
    c_master.post("/master/facturi", data={
        "firm_id": firm_id, "descriere": "Abonament iulie",
        "valoare_neta": "50", "cota_tva": "19", "judet": "Bucuresti"})

    rows = app.portal_conn.execute(
        "SELECT * FROM invoices ORDER BY numar").fetchall()
    assert len(rows) == 2
    assert rows[0]["numar"] == 1 and rows[1]["numar"] == 2
    # serie/numar raman doar cheia interna de randare - fgo_serie/fgo_numar
    # sunt cele REALE (vezi _mock_fgo), afisate firmei.
    assert rows[0]["serie"] == "ETVA"
    assert rows[0]["fgo_serie"] == "VML" and rows[0]["fgo_numar"] == "0001"
    assert rows[0]["firm_name"] == "Firma Unu SRL" and rows[0]["firm_cui"] == "RO111"
    assert rows[0]["valoare_tva"] == 19.0
    assert rows[0]["valoare_totala"] == 119.0
    assert rows[0]["creat_de"] == "sef"


def test_descarca_factura_pdf_requires_master(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO111")
    firm_id = app.portal_conn.execute("SELECT id FROM firms").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post("/master/facturi", data={
        "firm_id": firm_id, "descriere": "Abonament", "valoare_neta": "100",
        "cota_tva": "19", "judet": "Bucuresti"})
    factura_id = app.portal_conn.execute("SELECT id FROM invoices").fetchone()["id"]

    r = c.get(f"/master/facturi/{factura_id}/pdf", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_descarca_factura_pdf_returns_pdf_bytes(app):
    """"Returns pdf bytes" istoric - de cand PDF-ul vine de la FGO
    (Factura.Link), ruta redirectioneaza acolo in loc sa serveasca bytes
    direct; vezi _mock_fgo pentru link-ul fals folosit in teste."""
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO111")
    firm_id = app.portal_conn.execute("SELECT id FROM firms").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post("/master/facturi", data={
        "firm_id": firm_id, "descriere": "Abonament e-TVA Reconciliere",
        "valoare_neta": "100", "cota_tva": "19", "judet": "Bucuresti"})
    factura_id = app.portal_conn.execute("SELECT id FROM invoices").fetchone()["id"]

    r = c_master.get(f"/master/facturi/{factura_id}/pdf", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].startswith("https://fgo.testuat/")


def test_next_invoice_number_starts_at_one_and_increments(app):
    from portal import invoicing
    from etva import dbcompat
    assert invoicing.next_invoice_number(app.portal_conn, "ETVA") == 1
    # invoices.firm_id are FK spre firms (aplicata pe PG, doar declarata pe
    # SQLite) - firma trebuie sa existe cu adevarat, nu doar un id inventat.
    firm_id = dbcompat.insert_id(
        app.portal_conn,
        "INSERT INTO firms(name, cui, tip, creat_la) VALUES(?,?,?,?)",
        ("Test SRL", "RO1", "direct", "2026-01-01T00:00:00+00:00"))
    app.portal_conn.execute(
        "INSERT INTO invoices(serie, numar, firm_id, firm_name, firm_cui, "
        "descriere, data_emiterii, valoare_neta, cota_tva, valoare_tva, "
        "valoare_totala, creat_de, creat_la) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("ETVA", 1, firm_id, "Test SRL", "RO1", "Test", "2026-01-01", 100, 19,
         19, 119, "sef", "2026-01-01"))
    assert invoicing.next_invoice_number(app.portal_conn, "ETVA") == 2


# ---------- facturare: emitere prin FGO (vezi _mock_fgo) ----------

def _creeaza_factura(app, client_master, firm_id, descriere="Abonament",
                     judet="Bucuresti"):
    client_master.post("/master/facturi", data={
        "firm_id": firm_id, "descriere": descriere,
        "valoare_neta": "100", "cota_tva": "19", "judet": judet})
    return app.portal_conn.execute(
        "SELECT id FROM invoices ORDER BY id DESC LIMIT 1").fetchone()["id"]


# ---------- facturare: vizibilitate firma (facturile ei proprii) ----------

def test_alege_plan_shows_own_invoices(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, name="Firma Patru SRL", cui="RO308")
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO308'").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    _creeaza_factura(app, c_master, firm_id, descriere="Abonament e-TVA")

    r = c.get("/panou/plan")
    assert "Facturile mele".encode() in r.data
    # Seria/numarul afisate sunt cele REALE, atribuite de FGO (vezi
    # _mock_fgo) - nu vechea numerotare locala "ETVA".
    assert "VML".encode() in r.data
    assert "119.00".encode() in r.data or "119.0".encode() in r.data


def test_descarca_factura_proprie_pdf_requires_login(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO309")
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO309'").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    factura_id = _creeaza_factura(app, c_master, firm_id)

    c_anonim = app.test_client()
    r = c_anonim.get(f"/panou/factura/{factura_id}/pdf", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_descarca_factura_proprie_pdf_returns_own_invoice(app):
    """PDF-ul vine de la FGO (Factura.Link) - ruta redirectioneaza acolo,
    vezi _mock_fgo pentru link-ul fals folosit in teste."""
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, name="Firma Proprie SRL", cui="RO310")
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO310'").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    factura_id = _creeaza_factura(app, c_master, firm_id)

    r = c.get(f"/panou/factura/{factura_id}/pdf", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].startswith("https://fgo.testuat/")


def test_firma_nu_poate_accesa_factura_altei_firme_prin_id_ghicit(app):
    """Proprietatea de securitate centrala a acestei functionalitati: o
    firma autentificata in propriul cont/propria firma NU trebuie sa poata
    accesa factura altei firme doar ghicind/iterand id-ul numeric din URL.

    O implementare naiva ar verifica doar `_role_in_firm(user_id,
    active_firm_id)` (adica "esti membru al vreunei firme?") si ar servi
    orice `factura_id` cerut - cum firma A e intr-adevar membra a firmei ei
    proprii (A), acel check ar trece, iar factura firmei B (la care A nu
    are niciun rol) ar fi servita oricum. Testul de fata ar pica exact pe
    o asemenea implementare, pentru ca verifica in mod explicit ca raspunsul
    NU e 200/continutul facturii B, ci un redirect identic cu cazul
    "factura inexistenta"."""
    _seed_master(app)
    c_a = app.test_client()
    inregistreaza(c_a, name="Firma A SRL", cui="RO314")
    firm_a_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO314'").fetchone()["id"]

    c_b = app.test_client()
    inregistreaza(c_b, name="Firma B SRL", cui="RO315")
    firm_b_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO315'").fetchone()["id"]

    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    factura_b_id = _creeaza_factura(app, c_master, firm_b_id,
                                    descriere="Abonament Firma B - confidential")

    # Firma A e complet neasociata firmei B (niciun rol in user_firms) - dar
    # e autentificata normal, in propriul cont. Incearca sa acceseze factura
    # firmei B ghicind id-ul (de exemplu incrementand propriul ei id de
    # factura, daca ar fi avut una).
    assert app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM invoices WHERE firm_id=?",
        (firm_a_id,)).fetchone()["n"] == 0

    r_pdf = c_a.get(f"/panou/factura/{factura_b_id}/pdf", follow_redirects=False)
    assert r_pdf.status_code == 302 and "/panou/plan" in r_pdf.headers["Location"]

    # Firma B insasi tot poate accesa propria factura - confirmam ca
    # respingerea de mai sus e specifica firmei A, nu o ruta stricata.
    r_ok = c_b.get(f"/panou/factura/{factura_b_id}/pdf", follow_redirects=False)
    assert r_ok.status_code == 302
    assert r_ok.headers["Location"].startswith("https://fgo.testuat/")


# ---------- backup date productie ----------

def test_restaureaza_backup_requires_master(app):
    c = app.test_client()
    r = c.post("/master/backup/restaureaza", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_restaureaza_backup_blocked_in_productie(app, monkeypatch):
    monkeypatch.setattr(pl, "own_environment", lambda: "productie")
    _seed_master(app)
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    r = c_master.post("/master/backup/restaureaza", data={"confirm": "da"},
                      follow_redirects=True)
    assert "dezactivata in productie".encode() in r.data


@doar_sqlite
def test_restaureaza_backup_requires_confirmation(app, monkeypatch):
    monkeypatch.setattr(pl, "own_environment", lambda: "testare")
    _seed_master(app)
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    r = c_master.post("/master/backup/restaureaza", data={}, follow_redirects=True)
    assert "confirmi explicit".encode() in r.data


@doar_sqlite
def test_restaureaza_backup_requires_file(app, monkeypatch):
    monkeypatch.setattr(pl, "own_environment", lambda: "testare")
    _seed_master(app)
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    r = c_master.post("/master/backup/restaureaza", data={"confirm": "da"},
                      follow_redirects=True)
    assert "Alege un fisier".encode() in r.data


@doar_sqlite
def test_restaureaza_backup_rejects_invalid_zip(app, monkeypatch):
    import io
    import zipfile
    monkeypatch.setattr(pl, "own_environment", lambda: "testare")
    _seed_master(app)
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    bogus = io.BytesIO()
    with zipfile.ZipFile(bogus, "w") as zf:
        zf.writestr("not-a-backup.txt", "nope")
    bogus.seek(0)

    r = c_master.post("/master/backup/restaureaza", data={
        "confirm": "da", "fisier": (bogus, "bogus.zip"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert "Restaurare esuata".encode() in r.data


@doar_sqlite
def test_restaureaza_backup_restores_older_state_and_closes_process_connections(app, monkeypatch):
    import io
    import sqlite3
    monkeypatch.setattr(pl, "own_environment", lambda: "testare")
    _seed_master(app)
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    c_master.post("/master/backup/creeaza")
    nume_backup_vechi = app.portal_conn.execute(
        "SELECT detalii FROM master_actions WHERE actiune='backup_creat' "
        "ORDER BY id DESC LIMIT 1").fetchone()["detalii"]
    zip_bytes = c_master.get(f"/master/backup/{nume_backup_vechi}/descarca").data

    c = app.test_client()
    inregistreaza(c, name="Firma Noua SRL", cui="RO222")
    assert app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM firms WHERE cui='RO222'").fetchone()["n"] == 1
    # trebuie citit inaintea restaurarii - conexiunea aplicatiei se va inchide
    db_path = dict(app.portal_conn.execute("PRAGMA database_list").fetchone())["file"]

    r = c_master.post("/master/backup/restaureaza", data={
        "confirm": "da", "fisier": (io.BytesIO(zip_bytes), "backup.zip"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    assert "Backup restaurat".encode() in r.data
    assert "Reporneste manual serverul".encode() in r.data

    fresh = sqlite3.connect(db_path)
    fresh.row_factory = sqlite3.Row
    n_pe_disc = fresh.execute(
        "SELECT COUNT(*) AS n FROM firms WHERE cui='RO222'").fetchone()["n"]
    fresh.close()
    assert n_pe_disc == 0  # firma noua a disparut din fisierul de pe disc

    # conexiunea procesului a fost inchisa explicit - nimic nu mai poate rula
    # pana la restart, nu doar "date invechite"
    with pytest.raises(sqlite3.ProgrammingError):
        app.portal_conn.execute("SELECT 1")


# ---------- master: restaurare Postgres ----------
# Majoritatea acestor teste nu au nevoie de un Postgres real: ruta citeste
# dbcompat.backend()/pl.own_environment() la momentul cererii, asa ca un
# monkeypatch e suficient sa exercite toate verificarile, scrierea
# trigger-ului si selectorul din manifest pe suita implicita SQLite. Doar
# afisarea rezultatului verify_schema chiar are nevoie de Postgres real -
# vezi @doar_postgres mai jos.
doar_postgres = pytest.mark.skipif(
    os.environ.get("ETVA_TEST_PG") != "1",
    reason="restaurarea Postgres exista doar pe mediile migrate (ETVA_TEST_PG=1)")


def _pregateste_postgres_testare(monkeypatch):
    from etva import dbcompat
    monkeypatch.setattr(dbcompat, "backend", lambda: "postgres")
    monkeypatch.setattr(pl, "own_environment", lambda: "testare")


def test_restaurare_pg_requires_master(app):
    c = app.test_client()
    r = c.post("/master/backup/postgres/restaureaza", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_restaurare_pg_blocked_in_productie(app, tmp_path, monkeypatch):
    from etva import dbcompat
    monkeypatch.setattr(dbcompat, "backend", lambda: "postgres")
    monkeypatch.setattr(pl, "own_environment", lambda: "productie")
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    r = c.post("/master/backup/postgres/restaureaza",
              data={"confirmare": backup_pg.nume_baza(os.environ.get("DATABASE_URL")), "sursa": "local", "data": "2026-08-04"},
              follow_redirects=True)
    assert "dezactivata in productie".encode() in r.data
    assert not (tmp_path / backup_pg.RESTORE_TRIGGER_NAME).exists()
    assert app.portal_conn.execute(
        "SELECT 1 FROM master_actions WHERE actiune='backup.pg_restaurare_solicitata'"
    ).fetchone() is None


def test_restaurare_pg_blocked_outside_testare(app, tmp_path, monkeypatch):
    from etva import dbcompat
    monkeypatch.setattr(dbcompat, "backend", lambda: "postgres")
    monkeypatch.setattr(pl, "own_environment", lambda: None)
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    r = c.post("/master/backup/postgres/restaureaza",
              data={"confirmare": backup_pg.nume_baza(os.environ.get("DATABASE_URL")), "sursa": "local", "data": "2026-08-04"},
              follow_redirects=True)
    assert "disponibila doar pe mediul testare".encode() in r.data
    assert not (tmp_path / backup_pg.RESTORE_TRIGGER_NAME).exists()


@doar_sqlite
def test_restaurare_pg_refuza_pe_sqlite(app, tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "own_environment", lambda: "testare")
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    r = c.post("/master/backup/postgres/restaureaza",
              data={"confirmare": backup_pg.nume_baza(os.environ.get("DATABASE_URL")), "sursa": "local", "data": "2026-08-04"},
              follow_redirects=True)
    assert "nu ruleaza pe Postgres".encode() in r.data
    assert not (tmp_path / backup_pg.RESTORE_TRIGGER_NAME).exists()


def test_restaurare_pg_cere_fraza_de_confirmare(app, tmp_path, monkeypatch):
    _pregateste_postgres_testare(monkeypatch)
    (tmp_path / backup_pg.MANIFEST_NAME).write_text("2026-08-04|100\n", encoding="utf-8")
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    r = c.post("/master/backup/postgres/restaureaza",
              data={"confirmare": "da", "sursa": "local", "data": "2026-08-04"},
              follow_redirects=True)
    assert "Trebuie sa scrii exact".encode() in r.data
    assert not (tmp_path / backup_pg.RESTORE_TRIGGER_NAME).exists()
    assert app.portal_conn.execute(
        "SELECT 1 FROM master_actions WHERE actiune='backup.pg_restaurare_solicitata'"
    ).fetchone() is None


def test_restaurare_pg_refuza_data_absenta_din_manifest(app, tmp_path, monkeypatch):
    _pregateste_postgres_testare(monkeypatch)
    (tmp_path / backup_pg.MANIFEST_NAME).write_text("2026-08-04|100\n", encoding="utf-8")
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    r = c.post("/master/backup/postgres/restaureaza",
              data={"confirmare": backup_pg.nume_baza(os.environ.get("DATABASE_URL")), "sursa": "local", "data": "2026-08-01"},
              follow_redirects=True)
    assert "nu se afla in lista".encode() in r.data
    assert not (tmp_path / backup_pg.RESTORE_TRIGGER_NAME).exists()


def test_restaurare_pg_din_backup_local_scrie_trigger(app, tmp_path, monkeypatch):
    _pregateste_postgres_testare(monkeypatch)
    (tmp_path / backup_pg.MANIFEST_NAME).write_text("2026-08-04|100\n", encoding="utf-8")
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    r = c.post("/master/backup/postgres/restaureaza",
              data={"confirmare": backup_pg.nume_baza(os.environ.get("DATABASE_URL")), "sursa": "local", "data": "2026-08-04"})
    assert r.status_code == 200
    assert "Restaurare pornita".encode() in r.data

    linii = (tmp_path / backup_pg.RESTORE_TRIGGER_NAME).read_text(
        encoding="utf-8").strip("\n").split("\n")
    assert len(linii) == 2 and linii[1] == "local:2026-08-04"
    from datetime import datetime
    datetime.fromisoformat(linii[0])

    rand = app.portal_conn.execute(
        "SELECT actiune, detalii FROM master_actions ORDER BY id DESC LIMIT 1").fetchone()
    assert rand["actiune"] == "backup.pg_restaurare_solicitata"
    assert rand["detalii"] == "local:2026-08-04"


def test_restaurare_pg_din_fisier_incarcat_salveaza_si_scrie_trigger(app, tmp_path, monkeypatch):
    import io
    import gzip
    _pregateste_postgres_testare(monkeypatch)
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    continut = gzip.compress(b"-- PostgreSQL database dump")
    r = c.post("/master/backup/postgres/restaureaza", data={
        "confirmare": backup_pg.nume_baza(os.environ.get("DATABASE_URL")), "sursa": "upload",
        "fisier": (io.BytesIO(continut), "backup.sql.gz"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    assert "Restaurare pornita".encode() in r.data

    linii = (tmp_path / backup_pg.RESTORE_TRIGGER_NAME).read_text(
        encoding="utf-8").strip("\n").split("\n")
    assert linii[1] == "upload"
    assert (tmp_path / backup_pg.RESTORE_UPLOAD_NAME).read_bytes() == continut


def test_restaurare_pg_refuza_fisier_care_nu_e_gzip(app, tmp_path, monkeypatch):
    import io
    _pregateste_postgres_testare(monkeypatch)
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    r = c.post("/master/backup/postgres/restaureaza", data={
        "confirmare": backup_pg.nume_baza(os.environ.get("DATABASE_URL")), "sursa": "upload",
        "fisier": (io.BytesIO(b"nu sunt gzip"), "backup.sql.gz"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert "Restaurare esuata".encode() in r.data
    assert not (tmp_path / backup_pg.RESTORE_TRIGGER_NAME).exists()
    assert not (tmp_path / backup_pg.RESTORE_UPLOAD_NAME).exists()


def test_restaurare_pg_ignora_numele_de_fisier_trimis(app, tmp_path, monkeypatch):
    import io
    import gzip
    _pregateste_postgres_testare(monkeypatch)
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    continut = gzip.compress(b"-- PostgreSQL database dump")
    c.post("/master/backup/postgres/restaureaza", data={
        "confirmare": backup_pg.nume_baza(os.environ.get("DATABASE_URL")), "sursa": "upload",
        "fisier": (io.BytesIO(continut), "../../evil.sql.gz"),
    }, content_type="multipart/form-data")
    nume_fisiere = {p.name for p in tmp_path.iterdir() if p.is_file()}
    assert backup_pg.RESTORE_UPLOAD_NAME in nume_fisiere
    assert not any(".." in nume for nume in nume_fisiere)


def test_master_backup_afiseaza_starea_restaurarii_pg(app, tmp_path, monkeypatch):
    _pregateste_postgres_testare(monkeypatch)
    (tmp_path / backup_pg.RESTORE_STATUS_NAME).write_text(
        "ok|2026-08-04T21:48:11Z|Baza etva_testare a fost restaurata.", encoding="utf-8")
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c.get("/master/backup")
    assert "Baza etva_testare a fost restaurata.".encode() in r.data


def test_master_backup_listeaza_backupurile_din_manifest(app, tmp_path, monkeypatch):
    _pregateste_postgres_testare(monkeypatch)
    (tmp_path / backup_pg.MANIFEST_NAME).write_text(
        "2026-08-04|186666\nlinie-stricata\n2026-08-03|182401\n", encoding="utf-8")
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c.get("/master/backup")
    assert r.status_code == 200
    assert "2026-08-04".encode() in r.data
    assert "2026-08-03".encode() in r.data


def test_master_backup_fara_manifest_nu_crapa(app, monkeypatch):
    _pregateste_postgres_testare(monkeypatch)
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c.get("/master/backup")
    assert r.status_code == 200
    assert "nu a fost generată încă".encode() in r.data


@doar_postgres
def test_master_backup_afiseaza_verificarea_schemei(app, monkeypatch):
    monkeypatch.setattr(pl, "own_environment", lambda: "testare")
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c.get("/master/backup")
    assert "Schema bazei curente corespunde codului".encode() in r.data


# ---------- inregistrare: email obligatoriu + verificare cont + trial ----------

def test_inregistrare_requires_email(app):
    c = app.test_client()
    r = c.post("/inregistrare", data={
        "name": "Firma X SRL", "cui": "RO333", "tip": "direct",
        "password": "ParolaLunga123!", "accept_termeni": "on"})
    assert "obligatorii".encode() in r.data


def test_inregistrare_rejects_invalid_email_format(app):
    c = app.test_client()
    r = c.post("/inregistrare", data={
        "name": "Firma X SRL", "cui": "RO333", "tip": "direct",
        "email": "nu-e-email", "password": "ParolaLunga123!",
        "accept_termeni": "on"})
    assert "nu pare valida".encode() in r.data


def test_inregistrare_stores_email_and_starts_trial(app):
    c = app.test_client()
    inregistreaza(c, cui="RO333", email="andrei@exemplu.ro")
    row = app.portal_conn.execute(
        "SELECT u.email, f.email_verificat, f.email_verificare_token, "
        "f.creat_la, f.trial_expira_la FROM users u "
        "JOIN user_firms uf ON uf.user_id=u.id "
        "JOIN firms f ON f.id=uf.firm_id WHERE f.cui='RO333'").fetchone()
    assert row["email"] == "andrei@exemplu.ro"
    assert row["email_verificat"] == 0
    assert row["email_verificare_token"]
    assert row["creat_la"] and row["trial_expira_la"]


def test_adauga_firma_pe_cont_existent_nu_cere_reverificare(app):
    c = app.test_client()
    inregistreaza(c, cui="RO901")
    r = c.post("/panou/firme", data={
        "name": "A Doua Firma SRL", "cui": "RO902", "tip": "direct", "reconcilieri_estimate": "10"})
    row = app.portal_conn.execute(
        "SELECT email_verificat, email_verificare_token FROM firms "
        "WHERE cui='RO902'").fetchone()
    assert row["email_verificat"] == 1
    assert row["email_verificare_token"] is None


def test_verifica_email_marks_firm_verified(app):
    c = app.test_client()
    inregistreaza(c, cui="RO444")
    token = app.portal_conn.execute(
        "SELECT email_verificare_token FROM firms WHERE cui='RO444'"
    ).fetchone()["email_verificare_token"]
    r = c.get(f"/verifica-email/{token}", follow_redirects=True)
    assert "confirmata".encode() in r.data
    row = app.portal_conn.execute(
        "SELECT email_verificat, email_verificare_token FROM firms "
        "WHERE cui='RO444'").fetchone()
    assert row["email_verificat"] == 1
    assert row["email_verificare_token"] is None


def test_verifica_email_rejects_unknown_token(app):
    c = app.test_client()
    r = c.get("/verifica-email/token-inexistent", follow_redirects=True)
    assert "invalid".encode() in r.data


def test_app_allows_access_when_verification_not_enforced(app):
    c = app.test_client()
    inregistreaza(c, cui="RO555")
    r = c.get("/app")
    assert r.status_code == 200


def test_app_blocks_unverified_firm_when_enforced(app, monkeypatch):
    monkeypatch.setattr(portal_app_module, "EMAIL_VERIFICARE_OBLIGATORIE", True)
    c = app.test_client()
    inregistreaza(c, cui="RO666")
    r = c.get("/app", follow_redirects=False)
    assert r.status_code == 302
    assert "asteapta-verificare-email" in r.headers["Location"]


def test_app_allows_verified_firm_when_enforced(app, monkeypatch):
    monkeypatch.setattr(portal_app_module, "EMAIL_VERIFICARE_OBLIGATORIE", True)
    c = app.test_client()
    inregistreaza(c, cui="RO777")
    token = app.portal_conn.execute(
        "SELECT email_verificare_token FROM firms WHERE cui='RO777'"
    ).fetchone()["email_verificare_token"]
    c.get(f"/verifica-email/{token}")
    r = c.get("/app")
    assert r.status_code == 200


def test_retrimite_verificare_email_confirms_resend(app):
    c = app.test_client()
    inregistreaza(c, cui="RO888")
    r = c.post("/retrimite-verificare-email", follow_redirects=True)
    assert "Email retrimis".encode() in r.data


def test_alege_plan_requires_login(app):
    c = app.test_client()
    r = c.get("/panou/plan", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_alege_plan_shows_prices_for_firm_tip(app):
    from portal import db as pdb
    c = app.test_client()
    inregistreaza(c, cui="RO999", tip="contabilitate")
    r = c.get("/panou/plan")
    pret = pdb.get_preturi(app.portal_conn)["contabilitate"]["lunar"]
    assert str(pret).encode() in r.data


def test_salveaza_plan_rejects_invalid_cycle(app):
    c = app.test_client()
    inregistreaza(c, cui="RO101")
    r = c.post("/panou/plan", data={"ciclu": "saptamanal"}, follow_redirects=True)
    assert "ciclu de facturare valid".encode() in r.data


def test_salveaza_plan_stores_choice(app):
    c = app.test_client()
    inregistreaza(c, cui="RO102")
    r = c.post("/panou/plan", data={"ciclu": "an"}, follow_redirects=True)
    assert "Planul a fost salvat".encode() in r.data
    row = app.portal_conn.execute(
        "SELECT ciclu_facturare FROM firms WHERE cui='RO102'").fetchone()
    assert row["ciclu_facturare"] == "an"


# ---------- plati abonament ----------

def _apropie_trial_de_final(app, cui):
    from datetime import datetime, timedelta, timezone
    aproape = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
    app.portal_conn.execute(
        "UPDATE firms SET trial_expira_la=? WHERE cui=?", (aproape, cui))
    app.portal_conn.commit()


def _semneaza_contract_esemneaza(app, c):
    """Master creeaza si trimite contractul (fluxul nou, controlat de
    master - vezi planning/specs/2026-07-28-contract-esemneaza-admin-review-
    design.md), apoi verifica starea din partea firmei. `c` ramane
    autentificat ca firma - se foloseste un client separat, autentificat ca
    master, pentru pasul de trimitere. Modulul etva.esemneaza e mockuit
    implicit (vezi fixture-ul autouse _mock_esemneaza) sa raporteze ambii
    semnatari ca APPLIED la prima verificare, suficient cat sa treaca poarta
    din creeaza_cerere_plata fara sa depinda de serviciul real (vezi
    tests/test_esemneaza.py pentru testele modulului insusi)."""
    with c.session_transaction() as sess:
        firm_id = sess["active_firm_id"]
    master = _creeaza_master(app)
    ciclu = app.portal_conn.execute(
        "SELECT ciclu_facturare FROM firms WHERE id=?", (firm_id,)).fetchone()["ciclu_facturare"]
    master.post(f"/master/contracte/creeaza/{firm_id}", data={
        "denumire": "Firma Test SRL", "adresa": "Adresa Test",
        "ciclu": ciclu, "suma": "100.00"})
    return c.get("/panou/contract")


def test_alege_plan_hides_payment_button_early_in_trial(app):
    c = app.test_client()
    inregistreaza(c, cui="RO201")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    r = c.get("/panou/plan")
    assert "Plătește acum".encode() not in r.data


def test_alege_plan_shows_payment_button_near_trial_end(app):
    c = app.test_client()
    inregistreaza(c, cui="RO202")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    _apropie_trial_de_final(app, "RO202")
    r = c.get("/panou/plan")
    assert "Plătește acum".encode() in r.data


def test_creeaza_cerere_plata_requires_ciclu_ales(app):
    c = app.test_client()
    inregistreaza(c, cui="RO203")
    _apropie_trial_de_final(app, "RO203")
    r = c.post("/panou/plata", data={}, follow_redirects=True)
    assert "Alege intai un ciclu".encode() in r.data


def _multiplicator_tva(app):
    """1 + cota_tva/100, citita din setari_tva - nu hardcodata in teste, la
    fel cum nu mai e hardcodata in cod (cota s-a schimbat deja o data,
    19% -> 21%, in timpul acestui proiect)."""
    from portal import db as pdb
    return 1 + pdb.get_cota_tva(app.portal_conn) / 100


def test_alege_plan_shows_unavailable_notice_when_plata_disabled(app, monkeypatch):
    import portal.app as app_module
    monkeypatch.setattr(app_module, "PLATA_ACTIVA", False)
    c = app.test_client()
    inregistreaza(c, cui="RO205")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    _apropie_trial_de_final(app, "RO205")
    r = c.get("/panou/plan")
    assert "Plătește acum".encode() not in r.data
    assert "temporar indisponibilă".encode() in r.data


def test_creeaza_cerere_plata_blocked_when_disabled(app, monkeypatch):
    """PLATA_ACTIVA implicit False in productie (vezi portal/app.py) - ruta
    redirectioneaza fara sa inregistreze nimic in payments, indiferent daca
    firma are deja ciclu ales/contract semnat."""
    import portal.app as app_module
    monkeypatch.setattr(app_module, "PLATA_ACTIVA", False)
    c = app.test_client()
    inregistreaza(c, cui="RO206", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    _apropie_trial_de_final(app, "RO206")
    _semneaza_contract_esemneaza(app, c)
    r = c.post("/panou/plata", data={}, follow_redirects=True)
    assert "temporar indisponibilă".encode() in r.data
    row = app.portal_conn.execute(
        "SELECT p.* FROM payments p JOIN firms f ON f.id=p.firm_id "
        "WHERE f.cui='RO206'").fetchone()
    assert row is None


def test_creeaza_cerere_plata_direct_firm(app):
    c = app.test_client()
    inregistreaza(c, cui="RO204", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    _apropie_trial_de_final(app, "RO204")
    _semneaza_contract_esemneaza(app, c)
    r = c.post("/panou/plata", data={"recurent": "on"}, follow_redirects=True)
    assert "Cererea de plata a fost inregistrata".encode() in r.data
    row = app.portal_conn.execute(
        "SELECT p.* FROM payments p JOIN firms f ON f.id=p.firm_id "
        "WHERE f.cui='RO204'").fetchone()
    # pret lunar direct (59, exclusiv TVA) x un singur ciclu de o luna,
    # apoi +TVA - payments.suma e suma efectiv ceruta clientului
    assert row["suma"] == round(59 * _multiplicator_tva(app), 2)
    assert row["recurent"] == 1
    assert row["stare"] == "in_asteptare"


def test_creeaza_cerere_plata_logs_audit(app):
    c = app.test_client()
    inregistreaza(c, cui="RO299", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    _apropie_trial_de_final(app, "RO299")
    _semneaza_contract_esemneaza(app, c)
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO299'").fetchone()["id"]
    c.post("/panou/plata", data={})
    payment_id = app.portal_conn.execute(
        "SELECT id FROM payments WHERE firm_id=?", (firm_id,)).fetchone()["id"]
    entry = app.firm_conn(firm_id).execute(
        "SELECT * FROM audit_log WHERE action='plata.cerere' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    assert entry is not None
    assert entry["entity"] == "payment"
    assert entry["entity_id"] == str(payment_id)


def test_creeaza_cerere_plata_contabilitate_firm_floors_at_one_client(app):
    """O firma de contabilitate abia inregistrata n-are inca niciun client
    - suma trebuie calculata ca pentru minim 1 client, nu 0 RON."""
    c = app.test_client()
    inregistreaza(c, cui="RO205", tip="contabilitate")
    c.post("/panou/plan", data={"ciclu": "an"})
    _apropie_trial_de_final(app, "RO205")
    _semneaza_contract_esemneaza(app, c)
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO205'").fetchone()["id"]
    c.post("/panou/plata", data={})
    row = app.portal_conn.execute(
        "SELECT suma FROM payments WHERE firm_id=?", (firm_id,)).fetchone()
    assert row["suma"] == round(15 * 12 * _multiplicator_tva(app), 2)


def test_master_plati_requires_master(app):
    c = app.test_client()
    r = c.get("/master/plati", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_valideaza_plata_requires_master(app):
    c = app.test_client()
    r = c.post("/master/plati/1/valideaza", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_valideaza_plata_creates_invoice_and_updates_state(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO206", tip="direct")
    c.post("/panou/plan", data={"ciclu": "6luni"})
    _apropie_trial_de_final(app, "RO206")
    _semneaza_contract_esemneaza(app, c)
    c.post("/panou/plata", data={})
    plata_id = app.portal_conn.execute(
        "SELECT p.id FROM payments p JOIN firms f ON f.id=p.firm_id "
        "WHERE f.cui='RO206'").fetchone()["id"]

    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c_master.post(f"/master/plati/{plata_id}/valideaza",
                      data={"judet": "Bucuresti"}, follow_redirects=True)
    assert "Incasarea a fost validata".encode() in r.data

    row = app.portal_conn.execute(
        "SELECT * FROM payments WHERE id=?", (plata_id,)).fetchone()
    assert row["stare"] == "validata"
    assert row["validat_de"] == "sef"
    assert row["invoice_id"] is not None

    factura = app.portal_conn.execute(
        "SELECT * FROM invoices WHERE id=?", (row["invoice_id"],)).fetchone()
    assert factura["valoare_neta"] == 49 * 6  # pret 6luni direct x 6 luni
    assert "6 luni".encode() in factura["descriere"].encode()
    # payments.suma (ce a fost cerut/incasat de la client) trebuie sa
    # coincida exact cu valoare_totala din factura - fara aceasta corectie,
    # clientul platea doar pretul de baza in timp ce factura declara un
    # total mai mare (cu TVA), niciodata incasat integral.
    assert row["suma"] == factura["valoare_totala"]
    assert row["suma"] == round(49 * 6 * _multiplicator_tva(app), 2)


def test_valideaza_plata_seteaza_abonament_activ_pana(app):
    from datetime import datetime
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO2061", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    _apropie_trial_de_final(app, "RO2061")
    _semneaza_contract_esemneaza(app, c)
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO2061'").fetchone()["id"]
    assert app.portal_conn.execute(
        "SELECT abonament_activ_pana FROM firms WHERE id=?",
        (firm_id,)).fetchone()["abonament_activ_pana"] is None

    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    def _valideaza_ultima_plata():
        plata_id = app.portal_conn.execute(
            "SELECT id FROM payments WHERE firm_id=? AND stare='in_asteptare'",
            (firm_id,)).fetchone()["id"]
        c_master.post(f"/master/plati/{plata_id}/valideaza",
                      data={"judet": "Bucuresti"})
        return app.portal_conn.execute(
            "SELECT abonament_activ_pana FROM firms WHERE id=?",
            (firm_id,)).fetchone()["abonament_activ_pana"]

    c.post("/panou/plata", data={})
    pana1 = _valideaza_ultima_plata()
    assert pana1 is not None  # de la NULL, prima plata

    # Renew anticipat (abonament_activ_pana ramas inca in viitor) - trebuie
    # sa adauge ciclul la finalul perioadei deja platite, nu de la "acum",
    # altfel firma ar pierde zile platite deja la fiecare renew din timp.
    c.post("/panou/plata", data={})
    pana2 = _valideaza_ultima_plata()
    assert pana2 > pana1
    delta_zile = (datetime.fromisoformat(pana2) - datetime.fromisoformat(pana1)).days
    assert delta_zile == 30  # o luna, aproximat ca in pdb.TRIAL_ZILE


def test_valideaza_plata_rejects_already_validated(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO207", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    _apropie_trial_de_final(app, "RO207")
    _semneaza_contract_esemneaza(app, c)
    c.post("/panou/plata", data={})
    plata_id = app.portal_conn.execute(
        "SELECT p.id FROM payments p JOIN firms f ON f.id=p.firm_id "
        "WHERE f.cui='RO207'").fetchone()["id"]

    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post(f"/master/plati/{plata_id}/valideaza", data={"judet": "Bucuresti"})
    r = c_master.post(f"/master/plati/{plata_id}/valideaza",
                      data={"judet": "Bucuresti"}, follow_redirects=True)
    assert "deja validata".encode() in r.data


def test_postgres_pool_serves_concurrent_checkouts_without_blocking(app):
    """Dovada directa a fix-ului de concurenta: inainte de introducerea
    pool-ului, exista o singura conexiune Postgres pentru tot procesul,
    serializata de un lock global de request - o operatie lenta (ex.
    apelul FGO din valideaza_plata) bloca literalmente orice alta cerere.
    Acum fiecare cerere primeste propria conexiune din pool, deci o
    conexiune tinuta ocupata multa vreme de un thread nu mai intarzie o
    a doua conexiune ceruta de alt thread."""
    if os.environ.get("ETVA_TEST_PG") != "1":
        pytest.skip("pool-ul de conexiuni exista doar pe backend-ul Postgres")
    import threading
    import time

    pool = app.db_pool
    slow_checked_out = threading.Event()

    def _tine_conexiunea_ocupata():
        conn = pool.getconn()
        try:
            slow_checked_out.set()
            time.sleep(0.6)
            conn.execute("SELECT 1")
        finally:
            pool.putconn(conn)

    t = threading.Thread(target=_tine_conexiunea_ocupata)
    t.start()
    assert slow_checked_out.wait(timeout=2), "thread-ul lent nu a apucat conexiunea"

    inceput = time.monotonic()
    conn2 = pool.getconn()
    try:
        conn2.execute("SELECT 1")
    finally:
        pool.putconn(conn2)
    durata = time.monotonic() - inceput

    t.join(timeout=2)
    assert durata < 0.3, (
        f"a doua conexiune a asteptat {durata:.2f}s dupa cea lenta - "
        "pool-ul serializeaza cererile in loc sa le izoleze")


def test_alege_plan_shows_12_month_payment_history(app):
    c = app.test_client()
    inregistreaza(c, cui="RO208", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    _apropie_trial_de_final(app, "RO208")
    _semneaza_contract_esemneaza(app, c)
    c.post("/panou/plata", data={})
    r = c.get("/panou/plan")
    assert "Istoricul pl".encode() in r.data
    assert "59.00".encode() in r.data or "59.0".encode() in r.data


def test_master_nomenclator_requires_master(app):
    c = app.test_client()
    r = c.get("/master/nomenclator", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_salveaza_nomenclator_requires_master(app):
    c = app.test_client()
    r = c.post("/master/nomenclator", data={}, follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_master_nomenclator_shows_current_prices(app):
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c.get("/master/nomenclator")
    assert b'value="59' in r.data  # pret lunar firma directa
    assert b'value="25' in r.data  # pret lunar firma contabilitate


def _preturi_form(**overrides):
    valori = {"pret_direct_lunar": "59", "pret_direct_6luni": "49", "pret_direct_an": "39",
             "pret_contabilitate_lunar": "25", "pret_contabilitate_6luni": "20",
             "pret_contabilitate_an": "15"}
    valori.update(overrides)
    return valori


def test_salveaza_nomenclator_rejects_invalid_price(app):
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c.post("/master/nomenclator",
              data=_preturi_form(pret_direct_lunar="abc"), follow_redirects=True)
    assert "numar pozitiv".encode() in r.data
    from portal import db as pdb
    assert pdb.get_preturi(app.portal_conn)["direct"]["lunar"] == 59  # neschimbat


def test_salveaza_nomenclator_rejects_zero_or_negative_price(app):
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c.post("/master/nomenclator",
              data=_preturi_form(pret_direct_lunar="0"), follow_redirects=True)
    assert "numar pozitiv".encode() in r.data


def test_salveaza_nomenclator_updates_prices(app):
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c.post("/master/nomenclator",
              data=_preturi_form(pret_direct_lunar="99"), follow_redirects=True)
    assert "Preturile au fost actualizate".encode() in r.data
    from portal import db as pdb
    assert pdb.get_preturi(app.portal_conn)["direct"]["lunar"] == 99


def test_updated_nomenclator_price_is_used_by_payment_calculation(app):
    """Pretul modificat din nomenclator trebuie sa se reflecte imediat in
    suma de plata calculata pentru firme, nu doar in pagina de nomenclator."""
    _seed_master(app)
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post("/master/nomenclator", data=_preturi_form(pret_direct_lunar="100"))

    c = app.test_client()
    inregistreaza(c, cui="RO209", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    _apropie_trial_de_final(app, "RO209")
    _semneaza_contract_esemneaza(app, c)
    c.post("/panou/plata", data={})
    row = app.portal_conn.execute(
        "SELECT p.suma FROM payments p JOIN firms f ON f.id=p.firm_id "
        "WHERE f.cui='RO209'").fetchone()
    assert row["suma"] == round(100 * _multiplicator_tva(app), 2)


def test_inregistrare_direct_cere_estimarea_reconcilierilor(app):
    """O firma directa isi declara la inregistrare numarul minim estimat de
    reconcilieri lunare - fara el, contul nu se creeaza."""
    c = app.test_client()
    r = inregistreaza(c, cui="RO771", tip="direct", reconcilieri_estimate="")
    assert "numarul minim estimat de reconcilieri".encode() in r.data
    assert not app.portal_conn.execute(
        "SELECT 1 FROM firms WHERE cui='RO771'").fetchone()

    r = inregistreaza(c, cui="RO771", tip="direct", reconcilieri_estimate=120)
    assert r.status_code == 302
    row = app.portal_conn.execute(
        "SELECT reconcilieri_lunare_estimate FROM firms WHERE cui='RO771'"
    ).fetchone()
    assert row["reconcilieri_lunare_estimate"] == 120


def test_inregistrare_contabilitate_nu_cere_estimarea(app):
    """Firmele de contabilitate platesc per client, nu pe volum - campul de
    estimare nu li se cere si ramane NULL."""
    c = app.test_client()
    r = inregistreaza(c, cui="RO772", tip="contabilitate")
    assert r.status_code == 302
    row = app.portal_conn.execute(
        "SELECT reconcilieri_lunare_estimate FROM firms WHERE cui='RO772'"
    ).fetchone()
    assert row["reconcilieri_lunare_estimate"] is None


def test_pachete_extra_peste_prag_intra_in_suma_de_plata(app):
    """Abonament standard + reconcilieri: o firma directa care estimeaza
    peste pragul inclus (100) plateste pachete extra - cu valorile initiale
    (pachete de 50 la 19 RON/luna), 180 estimate inseamna 2 pachete."""
    c = app.test_client()
    inregistreaza(c, cui="RO773", tip="direct", reconcilieri_estimate=180)
    c.post("/panou/plan", data={"ciclu": "lunar",
                                "reconcilieri_estimate": "180"})
    _apropie_trial_de_final(app, "RO773")
    _semneaza_contract_esemneaza(app, c)
    c.post("/panou/plata", data={})
    row = app.portal_conn.execute(
        "SELECT p.suma FROM payments p JOIN firms f ON f.id=p.firm_id "
        "WHERE f.cui='RO773'").fetchone()
    assert row["suma"] == round((59 + 2 * 19) * _multiplicator_tva(app), 2)


def test_pachete_extra_sub_prag_nu_se_factureaza(app):
    """Sub pragul inclus, firma directa plateste doar abonamentul standard."""
    c = app.test_client()
    inregistreaza(c, cui="RO774", tip="direct", reconcilieri_estimate=40)
    c.post("/panou/plan", data={"ciclu": "lunar",
                                "reconcilieri_estimate": "40"})
    _apropie_trial_de_final(app, "RO774")
    _semneaza_contract_esemneaza(app, c)
    c.post("/panou/plata", data={})
    row = app.portal_conn.execute(
        "SELECT p.suma FROM payments p JOIN firms f ON f.id=p.firm_id "
        "WHERE f.cui='RO774'").fetchone()
    assert row["suma"] == round(59 * _multiplicator_tva(app), 2)


def test_salveaza_pachet_reconcilieri_din_nomenclator(app):
    """Masterul poate modifica pragul inclus, marimea pachetului si pretul
    lui - iar noile valori se reflecta imediat in suma de plata."""
    from portal import db as pdb
    _seed_master(app)
    c_master = app.test_client()
    c_master.post("/autentificare",
                  data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c_master.post("/master/nomenclator/pachete", data={
        "reconcilieri_incluse": "10", "marime_pachet": "20",
        "pret_pachet_lunar": "5"}, follow_redirects=True)
    assert "au fost actualizate".encode() in r.data
    pachet = pdb.get_pachet_reconcilieri(app.portal_conn)
    assert (pachet["reconcilieri_incluse"], pachet["marime_pachet"],
            pachet["pret_pachet_lunar_ron"]) == (10, 20, 5)

    c = app.test_client()
    inregistreaza(c, cui="RO775", tip="direct", reconcilieri_estimate=50)
    c.post("/panou/plan", data={"ciclu": "lunar",
                                "reconcilieri_estimate": "50"})
    _apropie_trial_de_final(app, "RO775")
    _semneaza_contract_esemneaza(app, c)
    c.post("/panou/plata", data={})
    row = app.portal_conn.execute(
        "SELECT p.suma FROM payments p JOIN firms f ON f.id=p.firm_id "
        "WHERE f.cui='RO775'").fetchone()
    # 50 estimate - 10 incluse = 40 peste prag -> 2 pachete de 20 la 5 RON
    assert row["suma"] == round((59 + 2 * 5) * _multiplicator_tva(app), 2)


def test_salveaza_pachet_reconcilieri_respinge_valori_invalide(app):
    from portal import db as pdb
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c.post("/master/nomenclator/pachete", data={
        "reconcilieri_incluse": "0", "marime_pachet": "50",
        "pret_pachet_lunar": "19"}, follow_redirects=True)
    assert "numere intregi pozitive".encode() in r.data
    assert pdb.get_pachet_reconcilieri(
        app.portal_conn)["reconcilieri_incluse"] == 100


def test_adaugare_client_cere_confirmarea_gdpr(app):
    """GDPR: firma de contabilitate nu poate adauga un client fara sa
    confirme ca are mandat de prelucrare a datelor lui; confirmarea ramane
    pe randul clientului si in jurnalul de audit."""
    c = app.test_client()
    inregistreaza(c, cui="RO776", tip="contabilitate")
    r = c.post("/api/clients", json={"cui": "RO9991", "name": "Fara Acord"})
    assert r.status_code == 400
    assert "bifa GDPR" in r.get_json()["error"]

    cid = c.post("/api/clients", json={
        "cui": "RO9991", "name": "Cu Acord",
        "gdpr_confirmat": True}).get_json()["id"]
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO776'").fetchone()["id"]
    fc = app.firm_conn(firm_id)
    client = fc.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    assert bool(client["gdpr_confirmat"])
    assert client["gdpr_confirmat_de"] == "firma-unu-srl"
    assert client["gdpr_confirmat_la"]
    actiuni = [r["action"] for r in fc.execute(
        "SELECT action FROM audit_log WHERE entity_id=?", (str(cid),))]
    assert "client.gdpr_confirmare" in actiuni
    # starea apare si in lista de clienti servita SPA-ului
    lista = c.get("/api/clients").get_json()
    assert [cl for cl in lista if cl["id"] == cid][0]["gdpr_confirmat"]


def test_salveaza_cota_tva_requires_master(app):
    c = app.test_client()
    r = c.post("/master/nomenclator/tva", data={"cota_tva": "21"},
              follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_salveaza_cota_tva_updates_value(app):
    from portal import db as pdb
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c.post("/master/nomenclator/tva", data={"cota_tva": "22"}, follow_redirects=True)
    assert "Cota de TVA a fost actualizata".encode() in r.data
    assert pdb.get_cota_tva(app.portal_conn) == 22


def test_salveaza_cota_tva_rejects_invalid_value(app):
    from portal import db as pdb
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    cota_initiala = pdb.get_cota_tva(app.portal_conn)
    r = c.post("/master/nomenclator/tva", data={"cota_tva": "0"}, follow_redirects=True)
    assert "numar intre 0 si 100".encode() in r.data
    assert pdb.get_cota_tva(app.portal_conn) == cota_initiala


def test_updated_cota_tva_is_used_by_payment_calculation(app):
    """Cota de TVA modificata din nomenclator trebuie sa se reflecte imediat
    in suma de plata calculata pentru firme, la fel ca preturile."""
    from portal import db as pdb
    _seed_master(app)
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post("/master/nomenclator/tva", data={"cota_tva": "22"})

    c = app.test_client()
    inregistreaza(c, cui="RO210", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    _apropie_trial_de_final(app, "RO210")
    _semneaza_contract_esemneaza(app, c)
    c.post("/panou/plata", data={})
    row = app.portal_conn.execute(
        "SELECT p.suma FROM payments p JOIN firms f ON f.id=p.firm_id "
        "WHERE f.cui='RO210'").fetchone()
    assert row["suma"] == round(59 * 1.22, 2)


def test_master_nomenclator_shows_cota_tva_history(app):
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c.post("/master/nomenclator/tva", data={"cota_tva": "22"})
    r = c.get("/master/nomenclator")
    assert "21.0%".encode() in r.data
    assert "22.0%".encode() in r.data
    assert "Activă".encode() in r.data
    assert "Inactivă".encode() in r.data


def test_activeaza_cota_tva_requires_master(app):
    c = app.test_client()
    r = c.post("/master/nomenclator/tva/1/activeaza", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_activeaza_cota_tva_route_reactivates_old_rate(app):
    from portal import db as pdb
    _seed_master(app)
    id_initial = pdb.listeaza_cote_tva(app.portal_conn)[0]["id"]
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c.post("/master/nomenclator/tva", data={"cota_tva": "22"})
    assert pdb.get_cota_tva(app.portal_conn) == 22

    r = c.post(f"/master/nomenclator/tva/{id_initial}/activeaza", follow_redirects=True)
    assert "Cota de TVA a fost reactivata".encode() in r.data
    assert pdb.get_cota_tva(app.portal_conn) == 21


def test_activeaza_cota_tva_route_rejects_missing_id(app):
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c.post("/master/nomenclator/tva/9999/activeaza", follow_redirects=True)
    assert "Cota de TVA nu a fost gasita".encode() in r.data


def test_master_backup_list_requires_master(app):
    c = app.test_client()
    r = c.get("/master/backup", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_creeaza_backup_requires_master(app):
    c = app.test_client()
    r = c.post("/master/backup/creeaza", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


@doar_sqlite
def test_creeaza_backup_produces_downloadable_zip_and_logs_action(app):
    import zipfile
    _seed_master(app)
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    r = c_master.post("/master/backup/creeaza", follow_redirects=True)
    assert "Backup creat".encode() in r.data

    row = app.portal_conn.execute(
        "SELECT actiune, detalii FROM master_actions "
        "WHERE actiune='backup_creat' ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None and row["detalii"].endswith(".zip")

    r_lista = c_master.get("/master/backup")
    assert row["detalii"].encode() in r_lista.data

    r_descarca = c_master.get(f"/master/backup/{row['detalii']}/descarca")
    assert r_descarca.status_code == 200
    import io
    with zipfile.ZipFile(io.BytesIO(r_descarca.data)) as zf:
        assert "portal.db" in zf.namelist()
        assert "secret.key" in zf.namelist()


def test_descarca_backup_rejects_unknown_names(app):
    """Path-traversal rejection itself (e.g. "../secret.key") is covered at
    the unit level in test_backup.py::test_backup_path_rejects_traversal_
    and_bad_names - URL routing may mangle a raw ".." segment before it
    ever reaches the view, so this only exercises names that reach
    descarca_backup() unchanged: a real file outside the naming pattern,
    and a validly-named backup that doesn't exist."""
    _seed_master(app)
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    for nume in ("portal.db", "etva-backup-20260101-000000.zip"):
        r = c_master.get(f"/master/backup/{nume}/descarca", follow_redirects=False)
        assert r.status_code == 302 and "/master/backup" in r.headers["Location"]


# ---------- schimbare self-service de plan (upgrade/downgrade + contract automat) ----------

def _firma_cu_abonament_platit(app, cui, tip="contabilitate", ciclu="lunar",
                               risc_fiscal_nivel=None, reconcilieri_estimate=None,
                               seed_master=True, email="test@exemplu.ro"):
    """Inregistreaza o firma, ii alege un plan initial, ii semneaza
    contractul (mock eSemneaza) si valideaza o prima plata de abonament -
    starea de baza necesara pentru orice test al schimbarii self-service de
    plan (are_plata_validata=True in schimba_plan), dar si pentru accesul
    efectiv la modulul Risc Fiscal (current_identity() cere acum o plata
    validata, nu doar risc_fiscal_nivel ales - vezi portal/app.py).
    seed_master=False pentru un al doilea apel in acelasi test (contul
    "sef" nu poate fi creat de doua ori). Returneaza (client firma, firm_id,
    client master deja autentificat)."""
    if seed_master:
        _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui=cui, tip=tip, email=email,
                 reconcilieri_estimate=reconcilieri_estimate)
    data = {"ciclu": ciclu}
    if tip == "direct":
        data["reconcilieri_estimate"] = str(reconcilieri_estimate or 10)
    if risc_fiscal_nivel:
        data["risc_fiscal_nivel"] = risc_fiscal_nivel
    c.post("/panou/plan", data=data)
    _apropie_trial_de_final(app, cui)
    _semneaza_contract_esemneaza(app, c)
    c.post("/panou/plata", data={})
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui=?", (cui,)).fetchone()["id"]
    plata_id = app.portal_conn.execute(
        "SELECT id FROM payments WHERE firm_id=? AND stare=?",
        (firm_id, "in_asteptare")).fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post(f"/master/plati/{plata_id}/valideaza", data={"judet": "Bucuresti"})
    return c, firm_id, c_master


def test_schimba_plan_fallback_fara_ciclu_ales(app):
    """Firma care inca nu si-a ales niciun ciclu de facturare (prima
    alegere) foloseste tot fluxul vechi, simplu, direct pe firms - nicio
    logica de contract/timing nu se aplica inainte de prima alegere."""
    c = app.test_client()
    inregistreaza(c, cui="RO301")
    r = c.post("/panou/plan/schimbare", data={"ciclu": "an"}, follow_redirects=True)
    assert "Planul a fost salvat".encode() in r.data
    row = app.portal_conn.execute(
        "SELECT ciclu_facturare FROM firms WHERE cui='RO301'").fetchone()
    assert row["ciclu_facturare"] == "an"


def test_schimba_plan_fara_plata_validata_actualizeaza_direct(app):
    """Firma care nu a platit inca niciodata (dar are deja ciclu ales si
    contract semnat) - update direct pe firms, fara niciun contract nou."""
    c = app.test_client()
    inregistreaza(c, cui="RO302", tip="contabilitate")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    _apropie_trial_de_final(app, "RO302")
    _semneaza_contract_esemneaza(app, c)
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO302'").fetchone()["id"]
    n_contracte_inainte = app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM contracts WHERE firm_id=?",
        (firm_id,)).fetchone()["n"]

    r = c.post("/panou/plan/schimbare",
              data={"ciclu": "lunar", "risc_fiscal_nivel": "simplu"},
              follow_redirects=True)
    assert "Planul a fost actualizat".encode() in r.data
    firm = app.portal_conn.execute(
        "SELECT * FROM firms WHERE id=?", (firm_id,)).fetchone()
    assert firm["risc_fiscal_nivel"] == "simplu"
    n_contracte_dupa = app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM contracts WHERE firm_id=?",
        (firm_id,)).fetchone()["n"]
    assert n_contracte_dupa == n_contracte_inainte


def test_schimba_plan_nimic_schimbat_e_no_op(app):
    c, firm_id, _ = _firma_cu_abonament_platit(app, "RO303")
    r = c.post("/panou/plan/schimbare", data={"ciclu": "lunar"},
              follow_redirects=True)
    assert "Nu ai schimbat nimic".encode() in r.data


def test_schimba_plan_upgrade_fara_timing_arata_alegerea(app):
    c, firm_id, _ = _firma_cu_abonament_platit(app, "RO304")
    r = c.post("/panou/plan/schimbare",
              data={"ciclu": "lunar", "risc_fiscal_nivel": "simplu"},
              follow_redirects=False)
    assert r.status_code == 302
    assert "alegere_timing=1" in r.headers["Location"]
    firm = app.portal_conn.execute(
        "SELECT risc_fiscal_nivel FROM firms WHERE id=?", (firm_id,)).fetchone()
    assert firm["risc_fiscal_nivel"] is None  # nimic scris inca
    assert app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM plan_schimbari_programate WHERE firm_id=?",
        (firm_id,)).fetchone()["n"] == 0

    r2 = c.get(r.headers["Location"], follow_redirects=True)
    assert "imediat".encode() in r2.data.lower() or "programat".encode() in r2.data.lower() \
        or b"200" in r2.data  # pagina reda macar diferenta de cost undeva


def test_schimba_plan_upgrade_programat_creeaza_schimbare_fara_contract(app):
    c, firm_id, _ = _firma_cu_abonament_platit(app, "RO305")
    n_contracte_inainte = app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM contracts WHERE firm_id=?",
        (firm_id,)).fetchone()["n"]

    r = c.post("/panou/plan/schimbare",
              data={"ciclu": "lunar", "risc_fiscal_nivel": "simplu",
                    "timing": "programat"},
              follow_redirects=True)
    assert "programata".encode() in r.data

    rand = app.portal_conn.execute(
        "SELECT * FROM plan_schimbari_programate WHERE firm_id=?",
        (firm_id,)).fetchone()
    assert rand is not None
    assert rand["stare"] == "in_asteptare"
    assert rand["risc_fiscal_nivel_nou"] == "simplu"
    assert rand["tip"] == "upgrade"

    firm = app.portal_conn.execute(
        "SELECT risc_fiscal_nivel FROM firms WHERE id=?", (firm_id,)).fetchone()
    assert firm["risc_fiscal_nivel"] is None  # neaplicat inca
    n_contracte_dupa = app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM contracts WHERE firm_id=?",
        (firm_id,)).fetchone()["n"]
    assert n_contracte_dupa == n_contracte_inainte  # niciun contract nou


def test_schimba_plan_upgrade_imediat_trimite_contract_si_plata_diferenta(app):
    c, firm_id, _ = _firma_cu_abonament_platit(app, "RO306")

    r = c.post("/panou/plan/schimbare",
              data={"ciclu": "lunar", "risc_fiscal_nivel": "simplu",
                    "timing": "imediat"},
              follow_redirects=True)
    assert "diferenta".encode() in r.data.lower() or "diferență".encode() in r.data

    contract = app.portal_conn.execute(
        "SELECT * FROM contracts WHERE firm_id=? ORDER BY id DESC LIMIT 1",
        (firm_id,)).fetchone()
    assert contract is not None
    assert contract["stare"] == "in_asteptare"
    assert contract["esemneaza_request_id"] == "fake-request-id"

    plata = app.portal_conn.execute(
        "SELECT * FROM payments WHERE firm_id=? AND tip=?",
        (firm_id, "diferenta_upgrade")).fetchone()
    assert plata is not None
    assert plata["stare"] == "in_asteptare"
    assert plata["contract_id"] == contract["id"]
    assert plata["risc_fiscal_nivel_nou"] == "simplu"
    # diferenta = 200 RON (pretul modulului simplu) - firma nu avea niciun
    # nivel activ inainte - cu TVA aplicat, la fel ca orice alta suma ceruta.
    assert plata["suma"] == round(200 * _multiplicator_tva(app), 2)

    firm = app.portal_conn.execute(
        "SELECT risc_fiscal_nivel FROM firms WHERE id=?", (firm_id,)).fetchone()
    assert firm["risc_fiscal_nivel"] is None  # neaplicat pana la validarea platii


def test_valideaza_plata_diferenta_upgrade_refuza_contract_nesemnat(app):
    c, firm_id, c_master = _firma_cu_abonament_platit(app, "RO311")
    c.post("/panou/plan/schimbare",
          data={"ciclu": "lunar", "risc_fiscal_nivel": "simplu",
                "timing": "imediat"})
    plata_id = app.portal_conn.execute(
        "SELECT id FROM payments WHERE firm_id=? AND tip=?",
        (firm_id, "diferenta_upgrade")).fetchone()["id"]

    r = c_master.post(f"/master/plati/{plata_id}/valideaza",
                      data={"judet": "Bucuresti"}, follow_redirects=True)
    assert "nu este inca semnat".encode() in r.data

    plata = app.portal_conn.execute(
        "SELECT stare FROM payments WHERE id=?", (plata_id,)).fetchone()
    assert plata["stare"] == "in_asteptare"  # claim-ul a fost eliberat
    firm = app.portal_conn.execute(
        "SELECT risc_fiscal_nivel FROM firms WHERE id=?", (firm_id,)).fetchone()
    assert firm["risc_fiscal_nivel"] is None


def test_valideaza_plata_diferenta_upgrade_aplica_planul_imediat(app):
    c, firm_id, c_master = _firma_cu_abonament_platit(app, "RO312")
    c.post("/panou/plan/schimbare",
          data={"ciclu": "lunar", "risc_fiscal_nivel": "simplu",
                "timing": "imediat"})
    c.get("/panou/contract")  # semneaza contractul nou (mock eSemneaza)
    pana_inainte = app.portal_conn.execute(
        "SELECT abonament_activ_pana FROM firms WHERE id=?",
        (firm_id,)).fetchone()["abonament_activ_pana"]

    plata_id = app.portal_conn.execute(
        "SELECT id FROM payments WHERE firm_id=? AND tip=?",
        (firm_id, "diferenta_upgrade")).fetchone()["id"]
    r = c_master.post(f"/master/plati/{plata_id}/valideaza",
                      data={"judet": "Bucuresti"}, follow_redirects=True)
    assert "validata".encode() in r.data

    plata = app.portal_conn.execute(
        "SELECT * FROM payments WHERE id=?", (plata_id,)).fetchone()
    assert plata["stare"] == "validata"
    assert plata["invoice_id"] is not None

    factura = app.portal_conn.execute(
        "SELECT * FROM invoices WHERE id=?", (plata["invoice_id"],)).fetchone()
    assert "Diferenta upgrade".encode() in factura["descriere"].encode()
    assert factura["valoare_totala"] == plata["suma"]

    firm = app.portal_conn.execute(
        "SELECT risc_fiscal_nivel, abonament_activ_pana FROM firms WHERE id=?",
        (firm_id,)).fetchone()
    assert firm["risc_fiscal_nivel"] == "simplu"  # aplicat imediat
    assert firm["abonament_activ_pana"] == pana_inainte  # neatins


def test_schimba_plan_downgrade_e_mereu_programat_ignora_timing_imediat(app):
    c, firm_id, _ = _firma_cu_abonament_platit(
        app, "RO307", risc_fiscal_nivel="complet")

    r = c.post("/panou/plan/schimbare",
              data={"ciclu": "lunar", "timing": "imediat"},  # cerere de downgrade
              follow_redirects=True)
    assert "programata".encode() in r.data

    rand = app.portal_conn.execute(
        "SELECT * FROM plan_schimbari_programate WHERE firm_id=?",
        (firm_id,)).fetchone()
    assert rand is not None
    assert rand["tip"] == "downgrade"
    assert rand["risc_fiscal_nivel_nou"] is None
    assert app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM payments WHERE firm_id=? AND tip=?",
        (firm_id, "diferenta_upgrade")).fetchone()["n"] == 0


def test_schimba_plan_schimbare_ciclu_e_mereu_programata(app):
    c, firm_id, _ = _firma_cu_abonament_platit(app, "RO308")
    r = c.post("/panou/plan/schimbare",
              data={"ciclu": "an", "timing": "imediat"},
              follow_redirects=True)
    assert "programata".encode() in r.data
    rand = app.portal_conn.execute(
        "SELECT * FROM plan_schimbari_programate WHERE firm_id=?",
        (firm_id,)).fetchone()
    assert rand is not None
    assert rand["ciclu_facturare_nou"] == "an"
    firm = app.portal_conn.execute(
        "SELECT ciclu_facturare FROM firms WHERE id=?", (firm_id,)).fetchone()
    assert firm["ciclu_facturare"] == "lunar"  # neaplicat inca


def test_schimba_plan_blocheaza_a_doua_cerere_cat_timp_prima_e_in_asteptare(app):
    c, firm_id, _ = _firma_cu_abonament_platit(app, "RO309")
    c.post("/panou/plan/schimbare",
          data={"ciclu": "lunar", "risc_fiscal_nivel": "simplu",
                "timing": "programat"})
    r = c.post("/panou/plan/schimbare",
              data={"ciclu": "lunar", "risc_fiscal_nivel": "complet",
                    "timing": "programat"},
              follow_redirects=True)
    assert "deja o schimbare de plan".encode() in r.data
    n = app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM plan_schimbari_programate WHERE firm_id=?",
        (firm_id,)).fetchone()["n"]
    assert n == 1


def test_creeaza_cerere_plata_blocata_de_plata_diferenta_activa(app):
    """Dupa ce noul contract (generat pentru upgrade-ul imediat) e semnat -
    _mock_esemneaza raporteaza implicit APPLIED la prima verificare, deci o
    vizita pe /panou/contract il finalizeaza - gate-ul vechi pe "contract
    nesemnat" nu mai blocheaza nimic (ciclul nu s-a schimbat); doar gate-ul
    nou, dedicat platii diferenta_upgrade inca nevalidate, trebuie sa
    blocheze o noua cerere de plata normala."""
    c, firm_id, _ = _firma_cu_abonament_platit(app, "RO310")
    c.post("/panou/plan/schimbare",
          data={"ciclu": "lunar", "risc_fiscal_nivel": "simplu",
                "timing": "imediat"})
    c.get("/panou/contract")
    r = c.post("/panou/plata", data={}, follow_redirects=True)
    assert "schimbare de plan in asteptare".encode() in r.data
    n = app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM payments WHERE firm_id=? AND tip=?",
        (firm_id, "abonament")).fetchone()["n"]
    # doar plata initiala (deja validata) - nicio a doua plata de abonament
    assert n == 1


def test_anuleaza_schimbare_plan_programata(app):
    c, firm_id, _ = _firma_cu_abonament_platit(app, "RO313")
    c.post("/panou/plan/schimbare",
          data={"ciclu": "lunar", "risc_fiscal_nivel": "simplu",
                "timing": "programat"})
    r = c.post("/panou/plan/schimbare/anuleaza", follow_redirects=True)
    assert "programata a fost anulata".encode() in r.data

    rand = app.portal_conn.execute(
        "SELECT * FROM plan_schimbari_programate WHERE firm_id=?",
        (firm_id,)).fetchone()
    assert rand["stare"] == "anulata"
    assert rand["anulata_de"]

    # Dupa anulare, firma poate cere din nou o schimbare (guard-ul nu mai
    # gaseste nicio schimbare "in_asteptare").
    r2 = c.post("/panou/plan/schimbare",
               data={"ciclu": "lunar", "risc_fiscal_nivel": "complet",
                     "timing": "programat"},
               follow_redirects=True)
    assert "programata".encode() in r2.data
    n = app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM plan_schimbari_programate WHERE firm_id=? "
        "AND stare='in_asteptare'", (firm_id,)).fetchone()["n"]
    assert n == 1


def test_anuleaza_schimbare_plan_imediata_cu_contract_nesemnat(app):
    c, firm_id, _ = _firma_cu_abonament_platit(app, "RO314")
    c.post("/panou/plan/schimbare",
          data={"ciclu": "lunar", "risc_fiscal_nivel": "simplu",
                "timing": "imediat"})
    contract_id = app.portal_conn.execute(
        "SELECT id FROM contracts WHERE firm_id=? ORDER BY id DESC LIMIT 1",
        (firm_id,)).fetchone()["id"]

    r = c.post("/panou/plan/schimbare/anuleaza", follow_redirects=True)
    assert "a fost anulata".encode() in r.data

    contract = app.portal_conn.execute(
        "SELECT stare FROM contracts WHERE id=?", (contract_id,)).fetchone()
    assert contract["stare"] == "anulat"
    plata = app.portal_conn.execute(
        "SELECT stare FROM payments WHERE firm_id=? AND tip=?",
        (firm_id, "diferenta_upgrade")).fetchone()
    assert plata["stare"] == "anulata"

    # _contract_curent revine la vechiul contract semnat - o plata normala
    # merge din nou fara nicio blocare.
    r2 = c.post("/panou/plata", data={}, follow_redirects=True)
    assert "inregistrata".encode() in r2.data


def test_anuleaza_schimbare_plan_refuza_daca_deja_semnat(app):
    c, firm_id, _ = _firma_cu_abonament_platit(app, "RO315")
    c.post("/panou/plan/schimbare",
          data={"ciclu": "lunar", "risc_fiscal_nivel": "simplu",
                "timing": "imediat"})
    c.get("/panou/contract")  # semneaza (mock eSemneaza -> APPLIED)

    r = c.post("/panou/plan/schimbare/anuleaza", follow_redirects=True)
    assert "deja semnat".encode() in r.data

    plata = app.portal_conn.execute(
        "SELECT stare FROM payments WHERE firm_id=? AND tip=?",
        (firm_id, "diferenta_upgrade")).fetchone()
    assert plata["stare"] == "in_asteptare"  # neatinsa


def test_anuleaza_schimbare_plan_fara_nimic_de_anulat(app):
    c, firm_id, _ = _firma_cu_abonament_platit(app, "RO316")
    r = c.post("/panou/plan/schimbare/anuleaza", follow_redirects=True)
    assert "nicio schimbare de plan de anulat".encode() in r.data


# ---------- scheduler: aplicare schimbari de plan programate ----------

def _insereaza_firma_minimala(app, cui="RO900", name="Firma Minimala SRL"):
    from etva import dbcompat
    return dbcompat.insert_id(
        app.portal_conn, "INSERT INTO firms(name, cui) VALUES (?,?)", (name, cui))


def _insereaza_schimbare_programata(app, firm_id, aplica_la, ciclu_nou="lunar"):
    from datetime import datetime, timezone
    from etva import dbcompat
    return dbcompat.insert_id(
        app.portal_conn,
        "INSERT INTO plan_schimbari_programate(firm_id, ciclu_facturare_nou, "
        "tip, aplica_la, solicitat_de, creat_la) VALUES(?,?,?,?,?,?)",
        (firm_id, ciclu_nou, "upgrade", aplica_la, "admin",
         datetime.now(timezone.utc).isoformat()))


def _insereaza_contract_minimal(app, firm_id, numar):
    """contract_id-ul intors de aplica_una_fn trebuie sa fie un rand real din
    contracts - Postgres impune FK-ul plan_schimbari_programate.contract_id,
    spre deosebire de SQLite (foreign_keys implicit oprit)."""
    from datetime import datetime, timezone
    from etva import dbcompat
    return dbcompat.insert_id(
        app.portal_conn,
        "INSERT INTO contracts(firm_id, numar, ciclu_facturare, suma, "
        "beneficiar_denumire, beneficiar_cui, beneficiar_adresa, stare, "
        "creat_la) VALUES(?,?,?,?,?,?,?,?,?)",
        (firm_id, numar, "lunar", 100.0, "Firma Test SRL", "RO900", "Adresa",
         "in_asteptare", datetime.now(timezone.utc).isoformat()))


def test_aplica_schimbari_programate_aplica_doar_ce_e_scadent(app):
    from datetime import datetime, timedelta, timezone
    from portal import plan_schimbari as plan_schimbari_mod
    # Doua firme distincte - indexul unic partial permite cel mult o
    # schimbare 'in_asteptare' per firma, deci nu pot coexista doua randuri
    # pe aceeasi firma pentru acest test.
    firm_id_scadent = _insereaza_firma_minimala(app, cui="RO900")
    firm_id_viitor = _insereaza_firma_minimala(app, cui="RO901")
    contract_id = _insereaza_contract_minimal(app, firm_id_scadent, numar=90001)
    trecut = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    viitor = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    id_scadent = _insereaza_schimbare_programata(app, firm_id_scadent, trecut)
    id_viitor = _insereaza_schimbare_programata(app, firm_id_viitor, viitor)

    apeluri = []

    def _stub(row):
        apeluri.append(row["id"])
        return contract_id

    n = plan_schimbari_mod.aplica_schimbari_programate(app.portal_conn, _stub)
    assert n == 1
    assert apeluri == [id_scadent]

    rand_scadent = app.portal_conn.execute(
        "SELECT * FROM plan_schimbari_programate WHERE id=?",
        (id_scadent,)).fetchone()
    assert rand_scadent["stare"] == "aplicata"
    assert rand_scadent["contract_id"] == contract_id
    assert rand_scadent["aplicata_la"] is not None

    rand_viitor = app.portal_conn.execute(
        "SELECT * FROM plan_schimbari_programate WHERE id=?",
        (id_viitor,)).fetchone()
    assert rand_viitor["stare"] == "in_asteptare"


def test_aplica_schimbari_programate_idempotenta(app):
    from datetime import datetime, timedelta, timezone
    from portal import plan_schimbari as plan_schimbari_mod
    firm_id = _insereaza_firma_minimala(app)
    contract_id = _insereaza_contract_minimal(app, firm_id, numar=90002)
    trecut = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _insereaza_schimbare_programata(app, firm_id, trecut)

    apeluri = {"n": 0}

    def _stub(row):
        apeluri["n"] += 1
        return contract_id

    n1 = plan_schimbari_mod.aplica_schimbari_programate(app.portal_conn, _stub)
    n2 = plan_schimbari_mod.aplica_schimbari_programate(app.portal_conn, _stub)
    assert n1 == 1
    assert n2 == 0  # randul deja 'aplicata' nu mai e reprocesat
    assert apeluri["n"] == 1


def test_aplica_schimbari_programate_esec_ramane_in_asteptare(app):
    from datetime import datetime, timedelta, timezone
    from portal import plan_schimbari as plan_schimbari_mod
    firm_id = _insereaza_firma_minimala(app)
    trecut = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    schimbare_id = _insereaza_schimbare_programata(app, firm_id, trecut)

    def _stub_esec(row):
        raise RuntimeError("eSemneaza indisponibil")

    n = plan_schimbari_mod.aplica_schimbari_programate(app.portal_conn, _stub_esec)
    assert n == 0
    rand = app.portal_conn.execute(
        "SELECT * FROM plan_schimbari_programate WHERE id=?",
        (schimbare_id,)).fetchone()
    assert rand["stare"] == "in_asteptare"
    assert rand["aplicata_la"] is None


# ---------- contract de prestari servicii ----------

@pytest.fixture(scope="module")
def _semnatura_certificat():
    """Genereaza un certificat radacina + unul de test sintetice (doar
    pentru acest fisier de test - generarea cheilor RSA nu e ieftina),
    semneaza un PDF minimal cu el si intoarce (pdf_semnat_bytes, root_pem).
    Nu e un certificat calificat real - vezi etva/trust_anchors/README.md."""
    import datetime as _dt
    import os
    import tempfile
    from cryptography import x509 as cx509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from reportlab.pdfgen import canvas as _canvas
    from pyhanko.sign.signers import SimpleSigner, sign_pdf
    from pyhanko.sign.fields import SigFieldSpec, append_signature_field
    from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

    def _cert(cn, issuer_key, issuer_cert, is_ca):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = cx509.Name([cx509.NameAttribute(NameOID.COMMON_NAME, cn)])
        issuer_name = issuer_cert.subject if issuer_cert else subject
        signing_key = issuer_key or key
        builder = (cx509.CertificateBuilder().subject_name(subject)
                  .issuer_name(issuer_name).public_key(key.public_key())
                  .serial_number(cx509.random_serial_number())
                  .not_valid_before(_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1))
                  .not_valid_after(_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=365))
                  .add_extension(cx509.BasicConstraints(ca=is_ca, path_length=None),
                                critical=True))
        return key, builder.sign(signing_key, hashes.SHA256())

    root_key, root_cert = _cert("Test Root CA", None, None, True)
    leaf_key, leaf_cert = _cert("Semnatar Test SRL", root_key, root_cert, False)
    root_pem = root_cert.public_bytes(serialization.Encoding.PEM)
    leaf_pem = leaf_cert.public_bytes(serialization.Encoding.PEM)
    leaf_key_pem = leaf_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption())

    buf = io.BytesIO()
    cv = _canvas.Canvas(buf)
    cv.drawString(100, 750, "Document de test pentru semnatura.")
    cv.save()

    with tempfile.TemporaryDirectory() as tmp:
        root_path = os.path.join(tmp, "root.pem")
        leaf_path = os.path.join(tmp, "leaf.pem")
        key_path = os.path.join(tmp, "leaf.key")
        with open(root_path, "wb") as f:
            f.write(root_pem)
        with open(leaf_path, "wb") as f:
            f.write(leaf_pem)
        with open(key_path, "wb") as f:
            f.write(leaf_key_pem)
        signer = SimpleSigner.load(key_path, leaf_path, ca_chain_files=[root_path])
        w = IncrementalPdfFileWriter(io.BytesIO(buf.getvalue()))
        append_signature_field(w, SigFieldSpec(sig_field_name="Semnatura1"))
        out = sign_pdf(w, PdfSignatureMetadata(field_name="Semnatura1"), signer=signer)
        pdf_semnat = out.getvalue()

    return pdf_semnat, root_pem


def test_vezi_contract_requires_login(app):
    c = app.test_client()
    r = c.get("/panou/contract", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def _creeaza_si_trimite_contract_master(app, firm_id, suma="100.00"):
    """Creeaza si trimite spre semnare un contract prin ruta noua a
    masterului (Task 4/5) - inlocuieste vechiul flux in care firma insasi
    declansa generarea/trimiterea din propria pagina (eliminat in acest
    task, vezi vezi_contract). Rezultatul e in_asteptare, cu ambii
    semnatari inregistrati la eSemneaza (mock-uit implicit, vezi fixture-ul
    autouse _mock_esemneaza)."""
    master = _creeaza_master(app)
    master.post(f"/master/contracte/creeaza/{firm_id}", data={
        "denumire": "Firma Test SRL", "adresa": "Str. Test 1",
        "ciclu": "lunar", "suma": suma})
    return master


def test_vezi_contract_shows_in_pregatire_when_none_sent(app):
    c = app.test_client()
    inregistreaza(c, cui="RO307")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    r = c.get("/panou/contract")
    assert "pregătire".encode() in r.data
    assert app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM contracts").fetchone()["n"] == 0


def test_vezi_contract_waits_for_prestator_before_showing_email_message(app, monkeypatch):
    c = app.test_client()
    inregistreaza(c, cui="RO308")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    master = _creeaza_master(app)
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO308'").fetchone()["id"]
    monkeypatch.setattr(esemneaza, "get_sign_request", lambda *a, **kw: {
        "recipients": [
            {"order": 1, "sigStatus": esemneaza.SIGSTATUS_PENDING},
            {"order": 2, "sigStatus": esemneaza.SIGSTATUS_PENDING}]})
    master.post(f"/master/contracte/creeaza/{firm_id}", data={
        "denumire": "Firma Test SRL", "adresa": "Str. Test 1",
        "ciclu": "lunar", "suma": "100.00"})
    r = c.get("/panou/contract")
    assert "finalizarea din partea noastră".encode() in r.data


def test_vezi_contract_shows_contract_text_after_master_sends_it(app):
    c = app.test_client()
    inregistreaza(c, cui="RO302", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO302'").fetchone()["id"]
    _creeaza_si_trimite_contract_master(app, firm_id)
    r = c.get("/panou/contract")
    assert r.status_code == 200
    assert "VML EXPERT ADVISOR SRL".encode() in r.data
    assert "Firma Test SRL".encode() in r.data  # denumirea trimisa de master
    row = app.portal_conn.execute(
        "SELECT c.* FROM contracts c JOIN firms f ON f.id=c.firm_id "
        "WHERE f.cui='RO302'").fetchone()
    assert row["ciclu_facturare"] == "lunar"
    assert row["suma"] == 100.00


def test_trimite_contract_master_sends_prestator_first_sign_in_order_one_click(
        app, monkeypatch):
    """Cele doua decizii centrale de design ale intregii functionalitati,
    confirmate explicit de operatorul uman la momentul designului: PRESTATORUL
    semneaza primul, apoi BENEFICIARUL (in aceasta ordine stricta, impusa prin
    sign_in_order), iar ambii primesc campul de semnatura pre-completat prin
    one_click_sign. Fixture-ul autouse _mock_esemneaza inlocuieste in mod
    normal create_sign_request cu un lambda care ignora complet argumentele -
    niciun test din suita nu inspecta ce se trimite de fapt. O regresie care
    ar inversa ordinea recipientilor, ar scapa sign_in_order sau ar scapa
    one_click_sign ar trece toata suita neobservata fara acest test (vezi
    Task 7 review finding 2)."""
    from portal.invoicing import FURNIZOR
    apeluri = []

    def _capteaza_create_sign_request(*args, **kwargs):
        apeluri.append((args, kwargs))
        return {"id": "fake-request-id", "status": "IN_PROGRESS"}

    monkeypatch.setattr(esemneaza, "create_sign_request",
                        _capteaza_create_sign_request)

    c = app.test_client()
    inregistreaza(c, cui="RO321", tip="direct", email="admin321@exemplu.ro")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO321'").fetchone()["id"]
    _creeaza_si_trimite_contract_master(app, firm_id)

    assert len(apeluri) == 1
    _, kwargs = apeluri[0]
    recipienti = kwargs["recipients"]
    assert len(recipienti) == 2
    # Ordinea in lista determina order=1/order=2 la eSemneaza (vezi
    # etva/esemneaza.py::create_sign_request) - prestatorul (master) trebuie
    # sa fie primul, beneficiarul (firma) al doilea.
    assert recipienti[0]["email"] == FURNIZOR["email"]
    assert recipienti[1]["email"] == "admin321@exemplu.ro"
    assert kwargs["sign_in_order"] is True
    assert "one_click_sign" in recipienti[0].get("options", [])

    numar = app.portal_conn.execute(
        "SELECT numar FROM contracts WHERE firm_id=?", (firm_id,)).fetchone()["numar"]
    assert kwargs["subject"] == f"Contract nr. {numar} - Firma Test SRL"
    assert "e-TVA Reconciliere" in kwargs["message"]
    assert "VML" in kwargs["message"]
    assert "one_click_sign" in recipienti[1].get("options", [])


def test_descarca_contract_pdf(app):
    c = app.test_client()
    inregistreaza(c, cui="RO310", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO310'").fetchone()["id"]
    _creeaza_si_trimite_contract_master(app, firm_id)
    r = c.get("/panou/contract/pdf")
    assert r.status_code == 200
    assert r.data[:4] == b"%PDF"


def test_descarca_contract_pdf_renders_romanian_diacritics(app):
    """Regresie: fonturile standard Helvetica ale reportlab (WinAnsiEncoding)
    nu acopera deloc s/t cu virgula dedesubt - le inlocuiau silentios cu
    alt caracter, fara nicio eroare, pana cand cineva citea PDF-ul cu
    atentie (vezi portal/pdf_fonts.py). Verificam explicit ca litere ca
    PARTILE/PRETUL/INCETAREA (cu diacritice) apar corect in textul extras."""
    import pdfplumber
    c = app.test_client()
    inregistreaza(c, cui="RO314", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO314'").fetchone()["id"]
    _creeaza_si_trimite_contract_master(app, firm_id)
    r = c.get("/panou/contract/pdf")
    with pdfplumber.open(io.BytesIO(r.data)) as pdf:
        text = pdf.pages[0].extract_text()
    for cuvant in ("PĂRȚILE", "PREȚUL", "ÎNCETAREA", "OBLIGAȚIILE"):
        assert cuvant in text


def test_genereaza_pdf_embeds_esemneaza_signature_tag(app):
    """Confirmat empiric impotriva eSemneaza real (2026-07-27): un PDF cu
    "{{s:1}}" in text, incarcat cu extractTags=True, produce singur un camp
    de semnatura cu pozitie corecta - nu mai trebuie ghicite coordonate.
    Acum ambele parti semneaza real (vezi planning/specs/2026-07-28-
    contract-esemneaza-admin-review-design.md), deci ambele tag-uri trebuie
    prezente: {{s:1}} langa PRESTATOR, {{s:2}} langa BENEFICIAR."""
    import pdfplumber
    from portal import contract as contract_mod
    from datetime import datetime, timezone
    beneficiar = {"denumire": "Firma Test SRL", "cui": "RO999",
                 "adresa": "Adresa test"}
    continut = contract_mod.genereaza_text(
        1, beneficiar, "lunar", 59.0, datetime.now(timezone.utc))
    pdf_bytes = contract_mod.genereaza_pdf(continut, tag_semnatura_esemneaza=True)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = pdf.pages[-1].extract_text()
    assert "{{s:1}}" in text
    assert "{{s:2}}" in text
    pdf_fara_tag = contract_mod.genereaza_pdf(continut)
    with pdfplumber.open(io.BytesIO(pdf_fara_tag)) as pdf:
        text_fara_tag = pdf.pages[-1].extract_text()
    assert "{{s:1}}" not in text_fara_tag
    assert "{{s:2}}" not in text_fara_tag


def test_descarca_contract_xml(app):
    c = app.test_client()
    inregistreaza(c, cui="RO315", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO315'").fetchone()["id"]
    _creeaza_si_trimite_contract_master(app, firm_id)
    r = c.get("/panou/contract/xml")
    assert r.status_code == 200
    assert r.mimetype == "application/xml"
    # beneficiar_cui e acum stocat exact cum e in firms.cui (ruta noua a
    # masterului nu mai re-normalizeaza prin ANAF la trimitere - vezi
    # trimite_contract_master), deci pastreaza prefixul "RO" trimis la
    # inregistrare.
    assert b"<cui>RO315</cui>" in r.data
    assert b"Firma Test SRL" in r.data  # denumirea trimisa de master


def test_descarca_contract_xml_serves_frozen_snapshot_not_live_regeneration(app):
    """Odata contractul complet semnat de ambele parti, _finalizeaza_contract_
    esemneaza ingheata un instantaneu XML in contracts.contract_xml_final -
    vezi planning/specs/2026-07-28-contract-esemneaza-admin-review-design.md:
    "Butonul de download XML existent ... serveste acest instantaneu cand
    exista, nu mai regenereaza din datele curente ale randului". Ambele rute
    de descarcare (firma si master) trebuie sa serveasca acei bytes inghetati,
    nu sa regenereze din randul curent - verificat aici mutand un camp al
    randului DUPA finalizare si confirmand ca raspunsul tot arata datele VECHI
    (o simpla comparatie de continut identic n-ar prinde un bug in care
    regenerarea produce coincidental aceiasi bytes)."""
    c = app.test_client()
    inregistreaza(c, cui="RO319", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO319'").fetchone()["id"]
    master = _creeaza_si_trimite_contract_master(app, firm_id)
    # Mock-ul implicit (_mock_esemneaza) raporteaza ambii semnatari APPLIED,
    # deci un singur GET /panou/contract declanseaza finalizarea completa
    # (vezi test_actualizeaza_stare_esemneaza_marks_prestator_then_completes).
    c.get("/panou/contract")
    contract = app.portal_conn.execute(
        "SELECT * FROM contracts WHERE firm_id=?", (firm_id,)).fetchone()
    assert contract["stare"] == "semnat"
    assert contract["contract_xml_final"] is not None
    contract_id = contract["id"]

    # Muteaza datele randului DUPA inghetarea instantaneului - regenerarea
    # din datele curente ar reflecta schimbarea, instantaneul inghetat nu.
    app.portal_conn.execute(
        "UPDATE contracts SET beneficiar_denumire=? WHERE id=?",
        ("Firma MUTATA DUPA SEMNARE SRL", contract_id))
    app.portal_conn.commit()

    r_firma = c.get("/panou/contract/xml")
    assert r_firma.status_code == 200
    assert b"Firma Test SRL" in r_firma.data  # denumirea inghetata la semnare
    assert b"Firma MUTATA DUPA SEMNARE SRL" not in r_firma.data

    r_master = master.get(f"/master/contracte/{contract_id}/xml")
    assert r_master.status_code == 200
    assert b"Firma Test SRL" in r_master.data
    assert b"Firma MUTATA DUPA SEMNARE SRL" not in r_master.data


def test_descarca_contract_pdf_certificat_regenerata_fara_fisierul_original(
        app, _semnatura_certificat):
    """Fara pdf_semnat stocat, descarcarea trebuie sa regenereze PDF-ul din
    date si sa arate rezultatul verificarii facute la semnare, nu fisierul
    brut incarcat de beneficiar (care nu mai e pastrat)."""
    import pdfplumber
    pdf_semnat, _root_pem = _semnatura_certificat
    c = app.test_client()
    inregistreaza(c, cui="RO316", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO316'").fetchone()["id"]
    # Nicio interogare GET /panou/contract intre trimitere si semnare cu
    # certificat - mock-ul _mock_esemneaza raporteaza implicit ambii
    # semnatari ca APPLIED, ceea ce ar finaliza contractul automat prin
    # eSemneaza inainte sa apucam sa testam ramura certificat.
    _creeaza_si_trimite_contract_master(app, firm_id)
    c.post("/panou/contract/semneaza", data={
        "metoda": "certificat",
        "semnatura_fisier": (io.BytesIO(pdf_semnat), "contract_semnat.pdf"),
    }, content_type="multipart/form-data")

    r = c.get("/panou/contract/pdf")
    assert r.status_code == 200
    assert r.data[:4] == b"%PDF"
    with pdfplumber.open(io.BytesIO(r.data)) as pdf:
        text = " ".join((p.extract_text() or "").replace("\n", " ")
                        for p in pdf.pages)
    assert "certificat digital calificat" in text
    assert "nu este păstrat pe server" in text


def test_semneaza_contract_rejects_esemneaza_method_from_firm(app):
    c = app.test_client()
    inregistreaza(c, cui="RO309")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    master = _creeaza_master(app)
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO309'").fetchone()["id"]
    master.post(f"/master/contracte/creeaza/{firm_id}", data={
        "denumire": "Firma Test SRL", "adresa": "Str. Test 1",
        "ciclu": "lunar", "suma": "100.00"})
    r = c.post("/panou/contract/semneaza", data={"metoda": "esemneaza"},
              follow_redirects=True)
    assert "metoda de semnatura valida".encode() in r.data


def test_webhook_esemneaza_requires_secret_header(app):
    c = app.test_client()
    r = c.post("/api/esemneaza/webhook", json={"requestId": "x", "event": "REQUEST_COMPLETED"})
    assert r.status_code == 401


def test_webhook_esemneaza_finalizes_matching_contract(app, monkeypatch):
    import portal.app as app_module
    monkeypatch.setattr(app_module, "ESEMNEAZA_WEBHOOK_SECRET", "sekret")
    c = app.test_client()
    inregistreaza(c, cui="RO317", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO317'").fetchone()["id"]
    _creeaza_si_trimite_contract_master(app, firm_id)
    request_id = app.portal_conn.execute(
        "SELECT c.esemneaza_request_id FROM contracts c JOIN firms f "
        "ON f.id=c.firm_id WHERE f.cui='RO317'").fetchone()["esemneaza_request_id"]

    r = c.post("/api/esemneaza/webhook",
              headers={"X-Webhook-Secret": "sekret"},
              json={"requestId": request_id, "event": "REQUEST_COMPLETED"})
    assert r.status_code == 200
    row = app.portal_conn.execute(
        "SELECT * FROM contracts WHERE esemneaza_request_id=?", (request_id,)).fetchone()
    assert row["stare"] == "semnat"


def test_finalizare_contract_trimite_notificarea_de_finalizare(
        app, monkeypatch):
    """Cand un contract e semnat de ambele parti, se trimite o notificare
    separata catre invoicing.NOTIFICARE_CONTRACT_FINALIZAT_EMAIL - o
    constanta distincta de invoicing.FURNIZOR['email'] (folosit pentru
    cererea INITIALA de semnat), chiar daca azi au aceeasi valoare
    (office@ereconciliere.ro)."""
    import smtplib as smtplib_mod
    trimise = []

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def send_message(self, msg):
            trimise.append(msg)

    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setattr(smtplib_mod, "SMTP", _FakeSMTP)

    c = app.test_client()
    inregistreaza(c, cui="RO322", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO322'").fetchone()["id"]
    _creeaza_si_trimite_contract_master(app, firm_id)

    # _mock_esemneaza (autouse) raporteaza implicit ambii semnatari ca
    # APPLIED - o singura vizualizare a paginii de contract a firmei
    # declanseaza polling-ul care finalizeaza contractul si trimite email-ul.
    r = c.get("/panou/contract")
    assert r.status_code == 200

    from portal.invoicing import NOTIFICARE_CONTRACT_FINALIZAT_EMAIL
    assert any(msg["To"] == NOTIFICARE_CONTRACT_FINALIZAT_EMAIL for msg in trimise)


def test_semneaza_contract_certificat_valid_dar_neincrezut(app, _semnatura_certificat):
    pdf_semnat, _root_pem = _semnatura_certificat
    c = app.test_client()
    inregistreaza(c, cui="RO305", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO305'").fetchone()["id"]
    _creeaza_si_trimite_contract_master(app, firm_id)
    r = c.post("/panou/contract/semneaza", data={
        "metoda": "certificat",
        "semnatura_fisier": (io.BytesIO(pdf_semnat), "contract_semnat.pdf"),
    }, content_type="multipart/form-data")
    assert r.status_code == 302
    row = app.portal_conn.execute(
        "SELECT c.* FROM contracts c JOIN firms f ON f.id=c.firm_id "
        "WHERE f.cui='RO305'").fetchone()
    assert row["stare"] == "semnat"
    assert row["metoda_semnatura"] == "certificat"
    # Radacina sintetica de test nu se afla printre ancorele reale DigiSign
    # din etva/trust_anchors/ - deci valid dar netrusted, exact ce ar
    # trebui sa raporteze.
    assert row["semnatura_verificata"] == 0
    import json as _json
    detalii = _json.loads(row["semnatura_detalii"])
    assert detalii["valid"] is True
    assert detalii["trusted"] is False
    # Eticheta afisata trebuie sa reflecte metoda reala folosita (certificat),
    # nu hardcodata la eSemneaza.ro - vezi Task 6 finding 1.
    r2 = c.get("/panou/contract")
    assert "certificat digital".encode() in r2.data
    assert "eSemneaza.ro".encode() not in r2.data


def test_vezi_contract_shows_label_for_legacy_mouse_signed_contract(app, _semnatura_certificat):
    """metoda_semnatura='mouse' nu mai e ofertata in UI, dar contracte vechi
    semnate asa inainte de eSemneaza tot exista si tot se pot vizualiza
    (vezi _regenereaza_pdf_contract) - eticheta nu trebuie sa devina goala
    pentru ele (vezi Task 6 finding 1, regresie prinsa la re-review)."""
    pdf_semnat, _root_pem = _semnatura_certificat
    c = app.test_client()
    inregistreaza(c, cui="RO318", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO318'").fetchone()["id"]
    _creeaza_si_trimite_contract_master(app, firm_id)
    c.post("/panou/contract/semneaza", data={
        "metoda": "certificat",
        "semnatura_fisier": (io.BytesIO(pdf_semnat), "contract_semnat.pdf"),
    }, content_type="multipart/form-data")
    # Simuleaza date vechi: niciun flux curent nu mai produce metoda_semnatura
    # = "mouse", dar contracte semnate asa inainte de eSemneaza tot exista.
    app.portal_conn.execute(
        "UPDATE contracts SET metoda_semnatura='mouse' WHERE firm_id=?", (firm_id,))
    app.portal_conn.commit()

    r = c.get("/panou/contract")
    assert "semnătură desenată".encode() in r.data
    assert "certificat digital".encode() not in r.data
    assert "eSemneaza.ro".encode() not in r.data


def test_semneaza_contract_certificat_rejects_unsigned_pdf(app):
    c = app.test_client()
    inregistreaza(c, cui="RO311", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO311'").fetchone()["id"]
    _creeaza_si_trimite_contract_master(app, firm_id)
    from reportlab.pdfgen import canvas as _canvas
    buf = io.BytesIO()
    cv = _canvas.Canvas(buf)
    cv.drawString(100, 750, "fara semnatura")
    cv.save()
    # Nu urmarim redirectul in acelasi apel: pasul urmator (GET /panou/contract)
    # ar declansa _actualizeaza_stare_esemneaza, care sub mock-ul implicit
    # _mock_esemneaza ar finaliza contractul prin eSemneaza inainte sa
    # apucam sa citim starea ramasa dupa incercarea (esuata) cu certificat.
    r = c.post("/panou/contract/semneaza", data={
        "metoda": "certificat",
        "semnatura_fisier": (io.BytesIO(buf.getvalue()), "nesemnat.pdf"),
    }, content_type="multipart/form-data", follow_redirects=False)
    assert r.status_code == 302
    row = app.portal_conn.execute(
        "SELECT c.stare FROM contracts c JOIN firms f ON f.id=c.firm_id "
        "WHERE f.cui='RO311'").fetchone()
    assert row["stare"] == "in_asteptare"
    r2 = c.get(r.headers["Location"])
    assert "nu contine nicio semnatura".encode() in r2.data


def test_semneaza_contract_certificat_rejects_non_pdf_file(app):
    c = app.test_client()
    inregistreaza(c, cui="RO312", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO312'").fetchone()["id"]
    _creeaza_si_trimite_contract_master(app, firm_id)
    r = c.post("/panou/contract/semneaza", data={
        "metoda": "certificat",
        "semnatura_fisier": (io.BytesIO(b"nu e deloc pdf"), "gresit.txt"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert "nu poate fi citit ca PDF".encode() in r.data


def test_creeaza_cerere_plata_requires_signed_contract(app):
    c = app.test_client()
    inregistreaza(c, cui="RO309", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    _apropie_trial_de_final(app, "RO309")
    r = c.post("/panou/plata", data={}, follow_redirects=True)
    assert "semnezi contractul de prestari servicii".encode() in r.data


def test_creeaza_cerere_plata_works_without_contract_when_disabled(app, monkeypatch):
    """Contractele sunt puse pe pauza (CONTRACTE_ACTIVE implicit False in
    productie - vezi portal/app.py) - poarta din creeaza_cerere_plata nu se
    mai aplica, firma poate cere plata direct."""
    import portal.app as app_module
    monkeypatch.setattr(app_module, "CONTRACTE_ACTIVE", False)
    c = app.test_client()
    inregistreaza(c, cui="RO320", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    _apropie_trial_de_final(app, "RO320")
    r = c.post("/panou/plata", data={}, follow_redirects=True)
    assert "semnezi contractul de prestari servicii".encode() not in r.data
    row = app.portal_conn.execute(
        "SELECT p.* FROM payments p JOIN firms f ON f.id=p.firm_id "
        "WHERE f.cui='RO320'").fetchone()
    assert row is not None


def test_contract_routes_redirect_when_disabled(app, monkeypatch):
    """Codul rutelor /panou/contract* ramane intact, doar nu mai e accesibil
    cat timp CONTRACTE_ACTIVE e False (implicit in productie)."""
    import portal.app as app_module
    monkeypatch.setattr(app_module, "CONTRACTE_ACTIVE", False)
    c = app.test_client()
    inregistreaza(c, cui="RO321", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    for path in ("/panou/contract", "/panou/contract/pdf",
                "/panou/contract/xml", "/panou/contract/certificat"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 302 and "/panou" in r.headers["Location"]
    r = c.post("/panou/contract/semneaza", data={"metoda": "esemneaza"},
              follow_redirects=False)
    assert r.status_code == 302 and "/panou" in r.headers["Location"]
    r = c.post("/panou/contract/reziliaza", follow_redirects=False)
    assert r.status_code == 302 and "/panou" in r.headers["Location"]


def test_master_contract_routes_redirect_when_disabled(app, monkeypatch):
    import portal.app as app_module
    monkeypatch.setattr(app_module, "CONTRACTE_ACTIVE", False)
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    for path in ("/master/contracte", "/master/contracte/1/pdf",
                "/master/contracte/1/xml", "/master/contracte/1/certificat"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 302 and "/master" in r.headers["Location"]
    r = c.post("/master/contracte/1/reziliaza", data={"ramburs_procent": "10"},
              follow_redirects=False)
    assert r.status_code == 302 and "/master" in r.headers["Location"]


def test_webhook_esemneaza_noop_when_disabled(app, monkeypatch):
    import portal.app as app_module
    monkeypatch.setattr(app_module, "CONTRACTE_ACTIVE", False)
    monkeypatch.setattr(app_module, "ESEMNEAZA_WEBHOOK_SECRET", "shh")
    c = app.test_client()
    r = c.post("/api/esemneaza/webhook", json={"requestId": "x"},
              headers={"X-Webhook-Secret": "shh"})
    assert r.status_code == 200


def test_reziliaza_contract_requires_signed_state(app):
    c = app.test_client()
    inregistreaza(c, cui="RO306", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    c.get("/panou/contract")
    r = c.post("/panou/contract/reziliaza", follow_redirects=True)
    assert "Nu exista niciun contract semnat activ".encode() in r.data


def test_master_contracte_requires_master(app):
    c = app.test_client()
    r = c.get("/master/contracte", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def _creeaza_master(app):
    conn = app.portal_conn
    if conn.execute("SELECT 1 FROM users WHERE username=?", ("master-test",)).fetchone() is None:
        # master-test doesn't exist, create it
        conn.execute(
            "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,TRUE)",
            ("master-test", psec.hash_password("ParolaMaster123!")))
        conn.commit()
    master = app.test_client()
    master.post("/autentificare",
               data={"cui": "master-test", "password": "ParolaMaster123!"})
    return master


def test_creeaza_contract_master_requires_master(app):
    c = app.test_client()
    inregistreaza(c, cui="RO301")
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO301'").fetchone()["id"]
    r = c.get(f"/master/contracte/creeaza/{firm_id}", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_creeaza_contract_master_prefills_from_anaf(app):
    c = app.test_client()
    inregistreaza(c, cui="RO302")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    master = _creeaza_master(app)
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO302'").fetchone()["id"]
    r = master.get(f"/master/contracte/creeaza/{firm_id}")
    assert b"Firma Test" in r.data  # din _mock_anaf_cui (denumire="Firma Test")


def test_trimite_contract_master_creeaza_si_trimite(app):
    c = app.test_client()
    inregistreaza(c, cui="RO303")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    master = _creeaza_master(app)
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO303'").fetchone()["id"]
    r = master.post(f"/master/contracte/creeaza/{firm_id}", data={
        "denumire": "Firma Test SRL", "adresa": "Str. Test 1",
        "ciclu": "lunar", "suma": "100.00"}, follow_redirects=False)
    assert r.status_code == 302 and "/master/contracte" in r.headers["Location"]
    contract = app.portal_conn.execute(
        "SELECT * FROM contracts WHERE firm_id=?", (firm_id,)).fetchone()
    assert contract is not None
    assert contract["stare"] == "in_asteptare"
    assert contract["metoda_semnatura"] == "esemneaza"
    assert contract["esemneaza_request_id"] == "fake-request-id"
    assert contract["numar"] >= 1


def test_trimite_contract_master_blocks_second_pending_contract(app):
    c = app.test_client()
    inregistreaza(c, cui="RO304")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    master = _creeaza_master(app)
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO304'").fetchone()["id"]
    data = {"denumire": "Firma Test SRL", "adresa": "Str. Test 1",
            "ciclu": "lunar", "suma": "100.00"}
    master.post(f"/master/contracte/creeaza/{firm_id}", data=data)
    r = master.post(f"/master/contracte/creeaza/{firm_id}", data=data,
                    follow_redirects=True)
    assert "deja un contract".encode() in r.data
    contracte = app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM contracts WHERE firm_id=?",
        (firm_id,)).fetchone()["n"]
    assert contracte == 1


def test_trimite_contract_master_allows_retry_after_rejection(app):
    c = app.test_client()
    inregistreaza(c, cui="RO310")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    master = _creeaza_master(app)
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO310'").fetchone()["id"]
    data = {"denumire": "Firma Test SRL", "adresa": "Str. Test 1",
            "ciclu": "lunar", "suma": "100.00"}
    master.post(f"/master/contracte/creeaza/{firm_id}", data=data)

    # Simulate a rejection: esemneaza_request_id cleared, stare stays in_asteptare
    # (matches _actualizeaza_stare_esemneaza's real behavior on SIGSTATUS_REJECTED).
    app.portal_conn.execute(
        "UPDATE contracts SET esemneaza_request_id=NULL WHERE firm_id=?", (firm_id,))
    app.portal_conn.commit()

    r = master.post(f"/master/contracte/creeaza/{firm_id}", data=data,
                    follow_redirects=False)
    assert r.status_code == 302 and "/master/contracte" in r.headers["Location"]
    contracte = app.portal_conn.execute(
        "SELECT COUNT(*) AS n FROM contracts WHERE firm_id=?",
        (firm_id,)).fetchone()["n"]
    assert contracte == 2


def test_actualizeaza_stare_esemneaza_marks_prestator_then_completes(app, monkeypatch):
    import portal.app as app_module
    c = app.test_client()
    inregistreaza(c, cui="RO305")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    master = _creeaza_master(app)
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO305'").fetchone()["id"]

    monkeypatch.setattr(esemneaza, "get_sign_request", lambda *a, **kw: {
        "recipients": [
            {"order": 1, "sigStatus": esemneaza.SIGSTATUS_APPLIED},
            {"order": 2, "sigStatus": esemneaza.SIGSTATUS_PENDING}]})
    master.post(f"/master/contracte/creeaza/{firm_id}", data={
        "denumire": "Firma Test SRL", "adresa": "Str. Test 1",
        "ciclu": "lunar", "suma": "100.00"})

    r = c.get("/panou/contract")
    contract = app.portal_conn.execute(
        "SELECT * FROM contracts WHERE firm_id=?", (firm_id,)).fetchone()
    assert contract["prestator_semnat_la"] is not None
    assert contract["stare"] == "in_asteptare"
    assert contract["contract_xml_final"] is None
    assert contract["esemneaza_request_id"] is not None
    # Randare corecta a starii "e randul beneficiarului sa semneze" - vezi
    # Task 6 finding 2 (test lipsa dupa stergerea
    # test_semneaza_contract_esemneaza_stays_pending_until_signed).
    assert "trimis spre semnare".encode() in r.data

    monkeypatch.setattr(esemneaza, "get_sign_request", lambda *a, **kw: {
        "recipients": [
            {"order": 1, "sigStatus": esemneaza.SIGSTATUS_APPLIED},
            {"order": 2, "sigStatus": esemneaza.SIGSTATUS_APPLIED}]})
    c.get("/panou/contract")
    contract = app.portal_conn.execute(
        "SELECT * FROM contracts WHERE firm_id=?", (firm_id,)).fetchone()
    assert contract["stare"] == "semnat"
    assert contract["contract_xml_final"] is not None
    assert b"<contract " in bytes(contract["contract_xml_final"])[:200]


def test_actualizeaza_stare_esemneaza_handles_rejection(app, monkeypatch):
    c = app.test_client()
    inregistreaza(c, cui="RO306")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    master = _creeaza_master(app)
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO306'").fetchone()["id"]
    master.post(f"/master/contracte/creeaza/{firm_id}", data={
        "denumire": "Firma Test SRL", "adresa": "Str. Test 1",
        "ciclu": "lunar", "suma": "100.00"})

    monkeypatch.setattr(esemneaza, "get_sign_request", lambda *a, **kw: {
        "recipients": [
            {"order": 1, "sigStatus": esemneaza.SIGSTATUS_REJECTED},
            {"order": 2, "sigStatus": esemneaza.SIGSTATUS_PENDING}]})
    c.get("/panou/contract")
    contract = app.portal_conn.execute(
        "SELECT * FROM contracts WHERE firm_id=?", (firm_id,)).fetchone()
    assert contract["esemneaza_request_id"] is None
    assert contract["stare"] == "in_asteptare"


def test_finalizeaza_reziliere_requires_master(app):
    c = app.test_client()
    r = c.post("/master/contracte/1/reziliaza", data={"ramburs_procent": "10"},
              follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_reziliaza_contract_flow_complete(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO307", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    _semneaza_contract_esemneaza(app, c)
    r = c.post("/panou/contract/reziliaza", follow_redirects=True)
    assert "reziliere a fost inregistrata".encode() in r.data

    contract_id = app.portal_conn.execute(
        "SELECT c.id FROM contracts c JOIN firms f ON f.id=c.firm_id "
        "WHERE f.cui='RO307'").fetchone()["id"]
    row = app.portal_conn.execute(
        "SELECT * FROM contracts WHERE id=?", (contract_id,)).fetchone()
    assert row["stare"] == "reziliere_solicitata"

    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r_master = c_master.post(
        f"/master/contracte/{contract_id}/reziliaza",
        data={"ramburs_procent": "30"}, follow_redirects=True)
    assert "reziliat".encode() in r_master.data
    row = app.portal_conn.execute(
        "SELECT * FROM contracts WHERE id=?", (contract_id,)).fetchone()
    assert row["stare"] == "reziliat"
    assert row["ramburs_procent"] == 30
    assert row["reziliat_de"] == "sef"


def test_finalizeaza_reziliere_rejects_ramburs_peste_maxim(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO308", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    _semneaza_contract_esemneaza(app, c)
    contract_id = app.portal_conn.execute(
        "SELECT c.id FROM contracts c JOIN firms f ON f.id=c.firm_id "
        "WHERE f.cui='RO308'").fetchone()["id"]

    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c_master.post(
        f"/master/contracte/{contract_id}/reziliaza",
        data={"ramburs_procent": "75"}, follow_redirects=True)
    assert "trebuie sa fie intre 0 si".encode() in r.data
    row = app.portal_conn.execute(
        "SELECT stare FROM contracts WHERE id=?", (contract_id,)).fetchone()
    assert row["stare"] == "semnat"


# ---------- verificare semnatura (master, independent de CONTRACTE_ACTIVE) ----------

def test_verificare_semnatura_master_requires_master(app):
    c = app.test_client()
    r = c.get("/master/verificare-semnatura", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_verificare_semnatura_master_works_when_contracte_dezactivate(app, monkeypatch):
    """Instrumentul e independent de CONTRACTE_ACTIVE - spre deosebire de
    toate rutele /master/contracte/..., trebuie sa ramana accesibil chiar
    cand contractele sunt puse pe pauza (valoarea implicita reala)."""
    import portal.app as app_module
    monkeypatch.setattr(app_module, "CONTRACTE_ACTIVE", False)
    master = _creeaza_master(app)
    r = master.get("/master/verificare-semnatura")
    assert r.status_code == 200
    assert "Verifică semnătura".encode() in r.data


def test_verificare_semnatura_master_valid_dar_neincrezut(app, _semnatura_certificat):
    """Semnatura sintetica din fixture e valida criptografic, dar radacina
    ei de test nu se afla in etva/trust_anchors/ (unde stau acum ancorele
    reale DigiSign) - deci trusted ramane False, exact ca la ramura
    echivalenta din semneaza_contract."""
    pdf_semnat, _root_pem = _semnatura_certificat
    master = _creeaza_master(app)
    r = master.post("/master/verificare-semnatura", data={
        "fisier": (io.BytesIO(pdf_semnat), "semnat.pdf"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    assert "Semnatar Test SRL".encode() in r.data
    assert "validă criptografic".encode() in r.data
    assert "neverificată ca fiind de încredere".encode() in r.data


def test_verificare_semnatura_master_rejects_unsigned_pdf(app):
    from reportlab.pdfgen import canvas as _canvas
    buf = io.BytesIO()
    cv = _canvas.Canvas(buf)
    cv.drawString(100, 750, "PDF fara nicio semnatura.")
    cv.save()
    master = _creeaza_master(app)
    r = master.post("/master/verificare-semnatura", data={
        "fisier": (io.BytesIO(buf.getvalue()), "nesemnat.pdf"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    assert "nicio semnatura electronica incorporata".encode() in r.data


def test_verificare_semnatura_master_rejects_missing_file(app):
    master = _creeaza_master(app)
    r = master.post("/master/verificare-semnatura", data={},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    assert "Incarca un fisier PDF semnat".encode() in r.data


# ---------- CSRF ----------
# Restul suitei ruleaza cu WTF_CSRF_ENABLED=False (vezi fixture-ul app) ca
# sa nu trebuiasca rescrise sutele de teste care posteaza formulare fara sa
# obtina intai un token - practica standard flask-wtf pentru teste. Aceste
# cateva teste reactiveaza explicit protectia, ca sa confirme ca ea chiar
# functioneaza cand e activa in productie.

def test_csrf_rejects_post_without_token(app):
    c = app.test_client()
    inregistreaza(c, cui="RO900")
    c.get("/iesire")
    app.config["WTF_CSRF_ENABLED"] = True
    r = c.post("/autentificare",
              data={"cui": "RO900", "password": "ParolaLunga123!"})
    assert r.status_code == 400


def test_csrf_accepts_post_with_valid_token_from_rendered_form(app):
    c = app.test_client()
    inregistreaza(c, cui="RO901")
    c.get("/iesire")
    app.config["WTF_CSRF_ENABLED"] = True
    pagina = c.get("/autentificare")
    token = re.search(
        rb'name="csrf_token" value="([^"]+)"', pagina.data).group(1).decode()
    r = c.post("/autentificare",
              data={"cui": "RO901", "password": "ParolaLunga123!",
                    "csrf_token": token},
              follow_redirects=False)
    assert r.status_code == 302 and "/app" in r.headers["Location"]


def test_csrf_exempt_contact_endpoint_works_without_token(app):
    """/api/contact ramane accesibil fara token chiar si cu CSRF activ -
    e singurul flux public neautentificat (docs/contact.html e servit
    static, fara acces la un token randat de Jinja)."""
    app.config["WTF_CSRF_ENABLED"] = True
    c = app.test_client()
    r = c.post("/api/contact", json={
        "nume": "Test", "email": "test@exemplu.ro", "tip": "general",
        "mesaj": "Un mesaj de test."})
    assert r.status_code == 200 and r.get_json()["ok"] is True


# ---------- statistici de business (master) ----------

def test_master_statistici_requires_master(app):
    c = app.test_client()
    r = c.get("/master/statistici", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_master_statistici_counts_and_mrr(app):
    _seed_master(app)
    c1 = app.test_client()
    inregistreaza(c1, cui="RO401", tip="direct")
    c1.post("/panou/plan", data={"ciclu": "lunar"})  # 59 RON/luna

    c2 = app.test_client()
    inregistreaza(c2, cui="RO402", tip="contabilitate")
    c2.post("/panou/plan", data={"ciclu": "lunar"})  # 25 RON/luna/client
    c2.post("/api/clients", json={"cui": "RO4021", "name": "Client Unu", "gdpr_confirmat": True})
    c2.post("/api/clients", json={"cui": "RO4022", "name": "Client Doi", "gdpr_confirmat": True})

    c3 = app.test_client()
    inregistreaza(c3, cui="RO403", tip="direct")  # inca in proba, fara ciclu

    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c_master.get("/master/statistici")
    assert r.status_code == 200
    # 59 (RO401) + 25*2 (RO402, 2 clienti) = 109
    assert "109.00".encode() in r.data


def test_master_statistici_excludes_inactive_firms_from_mrr(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO404", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO404'").fetchone()["id"]

    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post(f"/master/firma/{firm_id}/comutare")  # dezactiveaza firma

    r = c_master.get("/master/statistici")
    assert "0.00".encode() in r.data
    assert "59.00".encode() not in r.data


# ---------- remindere expirare trial ----------

def _seteaza_trial_zile_ramase(app, cui, zile):
    """Muta trial_expira_la ca zile_ramase_trial() sa raporteze exact `zile`
    zile ramase - sau 0 (trial deja expirat), pentru zile<=0."""
    from datetime import datetime, timedelta, timezone
    if zile <= 0:
        expira = datetime.now(timezone.utc) - timedelta(hours=1)
    else:
        expira = datetime.now(timezone.utc) + timedelta(days=zile, hours=1)
    app.portal_conn.execute(
        "UPDATE firms SET trial_expira_la=? WHERE cui=?", (expira.isoformat(), cui))
    app.portal_conn.commit()


def test_master_remindere_trial_requires_master(app):
    c = app.test_client()
    r = c.get("/master/remindere-trial", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_zile_ramase_trial_computes_whole_days_left(app):
    from portal import trial_reminders as remind_mod
    from datetime import datetime, timedelta, timezone
    expira = (datetime.now(timezone.utc) + timedelta(days=5, hours=1)).isoformat()
    assert remind_mod.zile_ramase_trial(expira) == 5


def test_zile_ramase_trial_never_negative(app):
    from portal import trial_reminders as remind_mod
    from datetime import datetime, timedelta, timezone
    expira = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    assert remind_mod.zile_ramase_trial(expira) == 0


def test_prag_de_trimis_advances_one_threshold_at_a_time(app):
    from portal import trial_reminders as remind_mod
    assert remind_mod._prag_de_trimis(8, None) is None  # peste orice prag
    assert remind_mod._prag_de_trimis(7, None) == 7
    # 3 zile ramase dar niciun email trimis inca - prinde intai pragul de 7
    # (cel mai putin urgent netrimis), nu sare direct la cel mai urgent.
    assert remind_mod._prag_de_trimis(3, None) == 7
    assert remind_mod._prag_de_trimis(0, None) == 7
    assert remind_mod._prag_de_trimis(1, 7) == 1  # 7 deja trimis, urmeaza 1
    assert remind_mod._prag_de_trimis(3, 7) is None  # 3 zile > pragul de 1, inca nimic de trimis
    assert remind_mod._prag_de_trimis(0, 1) == 0  # 1 deja trimis, urmeaza 0
    assert remind_mod._prag_de_trimis(5, 7) is None  # 5<=7 dar 7 e deja trimis


def test_verifica_si_trimite_sends_reminder_at_threshold_and_marks_sent(app):
    from portal import trial_reminders as remind_mod
    c = app.test_client()
    inregistreaza(c, cui="RO501", email="admin501@exemplu.ro")
    _seteaza_trial_zile_ramase(app, "RO501", 7)
    trimise = []
    n = remind_mod.verifica_si_trimite(
        app.portal_conn, lambda dest, subiect, continut: trimise.append((dest, subiect)))
    assert n == 1
    assert trimise[0][0] == "admin501@exemplu.ro"
    row = app.portal_conn.execute(
        "SELECT trial_reminder_ultim_prag FROM firms WHERE cui='RO501'").fetchone()
    assert row["trial_reminder_ultim_prag"] == 7


def test_verifica_si_trimite_does_not_resend_same_threshold(app):
    from portal import trial_reminders as remind_mod
    c = app.test_client()
    inregistreaza(c, cui="RO502")
    _seteaza_trial_zile_ramase(app, "RO502", 7)
    trimise = []
    remind_mod.verifica_si_trimite(app.portal_conn, lambda *a: trimise.append(a))
    remind_mod.verifica_si_trimite(app.portal_conn, lambda *a: trimise.append(a))
    assert len(trimise) == 1


def test_verifica_si_trimite_sends_next_more_urgent_threshold(app):
    from portal import trial_reminders as remind_mod
    c = app.test_client()
    inregistreaza(c, cui="RO503")
    _seteaza_trial_zile_ramase(app, "RO503", 7)
    trimise = []
    remind_mod.verifica_si_trimite(app.portal_conn, lambda *a: trimise.append(a))
    _seteaza_trial_zile_ramase(app, "RO503", 1)
    remind_mod.verifica_si_trimite(app.portal_conn, lambda *a: trimise.append(a))
    assert len(trimise) == 2
    row = app.portal_conn.execute(
        "SELECT trial_reminder_ultim_prag FROM firms WHERE cui='RO503'").fetchone()
    assert row["trial_reminder_ultim_prag"] == 1


def test_verifica_si_trimite_excludes_firms_with_ciclu_ales(app):
    from portal import trial_reminders as remind_mod
    c = app.test_client()
    inregistreaza(c, cui="RO504")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    _seteaza_trial_zile_ramase(app, "RO504", 0)
    trimise = []
    n = remind_mod.verifica_si_trimite(app.portal_conn, lambda *a: trimise.append(a))
    assert n == 0
    assert trimise == []


def test_verifica_si_trimite_excludes_inactive_firms(app):
    from portal import trial_reminders as remind_mod
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO505")
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO505'").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post(f"/master/firma/{firm_id}/comutare")  # dezactiveaza firma
    _seteaza_trial_zile_ramase(app, "RO505", 0)
    trimise = []
    n = remind_mod.verifica_si_trimite(app.portal_conn, lambda *a: trimise.append(a))
    assert n == 0


def test_master_remindere_trial_page_lists_firms_in_trial(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO506", name="Firma Cinci Sase SRL")
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c_master.get("/master/remindere-trial")
    assert r.status_code == 200
    assert "Firma Cinci Sase SRL".encode() in r.data
    assert b"RO506" in r.data


def test_trimite_remindere_trial_route_sends_and_updates_db(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO507")
    _seteaza_trial_zile_ramase(app, "RO507", 7)
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c_master.post("/master/remindere-trial/trimite", follow_redirects=True)
    assert r.status_code == 200
    assert "reminder trimis".encode() in r.data
    row = app.portal_conn.execute(
        "SELECT trial_reminder_ultim_prag FROM firms WHERE cui='RO507'").fetchone()
    assert row["trial_reminder_ultim_prag"] == 7


# ---------- arhivare automata a firmelor neplatitoare ----------

def test_arhiveaza_firme_neplatitoare_archives_expired_trial_no_cycle(app):
    from portal import trial_reminders as remind_mod
    c = app.test_client()
    inregistreaza(c, cui="RO601")
    _seteaza_trial_zile_ramase(app, "RO601", 0)
    n = remind_mod.arhiveaza_firme_neplatitoare(app.portal_conn)
    assert n == 1
    row = app.portal_conn.execute(
        "SELECT arhivata_la FROM firms WHERE cui='RO601'").fetchone()
    assert row["arhivata_la"] is not None


def test_arhiveaza_firme_neplatitoare_leaves_active_trial_untouched(app):
    from portal import trial_reminders as remind_mod
    c = app.test_client()
    inregistreaza(c, cui="RO602")
    _seteaza_trial_zile_ramase(app, "RO602", 5)
    n = remind_mod.arhiveaza_firme_neplatitoare(app.portal_conn)
    assert n == 0
    row = app.portal_conn.execute(
        "SELECT arhivata_la FROM firms WHERE cui='RO602'").fetchone()
    assert row["arhivata_la"] is None


def test_arhiveaza_firme_neplatitoare_excludes_firms_with_ciclu_ales(app):
    from portal import trial_reminders as remind_mod
    c = app.test_client()
    inregistreaza(c, cui="RO603")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    _seteaza_trial_zile_ramase(app, "RO603", 0)
    n = remind_mod.arhiveaza_firme_neplatitoare(app.portal_conn)
    assert n == 0
    row = app.portal_conn.execute(
        "SELECT arhivata_la FROM firms WHERE cui='RO603'").fetchone()
    assert row["arhivata_la"] is None


def test_arhiveaza_firme_neplatitoare_excludes_inactive_firms(app):
    from portal import trial_reminders as remind_mod
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO604")
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO604'").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post(f"/master/firma/{firm_id}/comutare")  # dezactiveaza firma
    _seteaza_trial_zile_ramase(app, "RO604", 0)
    n = remind_mod.arhiveaza_firme_neplatitoare(app.portal_conn)
    assert n == 0


def test_arhiveaza_firme_neplatitoare_is_idempotent(app):
    from portal import trial_reminders as remind_mod
    c = app.test_client()
    inregistreaza(c, cui="RO605")
    _seteaza_trial_zile_ramase(app, "RO605", 0)
    assert remind_mod.arhiveaza_firme_neplatitoare(app.portal_conn) == 1
    assert remind_mod.arhiveaza_firme_neplatitoare(app.portal_conn) == 0


@pytest.mark.skipif(
    os.environ.get("ETVA_TEST_PG") != "1",
    reason="verifica starea reala a tranzactiei Postgres (idle in transaction)")
def test_verifica_si_trimite_and_arhiveaza_do_not_leave_transaction_open_when_empty(app):
    """Regresie: descoperit direct pe testare si productie - firul de fundal
    a lasat o tranzactie 'idle in transaction' agatata 5+ ore dupa un ciclu
    fara nicio firma de procesat (fetchall gol -> bucla nu ajunge niciodata
    la commit()), destul cat sa blocheze un CREATE INDEX CONCURRENTLY."""
    import psycopg
    from portal import trial_reminders as remind_mod
    n1 = remind_mod.verifica_si_trimite(app.portal_conn, lambda *a: None)
    n2 = remind_mod.arhiveaza_firme_neplatitoare(app.portal_conn)
    assert (n1, n2) == (0, 0)  # aplicatie noua de test, fara firme in proba
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as diag:
        idle = diag.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE state = 'idle in transaction' AND pid <> pg_backend_pid()"
        ).fetchone()[0]
    assert idle == 0


def test_scheduler_leader_lock_is_exclusive_per_data_dir(tmp_path):
    """Cu --workers > 1, fiecare proces gunicorn apeleaza create_app separat
    - fara asta, firele de fundal (backup, remindere trial) ar porni cate
    unul per proces si ar face treaba de doua ori (ex. email de reminder
    trimis de doua ori la boot). _is_scheduler_leader e mecanismul de
    excludere: primul "proces" (fd deschis) castiga, urmatorul asupra
    aceluiasi data_dir pierde, pana cand primul elibereaza (inchide fd-ul -
    echivalentul unui restart de proces real)."""
    from portal.app import _is_scheduler_leader
    fd1 = _is_scheduler_leader(str(tmp_path))
    assert fd1 is not None
    fd2 = _is_scheduler_leader(str(tmp_path))
    assert fd2 is None
    fd1.close()
    fd3 = _is_scheduler_leader(str(tmp_path))
    assert fd3 is not None
    fd3.close()


def test_create_app_second_instance_on_same_data_dir_is_not_scheduler_leader(tmp_path):
    """Nivelul de integrare al testului de mai sus: doua create_app() cu
    scheduler-ele pornite, pe acelasi data_dir (simuleaza doi workeri
    gunicorn) - doar primul primeste un _scheduler_lock_fd real."""
    from portal.app import create_app
    app1 = create_app(str(tmp_path), enable_backup_scheduler=True,
                      enable_trial_reminder_scheduler=True)
    app2 = create_app(str(tmp_path), enable_backup_scheduler=True,
                      enable_trial_reminder_scheduler=True)
    try:
        assert app1._scheduler_lock_fd is not None
        assert app2._scheduler_lock_fd is None
    finally:
        app1._scheduler_lock_fd.close()


def test_create_app_second_instance_plan_schimbari_scheduler_not_leader(tmp_path):
    """Acelasi tipar ca testul de mai sus, pentru schedulerul de schimbari
    de plan programate - flag-ul lui trebuie sa intre in acelasi calcul al
    _scheduler_lock_fd (vezi OR-ul din create_app), nu doar cele 3 anterioare."""
    from portal.app import create_app
    app1 = create_app(str(tmp_path), enable_plan_schimbari_scheduler=True)
    app2 = create_app(str(tmp_path), enable_plan_schimbari_scheduler=True)
    try:
        assert app1._scheduler_lock_fd is not None
        assert app2._scheduler_lock_fd is None
    finally:
        app1._scheduler_lock_fd.close()


def test_arhiveaza_firme_trial_route_requires_master(app):
    c = app.test_client()
    r = c.post("/master/remindere-trial/arhiveaza", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_arhiveaza_firme_trial_route_archives_and_reports_count(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO606")
    _seteaza_trial_zile_ramase(app, "RO606", 0)
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c_master.post("/master/remindere-trial/arhiveaza", follow_redirects=True)
    assert r.status_code == 200
    assert "1 firma arhivata".encode() in r.data
    row = app.portal_conn.execute(
        "SELECT arhivata_la FROM firms WHERE cui='RO606'").fetchone()
    assert row["arhivata_la"] is not None


def test_app_redirects_to_panou_when_firm_archived(app):
    from datetime import datetime, timezone
    c = app.test_client()
    inregistreaza(c, cui="RO607")
    app.portal_conn.execute(
        "UPDATE firms SET arhivata_la=? WHERE cui='RO607'",
        (datetime.now(timezone.utc).isoformat(),))
    app.portal_conn.commit()
    r = c.get("/app", follow_redirects=False)
    assert r.status_code == 302 and "/panou" in r.headers["Location"]
    r = c.get(r.headers["Location"])
    assert "Contul acestei firme e arhivat".encode() in r.data


def test_api_blocked_when_firm_archived(app):
    from datetime import datetime, timezone
    c = app.test_client()
    inregistreaza(c, cui="RO608")
    assert c.get("/api/me").status_code == 200
    app.portal_conn.execute(
        "UPDATE firms SET arhivata_la=? WHERE cui='RO608'",
        (datetime.now(timezone.utc).isoformat(),))
    app.portal_conn.commit()
    assert c.get("/api/me").status_code == 401


def test_panou_shows_archived_banner(app):
    from datetime import datetime, timezone
    c = app.test_client()
    inregistreaza(c, cui="RO609")
    app.portal_conn.execute(
        "UPDATE firms SET arhivata_la=? WHERE cui='RO609'",
        (datetime.now(timezone.utc).isoformat(),))
    app.portal_conn.commit()
    r = c.get("/panou")
    assert "Cont arhivat".encode() in r.data


def test_valideaza_plata_reactivates_archived_firm(app):
    from datetime import datetime, timezone
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO610", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    _apropie_trial_de_final(app, "RO610")
    _semneaza_contract_esemneaza(app, c)
    c.post("/panou/plata", data={})
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO610'").fetchone()["id"]
    app.portal_conn.execute(
        "UPDATE firms SET arhivata_la=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), firm_id))
    app.portal_conn.commit()

    plata_id = app.portal_conn.execute(
        "SELECT id FROM payments WHERE firm_id=?", (firm_id,)).fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post(f"/master/plati/{plata_id}/valideaza", data={"judet": "Bucuresti"})

    row = app.portal_conn.execute(
        "SELECT arhivata_la FROM firms WHERE id=?", (firm_id,)).fetchone()
    assert row["arhivata_la"] is None
    r = c.get("/app", follow_redirects=False)
    assert r.status_code != 302 or "/panou" not in r.headers.get("Location", "")


# ---------- comutare rapida intre firme ----------

def test_switch_firm_shows_confirmation_message(app):
    c = app.test_client()
    inregistreaza(c, cui="RO611")
    c.post("/panou/firme",
          data={"name": "Firma Doisprezece PFA", "cui": "RO612", "tip": "direct", "reconcilieri_estimate": "10"})
    firm1_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO611'").fetchone()["id"]
    r = c.post("/panou/comutare-firma", data={"firm_id": str(firm1_id)},
               follow_redirects=True)
    assert "Acum lucrezi cu Firma Unu SRL".encode() in r.data


# ---------- rutele /api/risc-fiscal ----------

def _client_risc_fiscal_platit(app, cui, tip="direct", risc_fiscal_nivel="simplu",
                               reconcilieri_estimate=None, seed_master=True,
                               email="test@exemplu.ro"):
    """Wrapper subtire peste _firma_cu_abonament_platit, pentru testele care
    au nevoie doar de clientul firmei - accesul la /api/risc-fiscal/* cere
    acum o plata de abonament chiar validata, nu doar risc_fiscal_nivel
    ales (vezi current_identity() din portal/app.py)."""
    c, _firm_id, _c_master = _firma_cu_abonament_platit(
        app, cui, tip=tip, risc_fiscal_nivel=risc_fiscal_nivel,
        reconcilieri_estimate=reconcilieri_estimate, seed_master=seed_master,
        email=email)
    return c


def test_salveaza_risc_fiscal_respinge_daca_modulul_nu_e_activat(app):
    c = app.test_client()
    inregistreaza(c, cui="RO901", tip="direct")
    r = c.post("/api/risc-fiscal/perioada",
              data={"perioada": "2026-T2", "capitaluri_proprii": "100"})
    assert r.status_code == 403
    assert "nu e activat" in r.get_json()["errors"][0]


def test_salveaza_risc_fiscal_respinge_daca_nivelul_e_ales_dar_neplatit(app):
    """Firma alege nivelul din /panou/plan (posibil chiar in trial), dar nu
    a platit inca niciodata - accesul la tool ramane blocat, la fel ca daca
    n-ar fi ales deloc un nivel."""
    c = app.test_client()
    inregistreaza(c, cui="RO9011", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar", "risc_fiscal_nivel": "simplu",
                                "reconcilieri_estimate": "10"})
    r = c.post("/api/risc-fiscal/perioada",
              data={"perioada": "2026-T2", "capitaluri_proprii": "100"})
    assert r.status_code == 403
    assert "nu e activat" in r.get_json()["errors"][0]


def test_salveaza_risc_fiscal_saft_necesita_fisier(app):
    c = _client_risc_fiscal_platit(app, "RO9012", tip="direct", risc_fiscal_nivel="simplu")
    r = c.post("/api/risc-fiscal/perioada",
              data={"perioada": "2026-T2", "sursa_date": "saft_d406"})
    assert r.status_code == 400
    assert "Incarca fisierul SAF-T" in r.get_json()["errors"][0]


def test_salveaza_risc_fiscal_saft_respinge_fisier_invalid(app):
    import io
    c = _client_risc_fiscal_platit(app, "RO9013", tip="direct", risc_fiscal_nivel="simplu")
    r = c.post("/api/risc-fiscal/perioada", data={
        "perioada": "2026-T2", "sursa_date": "saft_d406",
        "saft_file": (io.BytesIO(b"nu sunt deloc un fisier SAF-T"), "fals.xml")})
    assert r.status_code == 400
    assert "nu e XML valid" in r.get_json()["errors"][0]


def test_salveaza_risc_fiscal_saft_respinge_xml_fara_conturi(app):
    import io
    c = _client_risc_fiscal_platit(app, "RO901499", tip="direct", risc_fiscal_nivel="simplu")
    continut = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<nsSAFT:AuditFile xmlns:nsSAFT="mfp:anaf:dgti:d406:declaratie:v1">'
        b'<nsSAFT:Header/></nsSAFT:AuditFile>')
    r = c.post("/api/risc-fiscal/perioada", data={
        "perioada": "2026-T2", "sursa_date": "saft_d406",
        "saft_file": (io.BytesIO(continut), "gol.xml")})
    assert r.status_code == 400
    assert "GeneralLedgerAccounts" in r.get_json()["errors"][0]


def test_salveaza_risc_fiscal_saft_extrage_automat_datele_financiare(app):
    """Alegerea SAF-T extrage automat capitalurile proprii, datoriile,
    cifra de afaceri si rezultatul net direct din conturile fisierului
    (etva/importer/saft_d406.py) - nu mai raman None ca inainte de parser."""
    import io
    c = _client_risc_fiscal_platit(app, "RO9014", tip="direct", risc_fiscal_nivel="simplu")
    ns = "mfp:anaf:dgti:d406:declaratie:v1"
    continut_saft = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<nsSAFT:AuditFile xmlns:nsSAFT="{ns}">'
        f'<nsSAFT:Header/>'
        f'<nsSAFT:MasterFiles><nsSAFT:GeneralLedgerAccounts>'
        f'<nsSAFT:Account><nsSAFT:AccountID>1012</nsSAFT:AccountID>'
        f'<nsSAFT:AccountType>Pasiv</nsSAFT:AccountType>'
        f'<nsSAFT:ClosingCreditBalance>-1</nsSAFT:ClosingCreditBalance></nsSAFT:Account>'
        f'<nsSAFT:Account><nsSAFT:AccountID>401</nsSAFT:AccountID>'
        f'<nsSAFT:AccountType>Pasiv</nsSAFT:AccountType>'
        f'<nsSAFT:ClosingCreditBalance>10</nsSAFT:ClosingCreditBalance></nsSAFT:Account>'
        f'<nsSAFT:Account><nsSAFT:AccountID>704</nsSAFT:AccountID>'
        f'<nsSAFT:AccountType>Pasiv</nsSAFT:AccountType>'
        f'<nsSAFT:ClosingCreditBalance>1000</nsSAFT:ClosingCreditBalance></nsSAFT:Account>'
        f'<nsSAFT:Account><nsSAFT:AccountID>121</nsSAFT:AccountID>'
        f'<nsSAFT:AccountType>Bifunctional</nsSAFT:AccountType>'
        f'<nsSAFT:ClosingDebitBalance>1</nsSAFT:ClosingDebitBalance></nsSAFT:Account>'
        f'</nsSAFT:GeneralLedgerAccounts></nsSAFT:MasterFiles>'
        f'</nsSAFT:AuditFile>'
    ).encode("utf-8")
    r = c.post("/api/risc-fiscal/perioada", data={
        "perioada": "2026-T2", "sursa_date": "saft_d406",
        "saft_file": (io.BytesIO(continut_saft), "export-d406.xml")})
    assert r.status_code == 200
    body = r.get_json()
    # capitaluri proprii=-1<=0 -> 100p; datorii/capital nedefinit (<=0) -> 50p;
    # rezultat net=-1<=0 -> 70p => scor_total=220/220=100.
    assert body["scor_afisat"] == 100

    istoric = c.get("/api/risc-fiscal/istoric").get_json()
    assert istoric[0]["sursa_date"] == "saft_d406"
    assert istoric[0]["are_fisier_saft"] is True
    # 1012 (-1) + 121 (net_credit -1, tot clasa 1) = -2 - contul 121
    # contribuie si la capitaluri proprii (e clasa 1), nu doar la rezultat_net.
    assert istoric[0]["capitaluri_proprii"] == -2.0
    assert istoric[0]["datorii_totale"] == 10.0
    assert istoric[0]["cifra_afaceri"] == 1000.0
    assert istoric[0]["rezultat_net"] == -1.0

    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO9014'").fetchone()["id"]
    rand_db = app.firm_conn(firm_id).execute(
        "SELECT saft_xml_original FROM risc_fiscal_perioade "
        "WHERE perioada='2026-T2'").fetchone()
    assert bytes(rand_db["saft_xml_original"]) == continut_saft


def test_salveaza_risc_fiscal_nivel_simplu_calculeaza_scor(app):
    c = _client_risc_fiscal_platit(app, "RO902", tip="direct", risc_fiscal_nivel="simplu")
    r = c.post("/api/risc-fiscal/perioada", data={
        "perioada": "2026-T2", "capitaluri_proprii": "-1",
        "datorii_totale": "10", "cifra_afaceri": "1000", "rezultat_net": "-1"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["scor_afisat"] == 100  # toti cei 3 indicatori la maxim
    assert body["scor_max_posibil"] == 220  # doar 1-3, nu si 4-5


def test_salveaza_risc_fiscal_ignora_campuri_complet_la_nivel_simplu(app):
    """Chiar daca formularul trimite declaratii_nedepuse/flag-uri (UI-ul nu
    ar trebui sa le arate la nivel 'simplu'), serverul le ignora - motorul
    de scoring (etva/risc_fiscal.py) le ignora deja la acest nivel."""
    c = _client_risc_fiscal_platit(app, "RO903", tip="direct", risc_fiscal_nivel="simplu")
    r = c.post("/api/risc-fiscal/perioada", data={
        "perioada": "2026-T2", "capitaluri_proprii": "100",
        "datorii_totale": "10", "cifra_afaceri": "1000", "rezultat_net": "10",
        "declaratii_nedepuse": "5", "flag_declarat_inactiv": "on"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["override_sectiune_b"] is False
    assert body["scor_max_posibil"] == 220


def test_salveaza_risc_fiscal_nivel_complet_cu_flag_sectiune_b(app):
    c = _client_risc_fiscal_platit(app, "RO904", tip="direct", risc_fiscal_nivel="complet")
    r = c.post("/api/risc-fiscal/perioada", data={
        "perioada": "2026-T2", "capitaluri_proprii": "100",
        "datorii_totale": "10", "cifra_afaceri": "1000", "rezultat_net": "10",
        "declaratii_nedepuse": "0", "flag_declarat_inactiv": "on"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["clasificare"] == "ridicat"
    assert body["override_sectiune_b"] is True
    assert "Declarat inactiv fiscal" in body["flaguri_risc_mare_active"]
    # scor_afisat trebuie sa reflecte riscul maxim cand clasificarea e
    # fortata de Sectiunea B, altfel raportul arata un scor mic langa o
    # eticheta rosie "ridicat" - contradictoriu (gasit intr-un raport real).
    assert body["scor_afisat"] == 100


def test_salveaza_risc_fiscal_declarat_inactiv_verificat_live_indiferent_de_bifa(app, monkeypatch):
    """"Declarat inactiv fiscal" nu se bazeaza doar pe bifa contabilului -
    se verifica live la ANAF la fiecare evaluare. Aici bifa e lasata
    NEBIFATA, dar ANAF (mockuit) spune ca firma E inactiva - rezultatul
    trebuie sa reflecte adevarul de la ANAF, nu bifa uitata."""
    from etva import anaf_cui
    monkeypatch.setattr(anaf_cui, "verify_cui", lambda cui, **kw: {
        "cui": anaf_cui.normalize_cui(cui), "denumire": "Firma Test",
        "adresa": "", "stare_inregistrare": "INREGISTRAT", "scpTVA": True,
        "inactiv_fiscal": True, "data_inactivare": "2026-01-10",
        "data_reactivare": None, "data_inregistrare": "2020-03-01"})
    c = _client_risc_fiscal_platit(app, "RO9041", tip="direct", risc_fiscal_nivel="complet")
    r = c.post("/api/risc-fiscal/perioada", data={
        "perioada": "2026-T2", "capitaluri_proprii": "100",
        "datorii_totale": "10", "cifra_afaceri": "1000", "rezultat_net": "10",
        "declaratii_nedepuse": "0"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["override_sectiune_b"] is True
    assert "Declarat inactiv fiscal" in body["flaguri_risc_mare_active"]
    assert body["verificari_automate"]["declarat_inactiv_verificat_live"] is True
    assert body["verificari_automate"]["data_inregistrare_anaf"] == "2020-03-01"


def test_salveaza_risc_fiscal_declarat_inactiv_corecteaza_bifa_gresita(app, monkeypatch):
    """Invers: contabilul bifeaza gresit "declarat inactiv", dar ANAF
    (mockuit) spune ca firma NU e inactiva - verificarea live trebuie sa
    corecteze bifa, nu s-o creada orbeste."""
    from etva import anaf_cui
    monkeypatch.setattr(anaf_cui, "verify_cui", lambda cui, **kw: {
        "cui": anaf_cui.normalize_cui(cui), "denumire": "Firma Test",
        "adresa": "", "stare_inregistrare": "INREGISTRAT", "scpTVA": True,
        "inactiv_fiscal": False})
    c = _client_risc_fiscal_platit(app, "RO9042", tip="direct", risc_fiscal_nivel="complet")
    r = c.post("/api/risc-fiscal/perioada", data={
        "perioada": "2026-T2", "capitaluri_proprii": "100",
        "datorii_totale": "10", "cifra_afaceri": "1000", "rezultat_net": "10",
        "declaratii_nedepuse": "0", "flag_declarat_inactiv": "on"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["override_sectiune_b"] is False
    assert body["flaguri_risc_mare_active"] == []
    assert body["verificari_automate"]["declarat_inactiv_verificat_live"] is True


def test_salveaza_risc_fiscal_pastreaza_bifa_daca_anaf_nu_raspunde(app, monkeypatch):
    """Daca serviciul ANAF nu poate fi contactat chiar acum, evaluarea nu se
    blocheaza - ramane pe bifa manuala a contabilului (fallback), nu
    presupune nimic despre starea reala a firmei."""
    from etva import anaf_cui

    # Inregistrarea/contractul folosesc si ele anaf_cui.verify_cui (mockuit
    # implicit sa reuseasca, vezi _mock_anaf_cui) - mock-ul care esueaza se
    # aplica DOAR dupa ce firma exista deja, ca sa nu strice inregistrarea
    # insasi, ci doar apelul live facut de evaluarea de risc fiscal.
    c = _client_risc_fiscal_platit(app, "RO9043", tip="direct", risc_fiscal_nivel="complet")

    def _boom(cui, **kw):
        raise anaf_cui.AnafCuiError("boom")
    monkeypatch.setattr(anaf_cui, "verify_cui", _boom)
    r = c.post("/api/risc-fiscal/perioada", data={
        "perioada": "2026-T2", "capitaluri_proprii": "100",
        "datorii_totale": "10", "cifra_afaceri": "1000", "rezultat_net": "10",
        "declaratii_nedepuse": "0", "flag_declarat_inactiv": "on"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["override_sectiune_b"] is True
    assert "Declarat inactiv fiscal" in body["flaguri_risc_mare_active"]
    assert body["verificari_automate"]["declarat_inactiv_verificat_live"] is False


def test_salveaza_risc_fiscal_entitate_noua_verificata_live_sub_prag(app, monkeypatch):
    """"Entitate nou infiintata" nu mai e o bifa - se stabileste din data
    reala de inregistrare la ANAF (sub 12 luni = nou infiintata), chiar
    daca bifa e lasata NEBIFATA."""
    from datetime import datetime, timedelta
    from etva import anaf_cui
    data_recenta = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    monkeypatch.setattr(anaf_cui, "verify_cui", lambda cui, **kw: {
        "cui": anaf_cui.normalize_cui(cui), "denumire": "Firma Test",
        "adresa": "", "stare_inregistrare": "INREGISTRAT", "scpTVA": True,
        "data_inregistrare": data_recenta})
    c = _client_risc_fiscal_platit(app, "RO9045", tip="direct", risc_fiscal_nivel="complet")
    r = c.post("/api/risc-fiscal/perioada", data={
        "perioada": "2026-T2", "capitaluri_proprii": "100",
        "datorii_totale": "10", "cifra_afaceri": "1000", "rezultat_net": "10",
        "declaratii_nedepuse": "0"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["override_sectiune_b"] is True
    assert "Entitate nou infiintata" in body["flaguri_risc_mare_active"]
    assert body["verificari_automate"]["entitate_noua_verificat_live"] is True
    assert body["verificari_automate"]["data_inregistrare_anaf"] == data_recenta


def test_salveaza_risc_fiscal_entitate_noua_corecteaza_bifa_peste_prag(app, monkeypatch):
    """Invers: contabilul bifeaza gresit "entitate noua" pentru o firma cu
    multi ani vechime - verificarea live trebuie sa corecteze bifa."""
    from etva import anaf_cui
    monkeypatch.setattr(anaf_cui, "verify_cui", lambda cui, **kw: {
        "cui": anaf_cui.normalize_cui(cui), "denumire": "Firma Test",
        "adresa": "", "stare_inregistrare": "INREGISTRAT", "scpTVA": True,
        "data_inregistrare": "2015-01-01"})
    c = _client_risc_fiscal_platit(app, "RO9046", tip="direct", risc_fiscal_nivel="complet")
    r = c.post("/api/risc-fiscal/perioada", data={
        "perioada": "2026-T2", "capitaluri_proprii": "100",
        "datorii_totale": "10", "cifra_afaceri": "1000", "rezultat_net": "10",
        "declaratii_nedepuse": "0", "flag_entitate_noua": "on"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["override_sectiune_b"] is False
    assert body["flaguri_risc_mare_active"] == []
    assert body["verificari_automate"]["entitate_noua_verificat_live"] is True


def test_salveaza_risc_fiscal_entitate_noua_pastreaza_bifa_daca_data_lipseste(app):
    """Mock-ul implicit (_mock_anaf_cui) nu include data_inregistrare - ca
    un CUI real fara acel camp in raspuns - trebuie sa ramana pe bifa
    manuala, fara sa presupuna nimic."""
    c = _client_risc_fiscal_platit(app, "RO9047", tip="direct", risc_fiscal_nivel="complet")
    r = c.post("/api/risc-fiscal/perioada", data={
        "perioada": "2026-T2", "capitaluri_proprii": "100",
        "datorii_totale": "10", "cifra_afaceri": "1000", "rezultat_net": "10",
        "declaratii_nedepuse": "0", "flag_entitate_noua": "on"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["override_sectiune_b"] is True
    assert "Entitate nou infiintata" in body["flaguri_risc_mare_active"]
    assert body["verificari_automate"]["entitate_noua_verificat_live"] is False


_BILANT_EXEMPLU = {
    "an": 2025, "denumire": "FIRMA TEST SRL", "caen": 6920,
    "capitaluri_proprii": 531748.0, "datorii_totale": 32749.0,
    "cifra_afaceri": 461057.0, "rezultat_net": 293631.0,
    "active_imobilizate": 126754.0, "numar_salariati": 1,
}


def _mock_bilant(monkeypatch, bilant=None, istoric=None):
    from etva import anaf_bilant
    ultim = bilant or _BILANT_EXEMPLU
    monkeypatch.setattr(anaf_bilant, "extrage_bilant", lambda cui, **kw: ultim)
    monkeypatch.setattr(anaf_bilant, "extrage_istoric",
                        lambda cui, **kw: istoric if istoric is not None else [ultim])


def test_salveaza_risc_fiscal_avertizeaza_la_discrepanta_fata_de_bilant(app, monkeypatch):
    """Cifrele tastate manual se confrunta cu ultimul bilant depus, ca sa
    prindem fisierul gresit sau virgula pusa aiurea."""
    c = _client_risc_fiscal_platit(app, "RO9060", tip="direct", risc_fiscal_nivel="simplu")
    _mock_bilant(monkeypatch)
    r = c.post("/api/risc-fiscal/perioada", data={
        "perioada": "2026-T2", "capitaluri_proprii": "-5000",
        "datorii_totale": "32749", "cifra_afaceri": "461057",
        "rezultat_net": "293631"})
    assert r.status_code == 200
    avertismente = r.get_json()["avertismente_bilant"]
    assert len(avertismente) == 1
    assert "semn opus" in avertismente[0]


def test_salveaza_risc_fiscal_fara_avertismente_cand_cifrele_se_potrivesc(app, monkeypatch):
    c = _client_risc_fiscal_platit(app, "RO9061", tip="direct", risc_fiscal_nivel="simplu")
    _mock_bilant(monkeypatch)
    r = c.post("/api/risc-fiscal/perioada", data={
        "perioada": "2026-T2", "capitaluri_proprii": "531748",
        "datorii_totale": "32749", "cifra_afaceri": "461057",
        "rezultat_net": "293631"})
    assert r.status_code == 200
    assert r.get_json()["avertismente_bilant"] == []


def test_salveaza_risc_fiscal_nu_se_compara_cu_sine_la_sursa_bilant(app, monkeypatch):
    """Cand sursa E bilantul, verificarea incrucisata n-are sens - cifrele
    sunt chiar cele de la ANAF."""
    c = _client_risc_fiscal_platit(app, "RO9062", tip="direct", risc_fiscal_nivel="simplu")
    _mock_bilant(monkeypatch)
    r = c.post("/api/risc-fiscal/perioada",
              data={"perioada": "2026-T2", "sursa_date": "bilant_anaf"})
    assert r.status_code == 200
    assert r.get_json()["avertismente_bilant"] == []


def test_salveaza_risc_fiscal_stocheaza_istoricul_bilanturilor(app, monkeypatch):
    """Istoricul se salveaza ODATA CU evaluarea, nu se ia live la generarea
    PDF-ului - un raport redescarcat peste un an trebuie sa arate ce se
    stia atunci."""
    c = _client_risc_fiscal_platit(app, "RO9063", tip="direct", risc_fiscal_nivel="simplu")
    istoric = [
        {**_BILANT_EXEMPLU, "an": 2025},
        {**_BILANT_EXEMPLU, "an": 2024, "cifra_afaceri": 377396.0},
        {**_BILANT_EXEMPLU, "an": 2023, "cifra_afaceri": 211534.0},
    ]
    _mock_bilant(monkeypatch, istoric=istoric)
    c.post("/api/risc-fiscal/perioada",
          data={"perioada": "2026-T2", "sursa_date": "bilant_anaf"})
    salvat = c.get("/api/risc-fiscal/istoric").get_json()[0]["bilant_istoric"]
    assert [x["an"] for x in salvat] == [2025, 2024, 2023]
    assert salvat[1]["cifra_afaceri"] == 377396.0


def test_risc_fiscal_pdf_cu_istoric_de_bilanturi(app, monkeypatch):
    c = _client_risc_fiscal_platit(app, "RO9064", tip="direct", risc_fiscal_nivel="simplu")
    _mock_bilant(monkeypatch, istoric=[
        {**_BILANT_EXEMPLU, "an": 2025}, {**_BILANT_EXEMPLU, "an": 2024}])
    c.post("/api/risc-fiscal/perioada",
          data={"perioada": "2026-T2", "sursa_date": "bilant_anaf"})
    r = c.get("/api/risc-fiscal/perioada/2026-T2/pdf")
    assert r.status_code == 200
    assert r.data[:4] == b"%PDF"


def test_salveaza_risc_fiscal_merge_si_cand_bilantul_nu_e_disponibil(app):
    """Mock-ul implicit intoarce None/[] - o evaluare manuala trebuie sa
    functioneze normal, fara avertismente si fara istoric."""
    c = _client_risc_fiscal_platit(app, "RO9065", tip="direct", risc_fiscal_nivel="simplu")
    r = c.post("/api/risc-fiscal/perioada", data={
        "perioada": "2026-T2", "capitaluri_proprii": "100",
        "datorii_totale": "10", "cifra_afaceri": "1000", "rezultat_net": "10"})
    assert r.status_code == 200
    assert r.get_json()["avertismente_bilant"] == []
    assert c.get("/api/risc-fiscal/istoric").get_json()[0]["bilant_istoric"] == []


def test_salveaza_risc_fiscal_sursa_bilant_anaf_preia_datele_de_la_anaf(app, monkeypatch):
    """A treia sursa de date financiare: nici fisier, nici tastare - doar
    CUI-ul. Indicatorii 1-3 se calculeaza pe cifrele din ultimul bilant
    depus la ANAF."""
    c = _client_risc_fiscal_platit(app, "RO9050", tip="direct", risc_fiscal_nivel="simplu")
    _mock_bilant(monkeypatch)
    r = c.post("/api/risc-fiscal/perioada",
              data={"perioada": "2026-T2", "sursa_date": "bilant_anaf"})
    assert r.status_code == 200
    body = r.get_json()
    # Capitaluri 531748>0 -> 0p; datorii/capitaluri 0.06<=1 -> 0p;
    # rezultat net 293631>0 -> 0p. Firma sanatoasa => scor 0.
    assert body["scor_afisat"] == 0
    assert body["clasificare"] == "scazut"

    istoric = c.get("/api/risc-fiscal/istoric").get_json()
    assert istoric[0]["sursa_date"] == "bilant_anaf"
    assert istoric[0]["capitaluri_proprii"] == 531748.0
    assert istoric[0]["datorii_totale"] == 32749.0
    assert istoric[0]["cifra_afaceri"] == 461057.0
    assert istoric[0]["rezultat_net"] == 293631.0


def test_salveaza_risc_fiscal_sursa_bilant_ignora_cifrele_din_formular(app, monkeypatch):
    """Cifrele "oficiale" nu se preiau nici partial din formular - altfel
    un client ar putea trimite valori inventate si le-ar vedea apoi in
    raport prezentate drept date de la ANAF."""
    c = _client_risc_fiscal_platit(app, "RO9051", tip="direct", risc_fiscal_nivel="simplu")
    _mock_bilant(monkeypatch)
    r = c.post("/api/risc-fiscal/perioada", data={
        "perioada": "2026-T2", "sursa_date": "bilant_anaf",
        "capitaluri_proprii": "-999", "datorii_totale": "999999",
        "cifra_afaceri": "1", "rezultat_net": "-999"})
    assert r.status_code == 200
    istoric = c.get("/api/risc-fiscal/istoric").get_json()
    assert istoric[0]["capitaluri_proprii"] == 531748.0
    assert istoric[0]["rezultat_net"] == 293631.0


def test_salveaza_risc_fiscal_sursa_bilant_fara_date_da_eroare_utila(app):
    """Mock-ul implicit intoarce None (fara bilant depus) - utilizatorul
    trebuie indrumat catre celelalte doua surse, nu lasat cu o eroare seaca."""
    c = _client_risc_fiscal_platit(app, "RO9052", tip="direct", risc_fiscal_nivel="simplu")
    r = c.post("/api/risc-fiscal/perioada",
              data={"perioada": "2026-T2", "sursa_date": "bilant_anaf"})
    assert r.status_code == 400
    mesaj = r.get_json()["errors"][0]
    assert "bilant" in mesaj.lower()
    assert "SAF-T" in mesaj


def test_salveaza_risc_fiscal_respinge_sursa_necunoscuta(app):
    c = _client_risc_fiscal_platit(app, "RO9053", tip="direct", risc_fiscal_nivel="simplu")
    r = c.post("/api/risc-fiscal/perioada",
              data={"perioada": "2026-T2", "sursa_date": "inventata"})
    assert r.status_code == 400


def test_api_bilant_risc_fiscal_intoarce_datele_pentru_precompletare(app, monkeypatch):
    c = _client_risc_fiscal_platit(app, "RO9054", tip="direct", risc_fiscal_nivel="complet")
    _mock_bilant(monkeypatch)
    r = c.get("/api/risc-fiscal/bilant")
    assert r.status_code == 200
    body = r.get_json()
    assert body["disponibil"] is True
    assert body["an"] == 2025
    assert body["numar_salariati"] == 1
    assert body["active_imobilizate"] == 126754.0


def test_api_bilant_risc_fiscal_semnaleaza_lipsa_datelor(app):
    c = _client_risc_fiscal_platit(app, "RO9055", tip="direct", risc_fiscal_nivel="complet")
    r = c.get("/api/risc-fiscal/bilant")
    assert r.status_code == 200
    assert r.get_json() == {"disponibil": False}


def test_api_bilant_risc_fiscal_refuza_fara_modul_activat(app):
    c = app.test_client()
    inregistreaza(c, cui="RO9056", tip="direct")
    r = c.get("/api/risc-fiscal/bilant")
    assert r.status_code == 403


def test_api_bilant_nu_crapa_daca_serviciul_anaf_pica(app, monkeypatch):
    """O defectiune la ANAF nu trebuie sa dea 500 - bilantul e o
    comoditate, nu o dependenta."""
    from etva import anaf_bilant

    def _boom(cui, **kw):
        raise anaf_bilant.AnafBilantError("boom")
    monkeypatch.setattr(anaf_bilant, "extrage_bilant", _boom)
    c = _client_risc_fiscal_platit(app, "RO9057", tip="direct", risc_fiscal_nivel="complet")
    r = c.get("/api/risc-fiscal/bilant")
    assert r.status_code == 200
    assert r.get_json() == {"disponibil": False}


def test_salveaza_risc_fiscal_verificari_automate_include_sold_imobilizari_saft(app):
    """sold_imobilizari_saft (clasa 2 din balanta SAF-T) e trimis in
    raspuns ca reper - portal-ul nu forteaza bifa "Lipsa bunurilor", doar
    ii da contabilului o cifra reala cu care sa se confrunte."""
    import io
    ns = "mfp:anaf:dgti:d406:declaratie:v1"
    continut_saft = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<nsSAFT:AuditFile xmlns:nsSAFT="{ns}">'
        f'<nsSAFT:Header/>'
        f'<nsSAFT:MasterFiles><nsSAFT:GeneralLedgerAccounts>'
        f'<nsSAFT:Account><nsSAFT:AccountID>2131</nsSAFT:AccountID>'
        f'<nsSAFT:AccountType>Activ</nsSAFT:AccountType>'
        f'<nsSAFT:ClosingDebitBalance>15000</nsSAFT:ClosingDebitBalance></nsSAFT:Account>'
        f'</nsSAFT:GeneralLedgerAccounts></nsSAFT:MasterFiles>'
        f'</nsSAFT:AuditFile>'
    ).encode("utf-8")
    c = _client_risc_fiscal_platit(app, "RO9044", tip="direct", risc_fiscal_nivel="complet")
    r = c.post("/api/risc-fiscal/perioada", data={
        "perioada": "2026-T2", "sursa_date": "saft_d406",
        "declaratii_nedepuse": "0",
        "saft_file": (io.BytesIO(continut_saft), "export-d406.xml")})
    assert r.status_code == 200
    body = r.get_json()
    assert body["verificari_automate"]["sold_imobilizari_saft"] == 15000.0


def test_salveaza_risc_fiscal_firma_contabilitate_cere_client_id(app):
    c = _client_risc_fiscal_platit(app, "RO905", tip="contabilitate", risc_fiscal_nivel="simplu")
    r = c.post("/api/risc-fiscal/perioada",
              data={"perioada": "2026-T2", "capitaluri_proprii": "100"})
    assert r.status_code == 400
    assert "Alege clientul" in r.get_json()["errors"][0]


def test_salveaza_risc_fiscal_firma_contabilitate_cu_client(app):
    c = _client_risc_fiscal_platit(app, "RO906", tip="contabilitate", risc_fiscal_nivel="simplu")
    client_id = c.post("/api/clients", json={
        "cui": "RO9999", "name": "Client Risc SRL", "gdpr_confirmat": True}).get_json()["id"]
    r = c.post("/api/risc-fiscal/perioada", data={
        "client_id": str(client_id), "perioada": "2026-T2",
        "capitaluri_proprii": "100", "datorii_totale": "10",
        "cifra_afaceri": "1000", "rezultat_net": "10"})
    assert r.status_code == 200
    istoric = c.get(
        f"/api/risc-fiscal/istoric?client_id={client_id}").get_json()
    assert len(istoric) == 1
    assert istoric[0]["perioada"] == "2026-T2"


def test_istoric_risc_fiscal_multiple_perioade_ordonate_descrescator(app):
    c = _client_risc_fiscal_platit(app, "RO907", tip="direct", risc_fiscal_nivel="simplu")
    for perioada in ("2026-T1", "2026-T2", "2026-T3"):
        c.post("/api/risc-fiscal/perioada",
              data={"perioada": perioada, "capitaluri_proprii": "100",
                    "datorii_totale": "10", "cifra_afaceri": "1000",
                    "rezultat_net": "10"})
    istoric = c.get("/api/risc-fiscal/istoric").get_json()
    assert [p["perioada"] for p in istoric] == ["2026-T3", "2026-T2", "2026-T1"]


def test_istoric_risc_fiscal_ordonat_dupa_momentul_rularii_nu_dupa_eticheta(app):
    """O resubmisie recenta a unei perioade cu eticheta "mai veche" trebuie
    sa apara sus in istoric - ordinea e dupa cand s-a rulat efectiv
    evaluarea (creat_la), nu dupa eticheta perioadei declarate."""
    c = _client_risc_fiscal_platit(app, "RO9071", tip="direct", risc_fiscal_nivel="simplu")
    for perioada in ("2026-T3", "2026-T1"):
        c.post("/api/risc-fiscal/perioada",
              data={"perioada": perioada, "capitaluri_proprii": "100",
                    "datorii_totale": "10", "cifra_afaceri": "1000",
                    "rezultat_net": "10"})
    istoric = c.get("/api/risc-fiscal/istoric").get_json()
    # "2026-T1" a fost rulat AL DOILEA (mai recent) - trebuie sa apara
    # primul, desi eticheta lui e "mai veche" decat "2026-T3".
    assert [p["perioada"] for p in istoric] == ["2026-T1", "2026-T3"]


def test_istoric_risc_fiscal_are_timestamp_iso_indiferent_de_backend(app):
    """creat_la trebuie sa fie mereu text ISO 8601 in raspunsul JSON, chiar
    daca backend-ul de baza de date intoarce un obiect datetime (Postgres,
    coloana timestamptz) - altfel Flask.jsonify il serializeaza diferit fata
    de SQLite (text simplu), rupand formatarea din UI in functie de backend."""
    from datetime import datetime, timezone
    c = _client_risc_fiscal_platit(app, "RO9072", tip="direct", risc_fiscal_nivel="simplu")
    inainte = datetime.now(timezone.utc)
    c.post("/api/risc-fiscal/perioada",
          data={"perioada": "2026-T2", "capitaluri_proprii": "100",
                "datorii_totale": "10", "cifra_afaceri": "1000",
                "rezultat_net": "10"})
    istoric = c.get("/api/risc-fiscal/istoric").get_json()
    creat = datetime.fromisoformat(istoric[0]["creat_la"])
    if creat.tzinfo is None:
        creat = creat.replace(tzinfo=timezone.utc)
    assert abs((creat - inainte).total_seconds()) < 30


def test_upsert_aceeasi_perioada_nu_creeaza_duplicat(app):
    c = _client_risc_fiscal_platit(app, "RO908", tip="direct", risc_fiscal_nivel="simplu")
    c.post("/api/risc-fiscal/perioada",
          data={"perioada": "2026-T2", "capitaluri_proprii": "100",
                "datorii_totale": "10", "cifra_afaceri": "1000",
                "rezultat_net": "10"})
    c.post("/api/risc-fiscal/perioada",
          data={"perioada": "2026-T2", "capitaluri_proprii": "-1",
                "datorii_totale": "10", "cifra_afaceri": "1000",
                "rezultat_net": "-1"})
    istoric = c.get("/api/risc-fiscal/istoric").get_json()
    assert len(istoric) == 1
    assert istoric[0]["scor_afisat"] == 100  # reflecta ultima salvare


def test_risc_fiscal_pdf_returneaza_pdf_valid(app):
    c = _client_risc_fiscal_platit(app, "RO909", tip="direct", risc_fiscal_nivel="simplu")
    c.post("/api/risc-fiscal/perioada",
          data={"perioada": "2026-T2", "capitaluri_proprii": "100",
                "datorii_totale": "10", "cifra_afaceri": "1000",
                "rezultat_net": "10"})
    r = c.get("/api/risc-fiscal/perioada/2026-T2/pdf")
    assert r.status_code == 200
    assert r.mimetype == "application/pdf"
    assert r.data[:4] == b"%PDF"


def test_risc_fiscal_pdf_404_pentru_perioada_inexistenta(app):
    c = _client_risc_fiscal_platit(app, "RO910", tip="direct", risc_fiscal_nivel="simplu")
    r = c.get("/api/risc-fiscal/perioada/2026-T9/pdf")
    assert r.status_code == 404


def test_app_page_contine_tab_ul_risc_fiscal(app):
    """Verificare de fum ca fisierul static web/index.html nu s-a corupt la
    adaugarea tab-ului nou - nu inlocuieste o verificare vizuala in browser."""
    c = app.test_client()
    inregistreaza(c, cui="RO913")
    r = c.get("/app")
    assert r.status_code == 200
    assert b'id="navRiscFiscal"' in r.data
    assert b'id="riscFiscal" class="view"' in r.data
    assert b"incarcaIstoricRiscFiscal" in r.data
    assert b"salveazaRiscFiscal" in r.data


def _seteaza_risc_ridicat(c, perioada="2026-T2"):
    """Salveaza o evaluare garantat clasificata 'ridicat' (capitaluri
    negative + indatorare mare + pierdere = punctaj maxim pe 1-3)."""
    return c.post("/api/risc-fiscal/perioada", data={
        "perioada": perioada, "capitaluri_proprii": "-1",
        "datorii_totale": "10", "cifra_afaceri": "1000", "rezultat_net": "-1"})


def test_verifica_si_alerteaza_trimite_pentru_scor_ridicat(app):
    from portal import risk_alerts as risk_alerts_mod
    c = _client_risc_fiscal_platit(app, "RO920", tip="direct",
                                   risc_fiscal_nivel="simplu",
                                   email="admin920@exemplu.ro")
    _seteaza_risc_ridicat(c)
    trimise = []
    n = risk_alerts_mod.verifica_si_alerteaza(
        app.portal_conn, app.firm_conn,
        lambda dest, subiect, continut: trimise.append((dest, subiect)))
    assert n == 1
    assert trimise[0][0] == "admin920@exemplu.ro"
    assert "RO920" not in trimise[0][1]  # subiectul foloseste numele firmei, nu CUI-ul
    assert "2026-T2" in trimise[0][1]


def test_verifica_si_alerteaza_nu_retrimite_aceeasi_semnatura(app):
    from portal import risk_alerts as risk_alerts_mod
    c = _client_risc_fiscal_platit(app, "RO921", tip="direct",
                                   risc_fiscal_nivel="simplu",
                                   email="admin921@exemplu.ro")
    _seteaza_risc_ridicat(c)
    trimise = []
    risk_alerts_mod.verifica_si_alerteaza(
        app.portal_conn, app.firm_conn, lambda *a: trimise.append(a))
    risk_alerts_mod.verifica_si_alerteaza(
        app.portal_conn, app.firm_conn, lambda *a: trimise.append(a))
    assert len(trimise) == 1


def test_verifica_si_alerteaza_retrimite_daca_semnatura_se_schimba(app):
    """Resubmisia aceleiasi perioade care adauga un flag nou de Sectiunea B
    trebuie sa alerteze din nou, chiar daca clasificarea/scorul raman
    identice (erau deja 'ridicat' prin scor) - semnatura include flagurile
    active, nu doar clasificarea si scorul."""
    from portal import risk_alerts as risk_alerts_mod
    c = _client_risc_fiscal_platit(app, "RO922", tip="direct",
                                   risc_fiscal_nivel="complet",
                                   email="admin922@exemplu.ro")
    # 100 (cap. proprii) + 50 (indatorare) + 70 (pierdere) + 100 (declaratii) +
    # 50 (obligatii restante crescute) = 370/370 = scor 100, 'ridicat'.
    date_complet = {
        "perioada": "2026-T2", "capitaluri_proprii": "-1", "datorii_totale": "10",
        "cifra_afaceri": "1000", "rezultat_net": "-1", "declaratii_nedepuse": "3",
        "obligatii_restante": "on", "obligatii_crescute": "on"}
    r = c.post("/api/risc-fiscal/perioada", data=date_complet)
    assert r.get_json()["clasificare"] == "ridicat"
    trimise = []
    risk_alerts_mod.verifica_si_alerteaza(
        app.portal_conn, app.firm_conn, lambda *a: trimise.append(a))
    assert len(trimise) == 1
    c.post("/api/risc-fiscal/perioada",
          data={**date_complet, "flag_declarat_inactiv": "on"})
    risk_alerts_mod.verifica_si_alerteaza(
        app.portal_conn, app.firm_conn, lambda *a: trimise.append(a))
    assert len(trimise) == 2


def test_verifica_si_alerteaza_ignora_scor_moderat_sau_scazut(app):
    from portal import risk_alerts as risk_alerts_mod
    c = _client_risc_fiscal_platit(app, "RO923", tip="direct",
                                   risc_fiscal_nivel="simplu",
                                   email="admin923@exemplu.ro")
    c.post("/api/risc-fiscal/perioada", data={
        "perioada": "2026-T2", "capitaluri_proprii": "100", "datorii_totale": "10",
        "cifra_afaceri": "1000", "rezultat_net": "10"})
    trimise = []
    n = risk_alerts_mod.verifica_si_alerteaza(
        app.portal_conn, app.firm_conn, lambda *a: trimise.append(a))
    assert n == 0


def test_verifica_si_alerteaza_ignora_firme_fara_modul_activat(app):
    from portal import risk_alerts as risk_alerts_mod
    c = app.test_client()
    inregistreaza(c, cui="RO924", tip="direct", email="admin924@exemplu.ro")
    trimise = []
    n = risk_alerts_mod.verifica_si_alerteaza(
        app.portal_conn, app.firm_conn, lambda *a: trimise.append(a))
    assert n == 0


def test_verifica_si_alerteaza_include_numele_clientului_pentru_firma_contabilitate(app):
    from portal import risk_alerts as risk_alerts_mod
    c = _client_risc_fiscal_platit(app, "RO925", tip="contabilitate",
                                   risc_fiscal_nivel="simplu",
                                   email="admin925@exemplu.ro")
    client_id = c.post("/api/clients", json={
        "cui": "RO9998", "name": "Client Risc Ridicat SRL",
        "gdpr_confirmat": True}).get_json()["id"]
    c.post("/api/risc-fiscal/perioada", data={
        "client_id": str(client_id), "perioada": "2026-T2",
        "capitaluri_proprii": "-1", "datorii_totale": "10",
        "cifra_afaceri": "1000", "rezultat_net": "-1"})
    trimise = []
    risk_alerts_mod.verifica_si_alerteaza(
        app.portal_conn, app.firm_conn,
        lambda dest, subiect, continut: trimise.append((dest, subiect, continut)))
    assert len(trimise) == 1
    assert "Client Risc Ridicat SRL" in trimise[0][1]
    assert "Client Risc Ridicat SRL" in trimise[0][2]


def test_risc_fiscal_izolat_intre_firme(app):
    """O firma nu poate vedea istoricul de risc fiscal al alteia - RLS/
    fisierul per-firma izoleaza automat, la fel ca la reconcilieri."""
    c1 = _client_risc_fiscal_platit(app, "RO911", tip="direct",
                                    risc_fiscal_nivel="simplu")
    c1.post("/api/risc-fiscal/perioada",
           data={"perioada": "2026-T2", "capitaluri_proprii": "100",
                 "datorii_totale": "10", "cifra_afaceri": "1000",
                 "rezultat_net": "10"})

    c2 = _client_risc_fiscal_platit(app, "RO912", tip="direct",
                                    risc_fiscal_nivel="simplu", seed_master=False)
    assert c2.get("/api/risc-fiscal/istoric").get_json() == []


# ---------- modulul premium Risc Fiscal: nivel, nomenclator, facturare ----------

def test_salveaza_plan_stores_risc_fiscal_nivel(app):
    from portal import db as pdb
    c = app.test_client()
    inregistreaza(c, cui="RO801")
    r = c.post("/panou/plan", data={"ciclu": "lunar", "risc_fiscal_nivel": "simplu"},
              follow_redirects=True)
    assert "Planul a fost salvat".encode() in r.data
    row = app.portal_conn.execute(
        "SELECT risc_fiscal_nivel FROM firms WHERE cui='RO801'").fetchone()
    assert row["risc_fiscal_nivel"] == pdb.RISC_FISCAL_SIMPLU


def test_salveaza_plan_allows_clearing_risc_fiscal_nivel(app):
    c = app.test_client()
    inregistreaza(c, cui="RO802")
    c.post("/panou/plan", data={"ciclu": "lunar", "risc_fiscal_nivel": "complet"})
    c.post("/panou/plan", data={"ciclu": "lunar", "risc_fiscal_nivel": ""})
    row = app.portal_conn.execute(
        "SELECT risc_fiscal_nivel FROM firms WHERE cui='RO802'").fetchone()
    assert row["risc_fiscal_nivel"] is None


def test_salveaza_plan_rejects_invalid_risc_fiscal_nivel(app):
    c = app.test_client()
    inregistreaza(c, cui="RO803")
    r = c.post("/panou/plan", data={"ciclu": "lunar", "risc_fiscal_nivel": "premium"},
              follow_redirects=True)
    assert "Nivel invalid".encode() in r.data
    row = app.portal_conn.execute(
        "SELECT risc_fiscal_nivel FROM firms WHERE cui='RO803'").fetchone()
    assert row["risc_fiscal_nivel"] is None


def test_master_poate_forta_nivel_risc_fiscal(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO804")
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO804'").fetchone()["id"]

    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c_master.post(f"/master/firma/{firm_id}/risc-fiscal/nivel",
                      data={"nivel": "complet"}, follow_redirects=True)
    assert r.status_code == 200
    row = app.portal_conn.execute(
        "SELECT risc_fiscal_nivel FROM firms WHERE id=?", (firm_id,)).fetchone()
    assert row["risc_fiscal_nivel"] == "complet"

    # Un nivel invalid nu modifica nimic si redirectioneaza cu eroare.
    r = c_master.post(f"/master/firma/{firm_id}/risc-fiscal/nivel",
                      data={"nivel": "premium"}, follow_redirects=True)
    assert "Nivel invalid".encode() in r.data
    row = app.portal_conn.execute(
        "SELECT risc_fiscal_nivel FROM firms WHERE id=?", (firm_id,)).fetchone()
    assert row["risc_fiscal_nivel"] == "complet"


def test_master_poate_dezactiva_nivel_risc_fiscal(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO805")
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO805'").fetchone()["id"]
    app.portal_conn.execute(
        "UPDATE firms SET risc_fiscal_nivel='simplu' WHERE id=?", (firm_id,))
    app.portal_conn.commit()

    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post(f"/master/firma/{firm_id}/risc-fiscal/nivel", data={"nivel": ""})
    row = app.portal_conn.execute(
        "SELECT risc_fiscal_nivel FROM firms WHERE id=?", (firm_id,)).fetchone()
    assert row["risc_fiscal_nivel"] is None


def test_seteaza_risc_fiscal_nivel_requires_master(app):
    c = app.test_client()
    r = c.post("/master/firma/1/risc-fiscal/nivel", data={"nivel": "simplu"},
              follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_master_identity_are_nivel_complet(app):
    """Masterul trebuie sa poata testa fluxul complet (indicatorii 4-5 +
    Sectiunea B) fara sa activeze plata modulul - vezi current_identity."""
    _seed_master(app)
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c_master.get("/api/me")
    assert r.get_json()["risc_fiscal_nivel"] == "complet"


def test_api_me_returns_risc_fiscal_nivel_for_firm(app):
    from portal import db as pdb
    c = app.test_client()
    inregistreaza(c, cui="RO806")
    assert c.get("/api/me").get_json()["risc_fiscal_nivel"] is None
    c.post("/panou/plan", data={"ciclu": "lunar", "risc_fiscal_nivel": "simplu"})
    # Alegerea nivelului, singura, NU da acces - current_identity() cere si
    # o plata de abonament chiar validata (vezi portal/app.py).
    assert c.get("/api/me").get_json()["risc_fiscal_nivel"] is None

    _seed_master(app)
    _apropie_trial_de_final(app, "RO806")
    _semneaza_contract_esemneaza(app, c)
    c.post("/panou/plata", data={})
    plata_id = app.portal_conn.execute(
        "SELECT p.id FROM payments p JOIN firms f ON f.id=p.firm_id "
        "WHERE f.cui='RO806'").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post(f"/master/plati/{plata_id}/valideaza", data={"judet": "Bucuresti"})

    assert c.get("/api/me").get_json()["risc_fiscal_nivel"] == pdb.RISC_FISCAL_SIMPLU


def test_salveaza_preturi_risc_fiscal_din_nomenclator(app):
    from portal import db as pdb
    _seed_master(app)
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c_master.post("/master/nomenclator/risc-fiscal", data={
        "risc_fiscal_simplu": "120", "risc_fiscal_simplu_incluse": "3",
        "risc_fiscal_simplu_extra": "40",
        "risc_fiscal_complet": "250", "risc_fiscal_complet_incluse": "10",
        "risc_fiscal_complet_extra": "80"},
        follow_redirects=True)
    assert "au fost actualizate".encode() in r.data
    preturi = pdb.get_preturi_module(app.portal_conn)
    assert preturi[pdb.MODUL_RISC_FISCAL_SIMPLU] == {
        "pret_lunar_ron": 120, "rapoarte_incluse": 3, "pret_raport_extra_ron": 40}
    assert preturi[pdb.MODUL_RISC_FISCAL_COMPLET] == {
        "pret_lunar_ron": 250, "rapoarte_incluse": 10, "pret_raport_extra_ron": 80}


def test_salveaza_preturi_risc_fiscal_respinge_valori_invalide(app):
    from portal import db as pdb
    _seed_master(app)
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c.post("/master/nomenclator/risc-fiscal", data={
        "risc_fiscal_simplu": "-5", "risc_fiscal_simplu_incluse": "5",
        "risc_fiscal_simplu_extra": "50",
        "risc_fiscal_complet": "250", "risc_fiscal_complet_incluse": "5",
        "risc_fiscal_complet_extra": "100"},
        follow_redirects=True)
    assert "trebuie sa fie" in r.get_data(as_text=True)
    preturi = pdb.get_preturi_module(app.portal_conn)
    assert preturi[pdb.MODUL_RISC_FISCAL_SIMPLU]["pret_lunar_ron"] == 200  # neschimbat


def test_valideaza_plata_adauga_linie_separata_pentru_risc_fiscal(app, monkeypatch):
    """Cand firma are un nivel de Risc Fiscal activ, factura lunara emisa
    prin FGO capata o a doua linie, cu CodArticol propriu si pretul din
    nomenclator - iar valoare_neta insumeaza ambele linii (vezi
    _emite_factura_fgo/linii_extra si valideaza_plata)."""
    c = app.test_client()
    inregistreaza(c, cui="RO807", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar", "risc_fiscal_nivel": "simplu"})
    _apropie_trial_de_final(app, "RO807")
    _semneaza_contract_esemneaza(app, c)
    c.post("/panou/plata", data={})
    plata_id = app.portal_conn.execute(
        "SELECT p.id FROM payments p JOIN firms f ON f.id=p.firm_id "
        "WHERE f.cui='RO807'").fetchone()["id"]

    _seed_master(app)
    continuturi_capturate = []

    def _fake_emite_factura(cod_unic, cheie_privata, platforma_url, mediu, *,
                            serie, continut, **kw):
        continuturi_capturate.append(continut)
        return {"Numar": "0099", "Serie": serie,
                "Link": "https://fgo.testuat/n/p/fake", "LinkPlata": None}
    monkeypatch.setattr(fgo, "emite_factura", _fake_emite_factura)

    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    r = c_master.post(f"/master/plati/{plata_id}/valideaza",
                      data={"judet": "Bucuresti"}, follow_redirects=True)
    assert "Incasarea a fost validata".encode() in r.data

    continut = continuturi_capturate[0]
    assert len(continut) == 2
    assert continut[0]["CodArticol"] == "ABONAMENT"
    assert continut[1]["CodArticol"] == "RISC_FISCAL_SIMPLU"
    assert continut[1]["PretUnitar"] == 200  # niciun raport generat -> doar abonamentul

    invoice_id = app.portal_conn.execute(
        "SELECT invoice_id FROM payments WHERE id=?", (plata_id,)).fetchone()["invoice_id"]
    factura = app.portal_conn.execute(
        "SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    # abonament direct lunar (59) + risc fiscal simplu (200) = 259
    assert factura["valoare_neta"] == 59 + 200
    assert factura["valoare_totala"] == round((59 + 200) * _multiplicator_tva(app), 2)


def test_cost_modul_risc_fiscal_adauga_supliment_peste_pragul_inclus(app, monkeypatch):
    """Fiecare raport generat peste pragul inclus (5/luna la nivelul
    simplu) se adauga la suma de plata la pretul per raport din
    nomenclator - verificat prin fluxul real de facturare (payments.suma),
    nu direct pe closure-ul intern _cost_modul_risc_fiscal. Accesul la
    /api/risc-fiscal/* cere acum o plata deja validata (current_identity()),
    deci firma plateste intai un ciclu normal (fara rapoarte inca -> fara
    supliment), genereaza rapoartele, apoi cere un al doilea ciclu - acela
    reflecta suplimentul acumulat luna aceasta."""
    c, firm_id, c_master = _firma_cu_abonament_platit(
        app, "RO926", tip="direct", risc_fiscal_nivel="simplu",
        reconcilieri_estimate=10)
    for i in range(7):  # 5 incluse + 2 peste prag * 50 RON = 100 RON supliment
        c.post("/api/risc-fiscal/perioada", data={
            "perioada": f"2026-T{i}", "capitaluri_proprii": "100",
            "datorii_totale": "10", "cifra_afaceri": "1000", "rezultat_net": "10"})
    c.post("/panou/plata", data={})
    plata2_id = app.portal_conn.execute(
        "SELECT id FROM payments WHERE firm_id=? AND stare=?",
        (firm_id, "in_asteptare")).fetchone()["id"]
    c_master.post(f"/master/plati/{plata2_id}/valideaza", data={"judet": "Bucuresti"})
    plata2 = app.portal_conn.execute(
        "SELECT suma FROM payments WHERE id=?", (plata2_id,)).fetchone()
    # abonament direct lunar (59) + risc fiscal simplu (200 + 2*50) = 359
    assert plata2["suma"] == round((59 + 200 + 100) * _multiplicator_tva(app), 2)
