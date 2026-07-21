import pytest
from portal.app import create_app
from portal import security as psec


@pytest.fixture
def app(tmp_path):
    a = create_app(str(tmp_path))
    a.config["TESTING"] = True
    return a


def inregistreaza(c, username="firma1", cui="RO111"):
    return c.post("/inregistrare", data={
        "name": "Firma Unu SRL", "cui": cui,
        "username": username, "password": "ParolaLunga123!"},
        follow_redirects=False)


def test_register_redirects_to_panou(app):
    c = app.test_client()
    r = inregistreaza(c)
    assert r.status_code == 302 and "/panou" in r.headers["Location"]
    r = c.get("/panou")
    assert "Firma Unu SRL".encode() in r.data


def test_register_duplicate_cui(app):
    c = app.test_client()
    inregistreaza(c)
    r = inregistreaza(c, username="alta", cui="RO111")
    assert b"CUI" in r.data


def test_login_wrong_password(app):
    c = app.test_client()
    inregistreaza(c)
    c.get("/iesire")
    r = c.post("/autentificare",
               data={"username": "firma1", "password": "gresit"})
    assert "incorecta".encode() in r.data


def test_api_auth_returns_identity_and_key(app):
    c = app.test_client()
    inregistreaza(c)
    r = c.post("/api/auth", json={"username": "firma1",
                                  "password": "ParolaLunga123!"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["role"] == "admin" and body["firm_cui"] == "RO111"
    assert "rapoarte.export" in body["permissions"]
    assert len(bytes.fromhex(body["data_key"])) == 32


def test_api_auth_stable_key(app):
    c = app.test_client()
    inregistreaza(c)
    k1 = c.post("/api/auth", json={"username": "firma1",
                                   "password": "ParolaLunga123!"}).get_json()["data_key"]
    k2 = c.post("/api/auth", json={"username": "firma1",
                                   "password": "ParolaLunga123!"}).get_json()["data_key"]
    assert k1 == k2


def test_member_roles_and_permissions(app):
    c = app.test_client()
    inregistreaza(c)
    c.post("/panou/utilizatori", data={"username": "junior1",
                                       "password": "ParolaLunga123!",
                                       "role": "junior"})
    r = c.post("/api/auth", json={"username": "junior1",
                                  "password": "ParolaLunga123!"})
    body = r.get_json()
    assert body["role"] == "junior"
    assert "rapoarte.export" not in body["permissions"]
    assert body["data_key"]  # same firm key delivered


def test_master_dashboard_and_firm_toggle(app):
    conn = app.portal_conn
    conn.execute(
        "INSERT INTO users(firm_id, username, pw_hash, role) VALUES(NULL,?,?,?)",
        ("sef", psec.hash_password("ParolaMaster123!"), "master"))
    conn.commit()
    c = app.test_client()
    inregistreaza(c)
    c.get("/iesire")
    r = c.post("/autentificare", data={"username": "sef",
                                       "password": "ParolaMaster123!"})
    assert "/master" in r.headers["Location"]
    assert b"Firma Unu SRL" in c.get("/master").data
    firm_id = conn.execute("SELECT id FROM firms").fetchone()["id"]
    c.post(f"/master/firma/{firm_id}/comutare")
    r = c.post("/api/auth", json={"username": "firma1",
                                  "password": "ParolaLunga123!"})
    assert r.status_code == 403


def test_master_cannot_use_app_api(app):
    conn = app.portal_conn
    conn.execute(
        "INSERT INTO users(firm_id, username, pw_hash, role) VALUES(NULL,?,?,?)",
        ("sef", psec.hash_password("ParolaMaster123!"), "master"))
    conn.commit()
    r = app.test_client().post("/api/auth", json={
        "username": "sef", "password": "ParolaMaster123!"})
    assert r.status_code == 401
