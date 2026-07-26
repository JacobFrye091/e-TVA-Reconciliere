import pytest
from portal.app import create_app
from portal import security as psec
from etva import anaf_cui


@pytest.fixture
def app(tmp_path):
    a = create_app(str(tmp_path))
    a.config["TESTING"] = True
    return a


@pytest.fixture(autouse=True)
def _mock_anaf_cui(monkeypatch):
    """Tests don't hit the real ANAF service: default to "CUI exists"."""
    def _fake(cui, on_date=None):
        return {"cui": anaf_cui.normalize_cui(cui), "denumire": "Firma Test",
                "adresa": "", "stare_inregistrare": "INREGISTRAT",
                "scpTVA": True}
    monkeypatch.setattr(anaf_cui, "verify_cui", _fake)


def inregistreaza(c, name="Firma Unu SRL", cui="RO111", tip="contabilitate",
                  email="test@exemplu.ro"):
    return c.post("/inregistrare", data={
        "name": name, "cui": cui, "tip": tip, "email": email,
        "password": "ParolaLunga123!", "accept_termeni": "on"},
        follow_redirects=False)


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


def test_migrate_stops_firms_from_reusing_a_soft_deleted_id(tmp_path):
    """Reproduces the real crash: a firm gets soft-deleted (firms/user_firms
    rows removed, firm_keys kept on purpose so the encrypted database stays
    recoverable - see sterge_toate_firmele.py), then a brand new firm gets
    handed that same id back by plain INTEGER PRIMARY KEY reuse and collides
    with the still-there firm_keys row."""
    import sqlite3
    from portal import db as pdb

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
    cur = reopened.execute(
        "INSERT INTO firms(name, cui) VALUES ('Firma Noua SRL', 'RO222')")
    id_nou = cur.lastrowid
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
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,1)",
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
          data={"name": "PFA Ionescu", "cui": "RO222", "tip": "direct"})
    # add_firm() comuta automat pe firma noua
    assert c.get("/api/clients").get_json() == []


def test_direct_firm_rejects_adding_a_client(app):
    c = app.test_client()
    inregistreaza(c, tip="direct")
    r = c.post("/api/clients", json={"cui": "RO999", "name": "Alta Firma"})
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
    c1 = app1.test_client()
    inregistreaza(c1)
    cid = c1.post("/api/clients",
                 json={"cui": "RO9", "name": "Client X"}).get_json()["id"]
    assert cid

    app2 = create_app(data_dir)  # simulates a server restart
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
    c1 = app1.test_client()
    r = inregistreaza(c1)
    session_cookie = r.headers["Set-Cookie"].split("session=")[1].split(";")[0]

    app2 = create_app(data_dir)  # simulates a server restart
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
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,1)",
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
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,1)",
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
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,1)",
        ("sef", psec.hash_password("ParolaMaster123!")))
    conn.commit()
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef",
                                   "password": "ParolaMaster123!"})
    text = c.get("/master").data.decode()
    assert "Server la zi" in text
    assert "repornește serverul" not in text


def test_master_cannot_use_app_api(app):
    conn = app.portal_conn
    conn.execute(
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,1)",
        ("sef", psec.hash_password("ParolaMaster123!")))
    conn.commit()
    c = app.test_client()
    c.post("/autentificare", data={"cui": "sef",
                                   "password": "ParolaMaster123!"})
    assert c.get("/api/me").status_code == 401


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
              data={"name": "Firma Doi PFA", "cui": "RO222", "tip": "direct"},
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


# ---------- dev/testare/productie pipeline (master dashboard) ----------

from portal import pipeline as pl


def _seed_master(app, username="sef", password="ParolaMaster123!"):
    conn = app.portal_conn
    conn.execute(
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,1)",
        (username, psec.hash_password(password)))
    conn.commit()


def test_pipeline_dashboard_requires_master(app):
    c = app.test_client()
    r = c.get("/master/pipeline")
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_pipeline_dashboard_and_promote(app, monkeypatch):
    _seed_master(app)
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
                 json={"cui": "RO9", "name": "Client X"}).get_json()["id"]
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
                  json={"cui": "RO2", "name": "Y"}).status_code == 403
    assert c.get("/api/clients").get_json() == []  # nimic alocat inca


def test_assignment_gives_visibility(app):
    c = app.test_client()
    inregistreaza(c)
    cid = c.post("/api/clients",
                 json={"cui": "RO9", "name": "Client X"}).get_json()["id"]
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
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,1)",
        ("sef", psec.hash_password("ParolaMaster123!")))
    conn.commit()
    c = app.test_client()
    inregistreaza(c)
    firm_id = conn.execute("SELECT id FROM firms").fetchone()["id"]
    conn.execute("UPDATE firms SET active=0 WHERE id=?", (firm_id,))
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
                 json={"cui": "RO999", "name": "Client X"}).get_json()["id"]

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
                 json={"cui": "RO999", "name": "Client X"}).get_json()["id"]

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
                 json={"cui": "RO999", "name": "Client X"}).get_json()["id"]
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
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,1)",
        ("sef", psec.hash_password("ParolaMaster123!")))
    conn.commit()

    c = app.test_client()
    inregistreaza(c, name="Firma1", cui="RO111", tip="contabilitate")
    cid = c.post("/api/clients",
                 json={"cui": "RO999", "name": "Client X"}).get_json()["id"]
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
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,1)",
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
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,1)",
        ("sef", psec.hash_password("ParolaMaster123!")))
    conn.commit()

    c1 = app.test_client()
    inregistreaza(c1, name="Firma1", cui="RO111", tip="contabilitate")
    cid = c1.post("/api/clients",
                  json={"cui": "RO999", "name": "Client X"}).get_json()["id"]
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
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,1)",
        ("sef", psec.hash_password("ParolaMaster123!")))
    conn.commit()

    c = app.test_client()
    inregistreaza(c, name="Firma Unu SRL", cui="RO111")
    c.post("/api/clients", json={"cui": "RO9", "name": "Client X"})
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
    from datetime import datetime, timedelta
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
        ((datetime.now() - timedelta(days=1)).isoformat(), firm_id))
    app.portal_conn.commit()

    monkeypatch.setattr(anaf_oauth, "refresh_access_token",
                        lambda *a, **kw: {"access_token": "REFRESHED", "refresh_token": "R2"})
    assert app.get_valid_anaf_access_token(firm_id) == "REFRESHED"
    row = app.portal_conn.execute(
        "SELECT * FROM anaf_oauth_tokens WHERE firm_id=?", (firm_id,)).fetchone()
    assert row["expira_la"] > datetime.now().isoformat()
    assert row["autorizat_de"] == "firma-unu-srl"


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
    assert invoicing.next_invoice_number(app.portal_conn, "ETVA") == 1
    app.portal_conn.execute(
        "INSERT INTO invoices(serie, numar, firm_id, firm_name, firm_cui, "
        "descriere, data_emiterii, valoare_neta, cota_tva, valoare_tva, "
        "valoare_totala, creat_de, creat_la) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("ETVA", 1, 1, "Test SRL", "RO1", "Test", "2026-01-01", 100, 19, 19,
         119, "sef", "2026-01-01"))
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


def test_restaureaza_backup_requires_confirmation(app, monkeypatch):
    monkeypatch.setattr(pl, "own_environment", lambda: "testare")
    _seed_master(app)
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    r = c_master.post("/master/backup/restaureaza", data={}, follow_redirects=True)
    assert "confirmi explicit".encode() in r.data


def test_restaureaza_backup_requires_file(app, monkeypatch):
    monkeypatch.setattr(pl, "own_environment", lambda: "testare")
    _seed_master(app)
    c_master = app.test_client()
    c_master.post("/autentificare", data={"cui": "sef", "password": "ParolaMaster123!"})

    r = c_master.post("/master/backup/restaureaza", data={"confirm": "da"},
                      follow_redirects=True)
    assert "Alege un fisier".encode() in r.data


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
        "name": "A Doua Firma SRL", "cui": "RO902", "tip": "direct"})
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
    assert str(pdb.PRETURI_LUNARE_RON["contabilitate"]["lunar"]).encode() in r.data


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


def test_creeaza_cerere_plata_direct_firm(app):
    c = app.test_client()
    inregistreaza(c, cui="RO204", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    _apropie_trial_de_final(app, "RO204")
    r = c.post("/panou/plata", data={"recurent": "on"}, follow_redirects=True)
    assert "Cererea de plata a fost inregistrata".encode() in r.data
    row = app.portal_conn.execute(
        "SELECT p.* FROM payments p JOIN firms f ON f.id=p.firm_id "
        "WHERE f.cui='RO204'").fetchone()
    assert row["suma"] == 59  # pret lunar direct, un singur ciclu de o luna
    assert row["recurent"] == 1
    assert row["stare"] == "in_asteptare"


def test_creeaza_cerere_plata_contabilitate_firm_floors_at_one_client(app):
    """O firma de contabilitate abia inregistrata n-are inca niciun client
    - suma trebuie calculata ca pentru minim 1 client, nu 0 RON."""
    c = app.test_client()
    inregistreaza(c, cui="RO205", tip="contabilitate")
    c.post("/panou/plan", data={"ciclu": "an"})
    _apropie_trial_de_final(app, "RO205")
    firm_id = app.portal_conn.execute(
        "SELECT id FROM firms WHERE cui='RO205'").fetchone()["id"]
    c.post("/panou/plata", data={})
    row = app.portal_conn.execute(
        "SELECT suma FROM payments WHERE firm_id=?", (firm_id,)).fetchone()
    assert row["suma"] == 15 * 12


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


def test_valideaza_plata_rejects_already_validated(app):
    _seed_master(app)
    c = app.test_client()
    inregistreaza(c, cui="RO207", tip="direct")
    c.post("/panou/plan", data={"ciclu": "lunar"})
    _apropie_trial_de_final(app, "RO207")
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
    c.post("/panou/plata", data={})
    r = c.get("/panou/plan")
    assert "Istoricul pl".encode() in r.data
    assert "59.00".encode() in r.data or "59.0".encode() in r.data


def test_master_backup_list_requires_master(app):
    c = app.test_client()
    r = c.get("/master/backup", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


def test_creeaza_backup_requires_master(app):
    c = app.test_client()
    r = c.post("/master/backup/creeaza", follow_redirects=False)
    assert r.status_code == 302 and "/autentificare" in r.headers["Location"]


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
