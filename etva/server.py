"""Local Flask API. Runs only on 127.0.0.1, rendered inside pywebview."""
import json, os, pathlib, secrets
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, request, session, jsonify, send_file
from etva import auth, audit, clients, permissions as pm
from etva.importer.company import parse_company_journal, ImportError_
from etva.importer.anaf import FileAnafDataSource
from etva.engine import reconcile
from etva.advisor import suggest_d300
from etva import export as export_mod

_WEB_DIR = str(pathlib.Path(__file__).resolve().parents[1] / "web")


def create_app(conn, upload_dir: str) -> Flask:
    app = Flask(__name__, static_folder=_WEB_DIR, static_url_path="/static")
    app.secret_key = secrets.token_hex(32)

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    def current_user():
        return session.get("user_id")

    def require(perm=None):
        def deco(fn):
            @wraps(fn)
            def wrapper(*a, **kw):
                uid = current_user()
                if uid is None:
                    return jsonify({"error": "Neautentificat"}), 401
                if perm and not pm.has_permission(conn, uid, perm):
                    return jsonify({"error": "Acces interzis"}), 403
                return fn(uid, *a, **kw)
            return wrapper
        return deco

    @app.post("/api/login")
    def login():
        data = request.get_json(force=True)
        try:
            uid = auth.verify_login(conn, data["username"], data["password"])
        except auth.AuthError as e:
            return jsonify({"error": str(e)}), 401
        session["user_id"] = uid
        audit.log(conn, uid, "login")
        return jsonify({"user_id": uid,
                        "permissions": sorted(pm.user_permissions(conn, uid))})

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify({"ok": True})

    @app.get("/api/me")
    @require()
    def me(uid):
        return jsonify({"user_id": uid,
                        "permissions": sorted(pm.user_permissions(conn, uid))})

    @app.get("/api/clients")
    @require()
    def list_clients(uid):
        return jsonify(clients.visible_clients(conn, uid))

    @app.post("/api/clients")
    @require("clienti.creare")
    def add_client(uid):
        data = request.get_json(force=True)
        try:
            cid = clients.create_client(conn, data["cui"], data["name"])
        except clients.ClientError as e:
            return jsonify({"error": str(e)}), 400
        audit.log(conn, uid, "client.creare", "client", str(cid))
        return jsonify({"id": cid})

    @app.delete("/api/clients/<int:cid>")
    @require("clienti.stergere")
    def del_client(uid, cid):
        clients.delete_client(conn, cid)
        audit.log(conn, uid, "client.stergere", "client", str(cid))
        return jsonify({"ok": True})

    def _save_upload(f):
        path = os.path.join(upload_dir, secrets.token_hex(8) + "_" + f.filename)
        f.save(path)
        return path

    def _persist(uid, client_id, period, comp_rows, anaf_rows):
        cur = conn.execute(
            "INSERT INTO reconciliations(client_id, period, created_at, "
            "created_by) VALUES(?,?,?,?)",
            (client_id, period,
             datetime.now(timezone.utc).isoformat(), uid))
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
    def new_reconciliation(uid):
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
        rid = _persist(uid, client_id, period, comp_rows, anaf_rows)
        audit.log(conn, uid, "reconciliere.creare", "reconciliation", str(rid))
        return jsonify(_result_payload(rid, comp_rows, anaf_rows))

    def _load_rows(rid, table):
        rows = conn.execute(
            f"SELECT partner_cui, invoice_no, date, base, vat, category "
            f"FROM {table} WHERE reconciliation_id=?", (rid,))
        return [dict(r) for r in rows]

    @app.get("/api/reconciliations/<int:rid>")
    @require()
    def get_reconciliation(uid, rid):
        comp = _load_rows(rid, "invoices_company")
        anaf = _load_rows(rid, "invoices_anaf")
        return jsonify(_result_payload(rid, comp, anaf))

    @app.get("/api/reconciliations/<int:rid>/export")
    @require("rapoarte.export")
    def export_report(uid, rid):
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
        audit.log(conn, uid, "raport.export", "reconciliation", str(rid))
        return send_file(path, as_attachment=True,
                         download_name=f"raport_{rid}.xlsx")

    @app.get("/api/audit")
    @require("audit.vizualizare")
    def audit_view(uid):
        return jsonify(audit.entries(conn))

    @app.get("/api/admin/users")
    @require("useri.gestionare")
    def list_users(uid):
        rows = conn.execute(
            "SELECT u.id, u.username, u.active, "
            "GROUP_CONCAT(r.name) AS roles FROM users u "
            "LEFT JOIN user_roles ur ON ur.user_id=u.id "
            "LEFT JOIN roles r ON r.id=ur.role_id GROUP BY u.id")
        return jsonify([dict(r) for r in rows])

    @app.post("/api/admin/users")
    @require("useri.gestionare")
    def add_user(uid):
        data = request.get_json(force=True)
        try:
            new_id = auth.create_user(conn, data["username"], data["password"])
        except auth.AuthError as e:
            return jsonify({"error": str(e)}), 400
        if data.get("role"):
            pm.assign_role(conn, new_id, data["role"])
        audit.log(conn, uid, "user.creare", "user", str(new_id))
        return jsonify({"id": new_id})

    @app.post("/api/admin/users/<int:target>/deactivate")
    @require("useri.gestionare")
    def deactivate_user(uid, target):
        auth.set_active(conn, target, False)
        audit.log(conn, uid, "user.dezactivare", "user", str(target))
        return jsonify({"ok": True})

    @app.post("/api/admin/roles")
    @require("useri.gestionare")
    def add_role(uid):
        data = request.get_json(force=True)
        try:
            rid = pm.create_role(conn, data["name"], data["permissions"])
        except pm.PermError as e:
            return jsonify({"error": str(e)}), 400
        audit.log(conn, uid, "rol.creare", "role", str(rid))
        return jsonify({"id": rid})

    @app.put("/api/admin/roles/<name>")
    @require("useri.gestionare")
    def edit_role(uid, name):
        data = request.get_json(force=True)
        try:
            pm.update_role(conn, name, data["permissions"])
        except pm.PermError as e:
            return jsonify({"error": str(e)}), 400
        audit.log(conn, uid, "rol.editare", "role", name)
        return jsonify({"ok": True})

    @app.post("/api/admin/assign")
    @require("useri.gestionare")
    def assign_client(uid):
        data = request.get_json(force=True)
        clients.assign(conn, data["user_id"], data["client_id"])
        audit.log(conn, uid, "client.alocare", "client",
                  str(data["client_id"]))
        return jsonify({"ok": True})

    return app
