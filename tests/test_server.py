import io, os, pytest, pandas as pd
from etva import db, clients
from etva.server import create_app, create_gate_app
from etva import portal_client

ADMIN_ID = {"username": "sef", "role": "admin", "firm_id": 1,
            "firm_name": "Firma SRL",
            "permissions": list(db.PERMISSIONS)}
JUNIOR_ID = {"username": "junior1", "role": "junior", "firm_id": 1,
             "firm_name": "Firma SRL",
             "permissions": db.DEFAULT_ROLES["Junior"]}


def make_app(tmp_path, identity):
    conn = db.open_db(str(tmp_path / "a.db"), os.urandom(32))
    db.init_schema(conn)
    app = create_app(conn, str(tmp_path), identity)
    app.config["TESTING"] = True
    return app


def _csv(df):
    return io.BytesIO(df.to_csv(index=False).encode())


def _journal():
    return pd.DataFrame({"cui_partener": ["RO1"], "nr_factura": ["F1"],
                         "data": ["2026-01-10"], "baza": ["100"],
                         "tva": ["19"], "categorie": ["livrari_interne"]})


def test_me_returns_identity(tmp_path):
    c = make_app(tmp_path, ADMIN_ID).test_client()
    body = c.get("/api/me").get_json()
    assert body["username"] == "sef" and "rapoarte.export" in body["permissions"]


def test_full_reconciliation_flow(tmp_path):
    c = make_app(tmp_path, ADMIN_ID).test_client()
    cid = c.post("/api/clients",
                 json={"cui": "RO9", "name": "Firma"}).get_json()["id"]
    anaf = _journal(); anaf.loc[0, "baza"] = "150"
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-01",
        "company_file": (_csv(_journal()), "j.csv"),
        "anaf_file": (_csv(anaf), "a.csv"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    assert body["differences"][0]["diff_type"] == "suma_diferita"
    rid = body["id"]
    r = c.get(f"/api/reconciliations/{rid}/export")
    assert r.status_code == 200 and r.data[:2] == b"PK"


def test_junior_cannot_export_or_manage(tmp_path):
    c = make_app(tmp_path, JUNIOR_ID).test_client()
    assert c.get("/api/reconciliations/1/export").status_code == 403
    assert c.post("/api/clients",
                  json={"cui": "RO9", "name": "F"}).status_code == 403
    assert c.post("/api/assignments",
                  json={"username": "x", "client_id": 1}).status_code == 403


def test_assignment_restricts_visibility(tmp_path):
    conn = db.open_db(str(tmp_path / "a.db"), os.urandom(32))
    db.init_schema(conn)
    admin = create_app(conn, str(tmp_path), ADMIN_ID)
    admin.config["TESTING"] = True
    ca = admin.test_client()
    c1 = ca.post("/api/clients", json={"cui": "RO1", "name": "A"}).get_json()["id"]
    ca.post("/api/clients", json={"cui": "RO2", "name": "B"})
    ca.post("/api/assignments", json={"username": "junior1", "client_id": c1})
    junior = create_app(conn, str(tmp_path), JUNIOR_ID)
    junior.config["TESTING"] = True
    vis = junior.test_client().get("/api/clients").get_json()
    assert [c["cui"] for c in vis] == ["RO1"]


def test_import_errors_returned(tmp_path):
    c = make_app(tmp_path, ADMIN_ID).test_client()
    cid = c.post("/api/clients",
                 json={"cui": "RO9", "name": "Firma"}).get_json()["id"]
    bad = _journal().drop(columns=["tva"])
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-01",
        "company_file": (_csv(bad), "j.csv"),
        "anaf_file": (_csv(_journal()), "a.csv"),
    }, content_type="multipart/form-data")
    assert r.status_code == 400
    assert "tva" in r.get_json()["errors"][0]


def test_audit_written(tmp_path):
    c = make_app(tmp_path, ADMIN_ID).test_client()
    c.post("/api/clients", json={"cui": "RO9", "name": "Firma"})
    rows = c.get("/api/audit").get_json()
    assert rows[0]["action"] == "client.creare"
    assert rows[0]["user_id"] == "sef"


def test_index_served(tmp_path):
    c = make_app(tmp_path, ADMIN_ID).test_client()
    r = c.get("/")
    assert r.status_code == 200 and b"e-TVA Reconciliere" in r.data


# ---------- gate ----------

def test_gate_login_success(tmp_path, monkeypatch):
    key = os.urandom(32)

    def fake_auth(url, username, password):
        assert username == "sef"
        return {"username": "sef", "role": "admin", "firm_id": 7,
                "firm_name": "Firma SRL", "firm_cui": "RO1",
                "permissions": list(db.PERMISSIONS),
                "data_key": key.hex()}

    monkeypatch.setattr(portal_client, "authenticate", fake_auth)
    holder = {}
    gate = create_gate_app(str(tmp_path), "http://portal",
                           lambda conn, ident: holder.update(conn=conn,
                                                             ident=ident))
    gate.config["TESTING"] = True
    c = gate.test_client()
    assert c.get("/api/me").status_code == 401
    r = c.post("/api/login", json={"username": "sef", "password": "x"})
    assert r.status_code == 200
    assert r.get_json()["firm_name"] == "Firma SRL"
    assert holder["ident"]["firm_id"] == 7
    assert os.path.exists(str(tmp_path / "firm_7.db"))
    # audit already has the login entry
    rows = holder["conn"].execute("SELECT action FROM audit_log").fetchall()
    assert rows[0]["action"] == "login"


def test_gate_login_portal_down(tmp_path, monkeypatch):
    def fake_auth(url, username, password):
        raise portal_client.PortalError("Portalul de conturi nu poate fi "
                                        "contactat.", 502)
    monkeypatch.setattr(portal_client, "authenticate", fake_auth)
    gate = create_gate_app(str(tmp_path), "http://portal", lambda c, i: None)
    gate.config["TESTING"] = True
    r = gate.test_client().post("/api/login",
                                json={"username": "x", "password": "y"})
    assert r.status_code == 502
    assert "Portalul" in r.get_json()["error"]
