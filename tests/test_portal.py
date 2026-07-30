import os
import re

import pytest
from portal.app import create_app
from portal import security as psec
from etva import anaf_cui
from etva import esemneaza

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
        # Conexiunea proprie a aplicatiei (portal_conn) trebuie inchisa
        # explicit - altfel DROP DATABASE esueaza cu "is being accessed by
        # other users", fiindca acea conexiune ramane deschisa pana la
        # garbage collection.
        a.portal_conn.close()
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
                                 "base": 42.0, "vat": 0.0}]


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
        "valoare_neta": "100", "cota_tva": "19"})
    c_master.post("/master/facturi", data={
        "firm_id": firm_id, "descriere": "Abonament iulie",
        "valoare_neta": "50", "cota_tva": "19"})

    rows = app.portal_conn.execute(
        "SELECT * FROM invoices ORDER BY numar").fetchall()
    assert len(rows) == 2
    assert rows[0]["numar"] == 1 and rows[1]["numar"] == 2
    assert rows[0]["serie"] == "ETVA"
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
        "cota_tva": "19"})
    factura_id = app.portal_conn.execute("SELECT id FROM invoices").fetchone()["id"]

    r = c.get(f"/master/facturi/{factura_id}/pdf", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_descarca_factura_pdf_returns_pdf_bytes(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO111")
    firm_id = app.portal_conn.execute("SELECT id FROM firms").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post("/master/facturi", data={
        "firm_id": firm_id, "descriere": "Abonament e-TVA Reconciliere",
        "valoare_neta": "100", "cota_tva": "19"})
    factura_id = app.portal_conn.execute("SELECT id FROM invoices").fetchone()["id"]

    r = c_master.get(f"/master/facturi/{factura_id}/pdf")
    assert r.status_code == 200
    assert r.mimetype == "application/pdf"
    assert r.data[:4] == b"%PDF"


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


# ---------- facturare: XML e-Factura + trimitere ANAF ----------

def _creeaza_factura(app, client_master, firm_id, descriere="Abonament"):
    client_master.post("/master/facturi", data={
        "firm_id": firm_id, "descriere": descriere,
        "valoare_neta": "100", "cota_tva": "19"})
    return app.portal_conn.execute(
        "SELECT id FROM invoices ORDER BY id DESC LIMIT 1").fetchone()["id"]


def _autorizeaza_vml_anaf(app, monkeypatch, tokens=None):
    """Inregistreaza firma emitentului insusi (VML, acelasi CUI ca
    portal.invoicing.FURNIZOR) si o trece prin fluxul de autorizare ANAF -
    exact ca orice alta firma, doar ca acum CUI-ul coincide cu emitentul."""
    from portal import invoicing
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(portal_app_module, "ANAF_OAUTH_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(anaf_oauth, "exchange_code_for_tokens",
                        lambda *a, **kw: tokens or {"access_token": "VML-TOKEN",
                                                    "refresh_token": "VML-REFRESH"})
    c = app.test_client()
    inregistreaza(c, name="VML EXPERT ADVISOR SRL", cui=invoicing.FURNIZOR["cui"],
                  tip="direct")
    _stare_anaf(c)
    return c


def test_descarca_factura_xml_requires_master(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO111")
    firm_id = app.portal_conn.execute("SELECT id FROM firms").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post("/master/facturi", data={
        "firm_id": firm_id, "descriere": "Abonament", "valoare_neta": "100",
        "cota_tva": "19"})
    factura_id = app.portal_conn.execute("SELECT id FROM invoices").fetchone()["id"]

    r = c.get(f"/master/facturi/{factura_id}/xml", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_descarca_factura_xml_returns_valid_xml(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, name="Firma Unu SRL", cui="RO111")
    firm_id = app.portal_conn.execute("SELECT id FROM firms").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    c_master.post("/master/facturi", data={
        "firm_id": firm_id, "descriere": "Abonament iunie", "valoare_neta": "100",
        "cota_tva": "19"})
    factura_id = app.portal_conn.execute("SELECT id FROM invoices").fetchone()["id"]

    r = c_master.get(f"/master/facturi/{factura_id}/xml")
    assert r.status_code == 200
    assert r.mimetype == "application/xml"
    assert b"<?xml" in r.data
    assert b"Firma Unu SRL" in r.data


def test_trimite_factura_anaf_requires_vml_own_authorization(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO111")
    firm_id = app.portal_conn.execute("SELECT id FROM firms").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    factura_id = _creeaza_factura(app, c_master, firm_id)

    r = c_master.post(f"/master/facturi/{factura_id}/trimite-anaf",
                      follow_redirects=True)
    assert "nu are acces ANAF autorizat".encode() in r.data
    row = app.portal_conn.execute(
        "SELECT anaf_stare FROM invoices WHERE id=?", (factura_id,)).fetchone()
    assert row["anaf_stare"] == "netrimisa"


def test_trimite_factura_anaf_uploads_and_updates_state(app, monkeypatch):
    _seed_master(app)
    _autorizeaza_vml_anaf(app, monkeypatch)
    c = app.test_client()
    inregistreaza(c, name="Firma Doi SRL", cui="RO222")
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO222'").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    factura_id = _creeaza_factura(app, c_master, firm_id)

    monkeypatch.setattr(anaf_oauth, "upload_invoice",
                        lambda *a, **kw: {"index_incarcare": "999888"})
    r = c_master.post(f"/master/facturi/{factura_id}/trimite-anaf",
                      follow_redirects=True)
    assert "trimisa la ANAF".encode() in r.data
    row = app.portal_conn.execute(
        "SELECT * FROM invoices WHERE id=?", (factura_id,)).fetchone()
    assert row["anaf_stare"] == "in_procesare"
    assert row["anaf_index_incarcare"] == "999888"
    assert row["anaf_trimis_la"] is not None


def test_trimite_factura_anaf_surfaces_anaf_rejection(app, monkeypatch):
    _seed_master(app)
    _autorizeaza_vml_anaf(app, monkeypatch)
    c = app.test_client()
    inregistreaza(c, name="Firma Doi SRL", cui="RO222")
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO222'").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    factura_id = _creeaza_factura(app, c_master, firm_id)

    def _boom(*a, **kw):
        raise anaf_oauth.AnafOAuthError("XML invalid")
    monkeypatch.setattr(anaf_oauth, "upload_invoice", _boom)
    r = c_master.post(f"/master/facturi/{factura_id}/trimite-anaf",
                      follow_redirects=True)
    assert "XML invalid".encode() in r.data
    row = app.portal_conn.execute(
        "SELECT anaf_stare FROM invoices WHERE id=?", (factura_id,)).fetchone()
    assert row["anaf_stare"] == "netrimisa"


def test_verifica_stare_still_processing(app, monkeypatch):
    _seed_master(app)
    _autorizeaza_vml_anaf(app, monkeypatch)
    c = app.test_client()
    inregistreaza(c, name="Firma Doi SRL", cui="RO222")
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO222'").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    factura_id = _creeaza_factura(app, c_master, firm_id)
    monkeypatch.setattr(anaf_oauth, "upload_invoice",
                        lambda *a, **kw: {"index_incarcare": "999888"})
    c_master.post(f"/master/facturi/{factura_id}/trimite-anaf")

    monkeypatch.setattr(anaf_oauth, "check_upload_status",
                        lambda *a, **kw: {"stare": "in prelucrare", "id_descarcare": None})
    r = c_master.post(f"/master/facturi/{factura_id}/verifica-stare",
                      follow_redirects=True)
    assert "inca in procesare".encode() in r.data
    row = app.portal_conn.execute(
        "SELECT anaf_stare FROM invoices WHERE id=?", (factura_id,)).fetchone()
    assert row["anaf_stare"] == "in_procesare"


def test_verifica_stare_accepted_downloads_and_stores_sealed_response(app, monkeypatch):
    _seed_master(app)
    _autorizeaza_vml_anaf(app, monkeypatch)
    c = app.test_client()
    inregistreaza(c, name="Firma Doi SRL", cui="RO222")
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO222'").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    factura_id = _creeaza_factura(app, c_master, firm_id)
    monkeypatch.setattr(anaf_oauth, "upload_invoice",
                        lambda *a, **kw: {"index_incarcare": "999888"})
    c_master.post(f"/master/facturi/{factura_id}/trimite-anaf")

    monkeypatch.setattr(anaf_oauth, "check_upload_status",
                        lambda *a, **kw: {"stare": "ok", "id_descarcare": "111222"})
    monkeypatch.setattr(anaf_oauth, "download_response",
                        lambda *a, **kw: b"PK\x03\x04semnat-de-anaf")
    r = c_master.post(f"/master/facturi/{factura_id}/verifica-stare",
                      follow_redirects=True)
    assert "acceptata de ANAF".encode() in r.data
    row = app.portal_conn.execute(
        "SELECT * FROM invoices WHERE id=?", (factura_id,)).fetchone()
    assert row["anaf_stare"] == "acceptata"
    assert row["anaf_id_descarcare"] == "111222"
    assert row["anaf_raspuns"] == b"PK\x03\x04semnat-de-anaf"


def test_verifica_stare_rejected(app, monkeypatch):
    _seed_master(app)
    _autorizeaza_vml_anaf(app, monkeypatch)
    c = app.test_client()
    inregistreaza(c, name="Firma Doi SRL", cui="RO222")
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO222'").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    factura_id = _creeaza_factura(app, c_master, firm_id)
    monkeypatch.setattr(anaf_oauth, "upload_invoice",
                        lambda *a, **kw: {"index_incarcare": "999888"})
    c_master.post(f"/master/facturi/{factura_id}/trimite-anaf")

    monkeypatch.setattr(anaf_oauth, "check_upload_status",
                        lambda *a, **kw: {"stare": "nok", "id_descarcare": "333444"})
    monkeypatch.setattr(anaf_oauth, "download_response",
                        lambda *a, **kw: b"PK\x03\x04eroare")
    r = c_master.post(f"/master/facturi/{factura_id}/verifica-stare",
                      follow_redirects=True)
    assert "respinsa de ANAF".encode() in r.data
    row = app.portal_conn.execute(
        "SELECT anaf_stare FROM invoices WHERE id=?", (factura_id,)).fetchone()
    assert row["anaf_stare"] == "respinsa"


def test_descarca_raspuns_anaf_requires_master(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO111")
    firm_id = app.portal_conn.execute("SELECT id FROM firms").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    factura_id = _creeaza_factura(app, c_master, firm_id)

    r = c.get(f"/master/facturi/{factura_id}/raspuns-anaf", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_descarca_raspuns_anaf_returns_none_when_not_yet_available(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO111")
    firm_id = app.portal_conn.execute("SELECT id FROM firms").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    factura_id = _creeaza_factura(app, c_master, firm_id)

    r = c_master.get(f"/master/facturi/{factura_id}/raspuns-anaf", follow_redirects=False)
    assert r.status_code == 302 and "/master/facturi" in r.headers["Location"]


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
    assert "ETVA".encode() in r.data
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
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, name="Firma Proprie SRL", cui="RO310")
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO310'").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    factura_id = _creeaza_factura(app, c_master, firm_id)

    r = c.get(f"/panou/factura/{factura_id}/pdf")
    assert r.status_code == 200
    assert r.mimetype == "application/pdf"
    assert r.data[:4] == b"%PDF"


def test_descarca_factura_proprie_xml_returns_own_invoice(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, name="Firma Proprie SRL", cui="RO311")
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO311'").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    factura_id = _creeaza_factura(app, c_master, firm_id, descriere="Abonament")

    r = c.get(f"/panou/factura/{factura_id}/xml")
    assert r.status_code == 200
    assert r.mimetype == "application/xml"
    assert b"<?xml" in r.data
    assert b"Firma Proprie SRL" in r.data


def test_descarca_raspuns_anaf_propriu_returns_none_when_not_yet_available(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO312")
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO312'").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    factura_id = _creeaza_factura(app, c_master, firm_id)

    r = c.get(f"/panou/factura/{factura_id}/raspuns-anaf", follow_redirects=False)
    assert r.status_code == 302 and "/panou/plan" in r.headers["Location"]


def test_descarca_raspuns_anaf_propriu_returns_zip_when_available(app, monkeypatch):
    _seed_master(app)
    _autorizeaza_vml_anaf(app, monkeypatch)
    c = app.test_client()
    inregistreaza(c, name="Firma Cinci SRL", cui="RO313")
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO313'").fetchone()["id"]
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})
    factura_id = _creeaza_factura(app, c_master, firm_id)
    monkeypatch.setattr(anaf_oauth, "upload_invoice",
                        lambda *a, **kw: {"index_incarcare": "999888"})
    c_master.post(f"/master/facturi/{factura_id}/trimite-anaf")
    monkeypatch.setattr(anaf_oauth, "check_upload_status",
                        lambda *a, **kw: {"stare": "ok", "id_descarcare": "111222"})
    monkeypatch.setattr(anaf_oauth, "download_response",
                        lambda *a, **kw: b"PK\x03\x04semnat-de-anaf")
    c_master.post(f"/master/facturi/{factura_id}/verifica-stare")

    r = c.get(f"/panou/factura/{factura_id}/raspuns-anaf")
    assert r.status_code == 200
    assert r.mimetype == "application/zip"
    assert r.data == b"PK\x03\x04semnat-de-anaf"


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

    r_xml = c_a.get(f"/panou/factura/{factura_b_id}/xml", follow_redirects=False)
    assert r_xml.status_code == 302 and "/panou/plan" in r_xml.headers["Location"]

    r_raspuns = c_a.get(f"/panou/factura/{factura_b_id}/raspuns-anaf",
                        follow_redirects=False)
    assert r_raspuns.status_code == 302 and "/panou/plan" in r_raspuns.headers["Location"]

    # Firma B insasi tot poate accesa propria factura - confirmam ca
    # respingerea de mai sus e specifica firmei A, nu o ruta stricata.
    r_ok = c_b.get(f"/panou/factura/{factura_b_id}/pdf")
    assert r_ok.status_code == 200 and r_ok.data[:4] == b"%PDF"


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
    r = c_master.post(f"/master/plati/{plata_id}/valideaza", follow_redirects=True)
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
    c_master.post(f"/master/plati/{plata_id}/valideaza")
    r = c_master.post(f"/master/plati/{plata_id}/valideaza", follow_redirects=True)
    assert "deja validata".encode() in r.data


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
    # Nicio ancora reala de incredere configurata (etva/trust_anchors/ e
    # gol) - deci valid dar netrusted, exact ce ar trebui sa raporteze.
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
    c_master.post(f"/master/plati/{plata_id}/valideaza")

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
