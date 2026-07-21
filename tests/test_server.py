import io, os, json, pytest, pandas as pd
from etva import db, auth, permissions as pm, clients
from etva.server import create_app, create_setup_app

@pytest.fixture
def app(tmp_path):
    conn = db.open_db(str(tmp_path / "a.db"), os.urandom(32))
    db.init_schema(conn)
    uid = auth.create_user(conn, "admin", "Parola123!")
    pm.assign_role(conn, uid, "Admin")
    jid = auth.create_user(conn, "junior", "Parola123!")
    pm.assign_role(conn, jid, "Junior")
    application = create_app(conn, str(tmp_path))
    application.config["TESTING"] = True
    return application

def login(client, user="admin"):
    r = client.post("/api/login",
                    json={"username": user, "password": "Parola123!"})
    assert r.status_code == 200
    return r

def _csv(df):
    return io.BytesIO(df.to_csv(index=False).encode())

def _journal():
    return pd.DataFrame({"cui_partener": ["RO1"], "nr_factura": ["F1"],
                         "data": ["2026-01-10"], "baza": ["100"],
                         "tva": ["19"], "categorie": ["livrari_interne"]})

def test_login_bad_password(app):
    c = app.test_client()
    r = c.post("/api/login", json={"username": "admin", "password": "x"})
    assert r.status_code == 401

def test_full_reconciliation_flow(app):
    c = app.test_client()
    login(c)
    r = c.post("/api/clients", json={"cui": "RO9", "name": "Firma"})
    cid = r.get_json()["id"]
    anaf = _journal(); anaf.loc[0, "baza"] = "150"
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-01",
        "company_file": (_csv(_journal()), "j.csv"),
        "anaf_file": (_csv(anaf), "a.csv"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    assert body["differences"][0]["diff_type"] == "suma_diferita"
    assert body["suggestions"][0]["status"] == "de_verificat"
    rid = body["id"]
    r = c.get(f"/api/reconciliations/{rid}")
    assert r.get_json()["differences"][0]["diff_type"] == "suma_diferita"
    r = c.get(f"/api/reconciliations/{rid}/export")
    assert r.status_code == 200
    assert r.data[:2] == b"PK"  # xlsx zip magic

def test_junior_cannot_export(app):
    c = app.test_client()
    login(c, "junior")
    r = c.get("/api/reconciliations/1/export")
    assert r.status_code == 403

def test_junior_cannot_manage_users(app):
    c = app.test_client()
    login(c, "junior")
    r = c.post("/api/admin/users",
               json={"username": "x", "password": "y", "role": "Junior"})
    assert r.status_code == 403

def test_import_errors_returned(app):
    c = app.test_client()
    login(c)
    r = c.post("/api/clients", json={"cui": "RO9", "name": "Firma"})
    cid = r.get_json()["id"]
    bad = _journal().drop(columns=["tva"])
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-01",
        "company_file": (_csv(bad), "j.csv"),
        "anaf_file": (_csv(_journal()), "a.csv"),
    }, content_type="multipart/form-data")
    assert r.status_code == 400
    assert "tva" in r.get_json()["errors"][0]

def test_index_served(app):
    c = app.test_client()
    r = c.get("/")
    assert r.status_code == 200
    assert b"e-TVA Reconciliere" in r.data

def test_audit_written(app):
    c = app.test_client()
    login(c)
    r = c.get("/api/audit")
    assert r.status_code == 200
    assert r.get_json()[0]["action"] == "login"

def test_setup_flow(tmp_path):
    holder = {}
    app2 = create_setup_app(str(tmp_path), lambda conn: holder.update(conn=conn))
    app2.config["TESTING"] = True
    c = app2.test_client()
    assert c.get("/api/setup/status").get_json() == {"initialized": False}
    r = c.post("/api/setup", json={"master_password": "Master123!",
                                   "admin_username": "admin",
                                   "admin_password": "Admin123!"})
    phrase = r.get_json()["recovery_phrase"]
    assert len(phrase.split()) == 24
    assert c.get("/api/setup/status").get_json() == {"initialized": True}
    r = c.post("/api/setup/unlock", json={"master_password": "gresit"})
    assert r.status_code == 401
    r = c.post("/api/setup/unlock", json={"master_password": "Master123!"})
    assert r.status_code == 200 and "conn" in holder

def test_setup_recover(tmp_path):
    holder = {}
    app2 = create_setup_app(str(tmp_path), lambda conn: holder.update(conn=conn))
    app2.config["TESTING"] = True
    c = app2.test_client()
    phrase = c.post("/api/setup", json={
        "master_password": "Master123!", "admin_username": "admin",
        "admin_password": "x"}).get_json()["recovery_phrase"]
    r = c.post("/api/setup/recover", json={
        "recovery_phrase": phrase, "new_master_password": "Nou123!"})
    assert r.status_code == 200 and "conn" in holder
