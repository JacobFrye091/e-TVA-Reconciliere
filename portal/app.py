"""e-TVA Reconciliere — the web platform.

One Flask app: public landing + firm accounts + the full reconciliation
product served in the browser. Each firm's working data lives in its own
SQLCipher-encrypted database on the server, opened with the firm's data key.
"""
import json, os, pathlib, secrets
from datetime import datetime, timezone
from functools import wraps

from flask import (Flask, request, session, redirect, url_for, jsonify,
                   render_template, send_file)

from portal import db as pdb
from portal import security as psec
from etva import db as fdb
from etva import audit, clients
from etva import export as export_mod
from etva.importer.company import parse_company_journal, ImportError_
from etva.importer.anaf import FileAnafDataSource
from etva.engine import reconcile
from etva.advisor import suggest_d300

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_LANDING = _ROOT / "docs" / "index.html"
_FAVICON = _ROOT / "docs" / "favicon.svg"
_SPA = _ROOT / "web" / "index.html"

FIRM_SUBROLES = ["manager", "contabil", "junior"]


def create_app(data_dir: str) -> Flask:
    os.makedirs(data_dir, exist_ok=True)
    firms_dir = os.path.join(data_dir, "firms")
    upload_dir = os.path.join(data_dir, "uploads")
    os.makedirs(firms_dir, exist_ok=True)
    os.makedirs(upload_dir, exist_ok=True)
    conn = pdb.open_db(os.path.join(data_dir, "portal.db"))
    secret = psec.load_secret(os.path.join(data_dir, "secret.key"))

    app = Flask(__name__)
    app.secret_key = secrets.token_hex(32)

    firm_conns = {}

    def firm_conn(firm_id: int):
        if firm_id not in firm_conns:
            wrapped = conn.execute(
                "SELECT wrapped_key FROM firm_keys WHERE firm_id=?",
                (firm_id,)).fetchone()["wrapped_key"]
            key = psec.unwrap_key(secret, wrapped)
            fc = fdb.open_db(os.path.join(firms_dir, f"firm_{firm_id}.db"), key)
            fdb.init_schema(fc)
            firm_conns[firm_id] = fc
        return firm_conns[firm_id]

    def current_user():
        uid = session.get("user_id")
        if uid is None:
            return None
        return conn.execute("SELECT * FROM users WHERE id=? AND active=1",
                            (uid,)).fetchone()

    def current_identity():
        """Firm identity for the product API; None for anonymous/master."""
        user = current_user()
        if user is None or user["role"] == "master":
            return None
        firm = conn.execute("SELECT * FROM firms WHERE id=? AND active=1",
                            (user["firm_id"],)).fetchone()
        if firm is None:
            return None
        return {"username": user["username"], "role": user["role"],
                "firm_id": firm["id"], "firm_name": firm["name"],
                "permissions": pdb.ROLE_PERMISSIONS[user["role"]]}

    def require(perm=None):
        def deco(fn):
            @wraps(fn)
            def wrapper(*a, **kw):
                ident = current_identity()
                if ident is None:
                    return jsonify({"error": "Neautentificat"}), 401
                if perm and perm not in ident["permissions"]:
                    return jsonify({"error": "Acces interzis"}), 403
                return fn(ident, *a, **kw)
            return wrapper
        return deco

    # ---------- public pages ----------
    @app.get("/")
    def landing():
        return send_file(_LANDING)

    @app.get("/favicon.svg")
    def favicon():
        return send_file(_FAVICON, mimetype="image/svg+xml")

    @app.route("/inregistrare", methods=["GET", "POST"])
    def register():
        if request.method == "GET":
            return render_template("inregistrare.html", eroare=None)
        f = request.form
        name, cui = f.get("name", "").strip(), f.get("cui", "").strip()
        username, password = f.get("username", "").strip(), f.get("password", "")
        if not all([name, cui, username, password]):
            return render_template("inregistrare.html",
                                   eroare="Toate campurile sunt obligatorii.")
        if len(password) < 10:
            return render_template("inregistrare.html",
                                   eroare="Parola trebuie sa aiba minim 10 caractere.")
        if conn.execute("SELECT 1 FROM firms WHERE cui=?", (cui,)).fetchone():
            return render_template("inregistrare.html",
                                   eroare="Exista deja o firma cu acest CUI.")
        if conn.execute("SELECT 1 FROM users WHERE username=?",
                        (username,)).fetchone():
            return render_template("inregistrare.html",
                                   eroare="Numele de utilizator este deja folosit.")
        cur = conn.execute("INSERT INTO firms(name, cui) VALUES(?,?)", (name, cui))
        firm_id = cur.lastrowid
        conn.execute(
            "INSERT INTO users(firm_id, username, pw_hash, role) VALUES(?,?,?,?)",
            (firm_id, username, psec.hash_password(password), "admin"))
        conn.execute("INSERT INTO firm_keys(firm_id, wrapped_key) VALUES(?,?)",
                     (firm_id, psec.wrap_key(secret, os.urandom(32))))
        conn.commit()
        session["user_id"] = conn.execute(
            "SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
        return redirect(url_for("aplicatie"))

    @app.route("/autentificare", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            return render_template("autentificare.html", eroare=None)
        row = conn.execute("SELECT * FROM users WHERE username=?",
                           (request.form.get("username", "").strip(),)).fetchone()
        if (row is None or not row["active"]
                or not psec.verify_password(row["pw_hash"],
                                            request.form.get("password", ""))):
            return render_template("autentificare.html",
                                   eroare="Utilizator sau parola incorecta.")
        session["user_id"] = row["id"]
        if row["role"] == "master":
            return redirect(url_for("master"))
        ident = current_identity()
        if ident is None:
            session.clear()
            return render_template("autentificare.html",
                                   eroare="Contul firmei este dezactivat.")
        audit.log(firm_conn(ident["firm_id"]), ident["username"], "login")
        return redirect(url_for("aplicatie"))

    @app.get("/iesire")
    def logout_page():
        session.clear()
        return redirect(url_for("landing"))

    # ---------- the product (SPA) ----------
    @app.get("/app")
    def aplicatie():
        if current_identity() is None:
            return redirect(url_for("login"))
        return send_file(_SPA)

    # ---------- firm account pages ----------
    @app.get("/panou")
    def panou():
        user = current_user()
        if user is None or user["role"] == "master":
            return redirect(url_for("login"))
        firm = conn.execute("SELECT * FROM firms WHERE id=?",
                            (user["firm_id"],)).fetchone()
        members = conn.execute(
            "SELECT username, role, active FROM users WHERE firm_id=? "
            "ORDER BY role, username", (user["firm_id"],)).fetchall()
        return render_template("panou.html", user=user, firm=firm,
                               members=members, subroles=FIRM_SUBROLES,
                               eroare=request.args.get("eroare"))

    @app.post("/panou/utilizatori")
    def add_member():
        user = current_user()
        if user is None or user["role"] != "admin":
            return redirect(url_for("login"))
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "")
        if role not in FIRM_SUBROLES or not username or len(password) < 10:
            return redirect(url_for(
                "panou", eroare="Date invalide (parola minim 10 caractere)."))
        if conn.execute("SELECT 1 FROM users WHERE username=?",
                        (username,)).fetchone():
            return redirect(url_for(
                "panou", eroare="Numele de utilizator este deja folosit."))
        conn.execute(
            "INSERT INTO users(firm_id, username, pw_hash, role) VALUES(?,?,?,?)",
            (user["firm_id"], username, psec.hash_password(password), role))
        conn.commit()
        return redirect(url_for("panou"))

    @app.post("/panou/utilizatori/<username>/dezactivare")
    def deactivate_member(username):
        user = current_user()
        if user is None or user["role"] != "admin":
            return redirect(url_for("login"))
        conn.execute(
            "UPDATE users SET active=0 WHERE username=? AND firm_id=? AND role!='admin'",
            (username, user["firm_id"]))
        conn.commit()
        return redirect(url_for("panou"))

    # ---------- master ----------
    @app.get("/master")
    def master():
        user = current_user()
        if user is None or user["role"] != "master":
            return redirect(url_for("login"))
        firms = conn.execute(
            "SELECT f.*, (SELECT COUNT(*) FROM users u WHERE u.firm_id=f.id) "
            "AS n_users FROM firms f ORDER BY f.name").fetchall()
        return render_template("master.html", user=user, firms=firms)

    @app.post("/master/firma/<int:firm_id>/comutare")
    def toggle_firm(firm_id):
        user = current_user()
        if user is None or user["role"] != "master":
            return redirect(url_for("login"))
        conn.execute("UPDATE firms SET active = 1 - active WHERE id=?", (firm_id,))
        conn.commit()
        return redirect(url_for("master"))

    # ---------- product API (session-based) ----------
    @app.get("/api/me")
    @require()
    def me(ident):
        return jsonify({"username": ident["username"], "role": ident["role"],
                        "firm_name": ident["firm_name"],
                        "permissions": sorted(ident["permissions"])})

    @app.post("/api/logout")
    @require()
    def logout_api(ident):
        audit.log(firm_conn(ident["firm_id"]), ident["username"], "logout")
        session.clear()
        return jsonify({"ok": True})

    @app.get("/api/clients")
    @require()
    def list_clients(ident):
        return jsonify(clients.visible_clients(firm_conn(ident["firm_id"]),
                                               ident))

    @app.post("/api/clients")
    @require("clienti.creare")
    def add_client(ident):
        fc = firm_conn(ident["firm_id"])
        data = request.get_json(force=True)
        try:
            cid = clients.create_client(fc, data["cui"], data["name"])
        except clients.ClientError as e:
            return jsonify({"error": str(e)}), 400
        audit.log(fc, ident["username"], "client.creare", "client", str(cid))
        return jsonify({"id": cid})

    @app.delete("/api/clients/<int:cid>")
    @require("clienti.stergere")
    def del_client(ident, cid):
        fc = firm_conn(ident["firm_id"])
        clients.delete_client(fc, cid)
        audit.log(fc, ident["username"], "client.stergere", "client", str(cid))
        return jsonify({"ok": True})

    @app.post("/api/assignments")
    @require("useri.gestionare")
    def assign_client(ident):
        fc = firm_conn(ident["firm_id"])
        data = request.get_json(force=True)
        clients.assign(fc, data["username"].strip(), int(data["client_id"]))
        audit.log(fc, ident["username"], "client.alocare", "client",
                  str(data["client_id"]))
        return jsonify({"ok": True})

    def _save_upload(f):
        path = os.path.join(upload_dir, secrets.token_hex(8) + "_" + f.filename)
        f.save(path)
        return path

    def _persist(fc, username, client_id, period, comp_rows, anaf_rows):
        cur = fc.execute(
            "INSERT INTO reconciliations(client_id, period, created_at, "
            "created_by) VALUES(?,?,?,?)",
            (client_id, period,
             datetime.now(timezone.utc).isoformat(), username))
        rid = cur.lastrowid
        for table, rows in (("invoices_company", comp_rows),
                            ("invoices_anaf", anaf_rows)):
            fc.executemany(
                f"INSERT INTO {table}(reconciliation_id, partner_cui, "
                "invoice_no, date, base, vat, category) VALUES(?,?,?,?,?,?,?)",
                [(rid, r["partner_cui"], r["invoice_no"], r["date"],
                  r["base"], r["vat"], r["category"]) for r in rows])
        fc.commit()
        return rid

    def _result_payload(fc, rid, comp_rows, anaf_rows):
        result = reconcile(comp_rows, anaf_rows)
        fc.execute("DELETE FROM differences WHERE reconciliation_id=?", (rid,))
        fc.executemany(
            "INSERT INTO differences(reconciliation_id, diff_type, details) "
            "VALUES(?,?,?)",
            [(rid, d["diff_type"], json.dumps(d)) for d in result.differences])
        fc.commit()
        return {"id": rid,
                "totals_company": result.totals_company,
                "totals_anaf": result.totals_anaf,
                "differences": result.differences,
                "suggestions": suggest_d300(result)}

    @app.post("/api/reconciliations")
    @require("reconciliere.creare")
    def new_reconciliation(ident):
        fc = firm_conn(ident["firm_id"])
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
        rid = _persist(fc, ident["username"], client_id, period,
                       comp_rows, anaf_rows)
        audit.log(fc, ident["username"], "reconciliere.creare",
                  "reconciliation", str(rid))
        return jsonify(_result_payload(fc, rid, comp_rows, anaf_rows))

    def _load_rows(fc, rid, table):
        rows = fc.execute(
            f"SELECT partner_cui, invoice_no, date, base, vat, category "
            f"FROM {table} WHERE reconciliation_id=?", (rid,))
        return [dict(r) for r in rows]

    @app.get("/api/reconciliations/<int:rid>")
    @require()
    def get_reconciliation(ident, rid):
        fc = firm_conn(ident["firm_id"])
        comp = _load_rows(fc, rid, "invoices_company")
        anaf = _load_rows(fc, rid, "invoices_anaf")
        return jsonify(_result_payload(fc, rid, comp, anaf))

    @app.get("/api/reconciliations/<int:rid>/export")
    @require("rapoarte.export")
    def export_report(ident, rid):
        fc = firm_conn(ident["firm_id"])
        row = fc.execute(
            "SELECT r.period, c.name FROM reconciliations r "
            "JOIN clients c ON c.id = r.client_id WHERE r.id=?",
            (rid,)).fetchone()
        if row is None:
            return jsonify({"error": "Reconciliere inexistenta"}), 404
        comp = _load_rows(fc, rid, "invoices_company")
        anaf = _load_rows(fc, rid, "invoices_anaf")
        result = reconcile(comp, anaf)
        path = os.path.join(upload_dir, f"raport_{ident['firm_id']}_{rid}.xlsx")
        export_mod.write_report(result, suggest_d300(result), path,
                                row["name"], row["period"])
        audit.log(fc, ident["username"], "raport.export",
                  "reconciliation", str(rid))
        return send_file(path, as_attachment=True,
                         download_name=f"raport_{rid}.xlsx")

    @app.get("/api/audit")
    @require("audit.vizualizare")
    def audit_view(ident):
        return jsonify(audit.entries(firm_conn(ident["firm_id"])))

    app.portal_conn = conn  # exposed for tests/seeding
    app.portal_secret = secret
    return app
