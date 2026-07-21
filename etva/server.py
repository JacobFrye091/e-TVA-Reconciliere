"""Local Flask apps for the desktop shell.

`create_gate_app` runs before login: it authenticates against the account
portal, opens the firm's encrypted DB with the key received and hands the
connection + identity to the caller. `create_app` serves the main product
for one authenticated identity (single local user per window).
"""
import json, os, pathlib, secrets
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, request, jsonify, send_file
from etva import audit, clients, portal_client
from etva.importer.company import parse_company_journal, ImportError_
from etva.importer.anaf import FileAnafDataSource
from etva.engine import reconcile
from etva.advisor import suggest_d300
from etva import export as export_mod

_WEB_DIR = str(pathlib.Path(__file__).resolve().parents[1] / "web")


def create_app(conn, upload_dir: str, identity: dict, on_logout=None) -> Flask:
    app = Flask(__name__, static_folder=_WEB_DIR, static_url_path="/static")
    app.secret_key = secrets.token_hex(32)
    perms = set(identity.get("permissions", []))
    username = identity["username"]

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    def require(perm=None):
        def deco(fn):
            @wraps(fn)
            def wrapper(*a, **kw):
                if perm and perm not in perms:
                    return jsonify({"error": "Acces interzis"}), 403
                return fn(*a, **kw)
            return wrapper
        return deco

    @app.get("/api/me")
    def me():
        return jsonify({"username": username,
                        "role": identity.get("role"),
                        "firm_name": identity.get("firm_name"),
                        "permissions": sorted(perms)})

    @app.post("/api/logout")
    def logout():
        audit.log(conn, username, "logout")
        if on_logout:
            on_logout()
        return jsonify({"ok": True})

    @app.get("/api/clients")
    def list_clients():
        return jsonify(clients.visible_clients(conn, identity))

    @app.post("/api/clients")
    @require("clienti.creare")
    def add_client():
        data = request.get_json(force=True)
        try:
            cid = clients.create_client(conn, data["cui"], data["name"])
        except clients.ClientError as e:
            return jsonify({"error": str(e)}), 400
        audit.log(conn, username, "client.creare", "client", str(cid))
        return jsonify({"id": cid})

    @app.delete("/api/clients/<int:cid>")
    @require("clienti.stergere")
    def del_client(cid):
        clients.delete_client(conn, cid)
        audit.log(conn, username, "client.stergere", "client", str(cid))
        return jsonify({"ok": True})

    @app.post("/api/assignments")
    @require("useri.gestionare")
    def assign_client():
        data = request.get_json(force=True)
        clients.assign(conn, data["username"].strip(), int(data["client_id"]))
        audit.log(conn, username, "client.alocare", "client",
                  str(data["client_id"]))
        return jsonify({"ok": True})

    def _save_upload(f):
        path = os.path.join(upload_dir, secrets.token_hex(8) + "_" + f.filename)
        f.save(path)
        return path

    def _persist(client_id, period, comp_rows, anaf_rows):
        cur = conn.execute(
            "INSERT INTO reconciliations(client_id, period, created_at, "
            "created_by) VALUES(?,?,?,?)",
            (client_id, period,
             datetime.now(timezone.utc).isoformat(), username))
        rid = cur.lastrowid
        for table, rows in (("invoices_company", comp_rows),
                            ("invoices_anaf", anaf_rows)):
            conn.executemany(
                f"INSERT INTO {table}(reconciliation_id, partner_cui, "
                "invoice_no, date, base, vat, category) VALUES(?,?,?,?,?,?,?)",
                [(rid, r["partner_cui"], r["invoice_no"], r["date"],
                  r["base"], r["vat"], r["category"]) for r in rows])
        conn.commit()
        return rid

    def _result_payload(rid, comp_rows, anaf_rows):
        result = reconcile(comp_rows, anaf_rows)
        conn.execute("DELETE FROM differences WHERE reconciliation_id=?", (rid,))
        conn.executemany(
            "INSERT INTO differences(reconciliation_id, diff_type, details) "
            "VALUES(?,?,?)",
            [(rid, d["diff_type"], json.dumps(d)) for d in result.differences])
        conn.commit()
        return {"id": rid,
                "totals_company": result.totals_company,
                "totals_anaf": result.totals_anaf,
                "differences": result.differences,
                "suggestions": suggest_d300(result)}

    @app.post("/api/reconciliations")
    @require("reconciliere.creare")
    def new_reconciliation():
        client_id = int(request.form["client_id"])
        period = request.form["period"]
        mapping = None
        if request.form.get("anaf_mapping"):
            mapping = json.loads(request.form["anaf_mapping"])
        try:
            comp_rows = parse_company_journal(
                _save_upload(request.files["company_file"]))
            anaf_rows = FileAnafDataSource(
                _save_upload(request.files["anaf_file"]),
                mapping).get_etva_data("", period)
        except ImportError_ as e:
            return jsonify({"errors": e.errors}), 400
        rid = _persist(client_id, period, comp_rows, anaf_rows)
        audit.log(conn, username, "reconciliere.creare",
                  "reconciliation", str(rid))
        return jsonify(_result_payload(rid, comp_rows, anaf_rows))

    def _load_rows(rid, table):
        rows = conn.execute(
            f"SELECT partner_cui, invoice_no, date, base, vat, category "
            f"FROM {table} WHERE reconciliation_id=?", (rid,))
        return [dict(r) for r in rows]

    @app.get("/api/reconciliations/<int:rid>")
    def get_reconciliation(rid):
        comp = _load_rows(rid, "invoices_company")
        anaf = _load_rows(rid, "invoices_anaf")
        return jsonify(_result_payload(rid, comp, anaf))

    @app.get("/api/reconciliations/<int:rid>/export")
    @require("rapoarte.export")
    def export_report(rid):
        row = conn.execute(
            "SELECT r.period, c.name FROM reconciliations r "
            "JOIN clients c ON c.id = r.client_id WHERE r.id=?",
            (rid,)).fetchone()
        if row is None:
            return jsonify({"error": "Reconciliere inexistenta"}), 404
        comp = _load_rows(rid, "invoices_company")
        anaf = _load_rows(rid, "invoices_anaf")
        result = reconcile(comp, anaf)
        path = os.path.join(upload_dir, f"raport_{rid}.xlsx")
        export_mod.write_report(result, suggest_d300(result), path,
                                row["name"], row["period"])
        audit.log(conn, username, "raport.export", "reconciliation", str(rid))
        return send_file(path, as_attachment=True,
                         download_name=f"raport_{rid}.xlsx")

    @app.get("/api/audit")
    @require("audit.vizualizare")
    def audit_view():
        return jsonify(audit.entries(conn))

    return app


def create_gate_app(app_dir: str, portal_url: str, on_ready) -> Flask:
    """Pre-login app: portal authentication + opening the firm DB."""
    from etva import db as db_mod
    app = Flask(__name__, static_folder=_WEB_DIR, static_url_path="/static")
    app.secret_key = secrets.token_hex(32)

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/me")
    def me():
        return jsonify({"error": "Neautentificat"}), 401

    @app.post("/api/login")
    def login():
        data = request.get_json(force=True)
        try:
            ident = portal_client.authenticate(
                portal_url, data.get("username", ""), data.get("password", ""))
        except portal_client.PortalError as e:
            return jsonify({"error": str(e)}), e.status
        key = bytes.fromhex(ident.pop("data_key"))
        db_path = os.path.join(app_dir, f"firm_{ident['firm_id']}.db")
        conn = db_mod.open_db(db_path, key)
        db_mod.init_schema(conn)
        audit.log(conn, ident["username"], "login")
        on_ready(conn, ident)
        return jsonify({"username": ident["username"],
                        "role": ident["role"],
                        "firm_name": ident.get("firm_name"),
                        "permissions": ident["permissions"]})

    return app
