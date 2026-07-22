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


def inregistreaza(c, username="firma1", cui="RO111"):
    return c.post("/inregistrare", data={
        "name": "Firma Unu SRL", "cui": cui,
        "username": username, "password": "ParolaLunga123!"},
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
    r = inregistreaza(c, username="alta", cui="RO111")
    assert b"CUI" in r.data


def test_login_wrong_password(app):
    c = app.test_client()
    inregistreaza(c)
    c.get("/iesire")
    r = c.post("/autentificare",
               data={"username": "firma1", "password": "gresit"})
    assert "incorecta".encode() in r.data


def test_api_me_returns_identity(app):
    c = app.test_client()
    inregistreaza(c)
    r = c.get("/api/me")
    assert r.status_code == 200
    body = r.get_json()
    assert body["role"] == "admin" and body["firm_name"] == "Firma Unu SRL"
    assert "rapoarte.export" in body["permissions"]


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
    c2.post("/autentificare", data={"username": "firma1",
                                    "password": "ParolaLunga123!"})
    vis = c2.get("/api/clients").get_json()
    assert [x["cui"] for x in vis] == ["RO9"]


def test_member_roles_and_permissions(app):
    c = app.test_client()
    inregistreaza(c)
    c.post("/panou/utilizatori", data={"username": "junior1",
                                       "password": "ParolaLunga123!",
                                       "role": "junior"})
    c.get("/iesire")
    c.post("/autentificare", data={"username": "junior1",
                                   "password": "ParolaLunga123!"})
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
    r = c_master.post("/autentificare", data={"username": "sef",
                                              "password": "ParolaMaster123!"})
    assert "/master" in r.headers["Location"]
    assert b"Firma Unu SRL" in c_master.get("/master").data

    firm_id = conn.execute("SELECT id FROM firms").fetchone()["id"]
    c_master.post(f"/master/firma/{firm_id}/comutare")

    assert c_firma.get("/api/me").status_code == 401


def test_master_cannot_use_app_api(app):
    conn = app.portal_conn
    conn.execute(
        "INSERT INTO users(username, pw_hash, is_master) VALUES(?,?,1)",
        ("sef", psec.hash_password("ParolaMaster123!")))
    conn.commit()
    c = app.test_client()
    c.post("/autentificare", data={"username": "sef",
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
    r = c.post("/panou/firme", data={"name": "Firma Doi PFA", "cui": "RO222"},
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
    r = c.post("/panou/firme", data={"name": "Firma Fantoma", "cui": "RO333"},
               follow_redirects=True)
    assert "nu a fost gasit la ANAF".encode() in r.data
    assert not app.portal_conn.execute(
        "SELECT 1 FROM firms WHERE cui='RO333'").fetchone()


def test_add_firm_rejects_duplicate_cui(app):
    c = app.test_client()
    inregistreaza(c)
    r = c.post("/panou/firme", data={"name": "Alta Denumire", "cui": "RO111"},
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
    c.post("/autentificare", data={"username": "sef", "password": "ParolaMaster123!"})
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
    c.post("/autentificare", data={"username": "sef", "password": "ParolaMaster123!"})
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
    c.post("/autentificare", data={"username": "sef", "password": "ParolaMaster123!"})
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
                                       "password": "ParolaLunga123!",
                                       "role": "junior"})
    c.get("/iesire")
    c.post("/autentificare", data={"username": "jr",
                                   "password": "ParolaLunga123!"})
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
                                       "password": "ParolaLunga123!",
                                       "role": "contabil"})
    c.post("/api/assignments", json={"username": "cont1", "client_id": cid})
    c.get("/iesire")
    c.post("/autentificare", data={"username": "cont1",
                                   "password": "ParolaLunga123!"})
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
