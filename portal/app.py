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
from portal import pipeline
from etva import db as fdb
from etva import audit, clients
from etva import anaf_cui
from etva import export as export_mod
from etva.importer.company import parse_company_journal, ImportError_
from etva.importer.anaf import FileAnafDataSource
from etva.importer.saga import parse_saga_journal, NotSagaFormat
from etva.importer.anaf_p300 import parse_p300_pdf, NotAnafP300
from etva.d300 import classify_legend, expand_derived_lines
from etva.engine import reconcile, reconcile_d300
from etva.advisor import suggest_d300, suggest_d300_lines

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_LANDING = _ROOT / "docs" / "index.html"
_FAVICON = _ROOT / "docs" / "favicon.svg"
_GHID = _ROOT / "docs" / "ghid.html"
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

    def list_user_firms(user_id: int):
        """Active firms this user belongs to, each with their role there."""
        return conn.execute(
            "SELECT f.id, f.name, f.cui, uf.role FROM user_firms uf "
            "JOIN firms f ON f.id = uf.firm_id "
            "WHERE uf.user_id=? AND uf.active=1 AND f.active=1 "
            "ORDER BY f.name", (user_id,)).fetchall()

    def current_identity():
        """Active-firm identity for the product API; None for anonymous/master."""
        user = current_user()
        if user is None or user["is_master"]:
            return None
        active_firm_id = session.get("active_firm_id")
        if active_firm_id is None:
            return None
        row = conn.execute(
            "SELECT uf.role, f.id, f.name FROM user_firms uf "
            "JOIN firms f ON f.id = uf.firm_id "
            "WHERE uf.user_id=? AND uf.firm_id=? AND uf.active=1 AND f.active=1",
            (user["id"], active_firm_id)).fetchone()
        if row is None:
            return None
        return {"username": user["username"], "role": row["role"],
                "firm_id": row["id"], "firm_name": row["name"],
                "permissions": pdb.ROLE_PERMISSIONS[row["role"]]}

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

    @app.get("/ghid.html")
    def ghid():
        return send_file(_GHID)

    def _verify_cui_or_error(cui: str) -> str | None:
        """Return an error message if the CUI isn't a real, ANAF-registered
        CUI, or None if it checks out."""
        try:
            info = anaf_cui.verify_cui(cui)
        except ValueError:
            return "CUI-ul introdus nu este valid."
        except anaf_cui.AnafCuiError:
            return ("Nu am putut verifica CUI-ul la ANAF chiar acum. "
                    "Incearca din nou peste cateva momente.")
        if info is None:
            return ("CUI-ul introdus nu a fost gasit la ANAF. "
                    "Verifica-l si incearca din nou.")
        return None

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
        eroare = _verify_cui_or_error(cui)
        if eroare:
            return render_template("inregistrare.html", eroare=eroare)
        cur = conn.execute("INSERT INTO firms(name, cui) VALUES(?,?)", (name, cui))
        firm_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO users(username, pw_hash) VALUES(?,?)",
            (username, psec.hash_password(password)))
        user_id = cur.lastrowid
        conn.execute(
            "INSERT INTO user_firms(user_id, firm_id, role, active) "
            "VALUES(?,?,?,1)", (user_id, firm_id, "admin"))
        conn.execute("INSERT INTO firm_keys(firm_id, wrapped_key) VALUES(?,?)",
                     (firm_id, psec.wrap_key(secret, os.urandom(32))))
        conn.commit()
        session["user_id"] = user_id
        session["active_firm_id"] = firm_id
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
        if row["is_master"]:
            return redirect(url_for("master"))
        firms = list_user_firms(row["id"])
        if not firms:
            session["active_firm_id"] = None
            return redirect(url_for("panou"))
        session["active_firm_id"] = firms[0]["id"]
        ident = current_identity()
        if ident is None:
            session.clear()
            return render_template("autentificare.html",
                                   eroare="Contul firmei este dezactivat.")
        audit.log(firm_conn(ident["firm_id"]), ident["username"], "login")
        if len(firms) > 1:
            return redirect(url_for("panou"))
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
    def _role_in_firm(user_id: int, firm_id: int) -> str | None:
        row = conn.execute(
            "SELECT role FROM user_firms WHERE user_id=? AND firm_id=? "
            "AND active=1", (user_id, firm_id)).fetchone()
        return row["role"] if row else None

    @app.get("/panou")
    def panou():
        user = current_user()
        if user is None or user["is_master"]:
            return redirect(url_for("login"))
        firms = list_user_firms(user["id"])
        active_firm_id = session.get("active_firm_id")
        active = next((f for f in firms if f["id"] == active_firm_id), None)
        if active is None and firms:
            active = firms[0]
            session["active_firm_id"] = active["id"]
        members = []
        if active is not None:
            members = conn.execute(
                "SELECT u.username, uf.role, uf.active FROM user_firms uf "
                "JOIN users u ON u.id = uf.user_id "
                "WHERE uf.firm_id=? ORDER BY uf.role, u.username",
                (active["id"],)).fetchall()
        return render_template("panou.html", user=user, firms=firms,
                               active=active, members=members,
                               subroles=FIRM_SUBROLES,
                               eroare=request.args.get("eroare"))

    @app.post("/panou/firme")
    def add_firm():
        user = current_user()
        if user is None or user["is_master"]:
            return redirect(url_for("login"))
        name = request.form.get("name", "").strip()
        cui = request.form.get("cui", "").strip()
        if not name or not cui:
            return redirect(url_for(
                "panou", eroare="Denumirea si CUI-ul sunt obligatorii."))
        if conn.execute("SELECT 1 FROM firms WHERE cui=?", (cui,)).fetchone():
            return redirect(url_for(
                "panou", eroare="Exista deja o firma cu acest CUI."))
        eroare = _verify_cui_or_error(cui)
        if eroare:
            return redirect(url_for("panou", eroare=eroare))
        cur = conn.execute("INSERT INTO firms(name, cui) VALUES(?,?)", (name, cui))
        firm_id = cur.lastrowid
        conn.execute(
            "INSERT INTO user_firms(user_id, firm_id, role, active) "
            "VALUES(?,?,?,1)", (user["id"], firm_id, "admin"))
        conn.execute("INSERT INTO firm_keys(firm_id, wrapped_key) VALUES(?,?)",
                     (firm_id, psec.wrap_key(secret, os.urandom(32))))
        conn.commit()
        session["active_firm_id"] = firm_id
        return redirect(url_for("panou"))

    @app.post("/panou/comutare-firma")
    def switch_firm():
        user = current_user()
        if user is None or user["is_master"]:
            return redirect(url_for("login"))
        firm_id = request.form.get("firm_id", type=int)
        if firm_id is not None and _role_in_firm(user["id"], firm_id):
            session["active_firm_id"] = firm_id
        return redirect(url_for("panou"))

    @app.post("/panou/utilizatori")
    def add_member():
        user = current_user()
        active_firm_id = session.get("active_firm_id")
        if (user is None or user["is_master"] or active_firm_id is None
                or _role_in_firm(user["id"], active_firm_id) != "admin"):
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
        cur = conn.execute(
            "INSERT INTO users(username, pw_hash) VALUES(?,?)",
            (username, psec.hash_password(password)))
        conn.execute(
            "INSERT INTO user_firms(user_id, firm_id, role, active) "
            "VALUES(?,?,?,1)", (cur.lastrowid, active_firm_id, role))
        conn.commit()
        return redirect(url_for("panou"))

    @app.post("/panou/utilizatori/<username>/dezactivare")
    def deactivate_member(username):
        user = current_user()
        active_firm_id = session.get("active_firm_id")
        if (user is None or user["is_master"] or active_firm_id is None
                or _role_in_firm(user["id"], active_firm_id) != "admin"):
            return redirect(url_for("login"))
        target = conn.execute("SELECT id FROM users WHERE username=?",
                              (username,)).fetchone()
        if target:
            conn.execute(
                "UPDATE user_firms SET active=0 WHERE user_id=? AND firm_id=? "
                "AND role!='admin'", (target["id"], active_firm_id))
            conn.commit()
        return redirect(url_for("panou"))

    # ---------- master ----------
    @app.get("/master")
    def master():
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        firms = conn.execute(
            "SELECT f.*, (SELECT COUNT(*) FROM user_firms uf "
            "WHERE uf.firm_id=f.id AND uf.active=1) AS n_users "
            "FROM firms f ORDER BY f.name").fetchall()
        return render_template("master.html", user=user, firms=firms)

    @app.post("/master/firma/<int:firm_id>/comutare")
    def toggle_firm(firm_id):
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        conn.execute("UPDATE firms SET active = 1 - active WHERE id=?", (firm_id,))
        conn.commit()
        return redirect(url_for("master"))

    # ---------- master: dev/testare/productie pipeline ----------
    @app.get("/master/pipeline")
    def pipeline_dashboard():
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        envs = {env: pipeline.branch_info(env) for env in pipeline.ENVIRONMENTS}
        promotions = []
        for source, target in pipeline.PROMOTIONS:
            info = {"source": source, "target": target}
            try:
                info["ahead"] = pipeline.ahead_count(source, target)
                info["can_promote"] = (info["ahead"] > 0
                                       and pipeline.can_promote(source, target))
                info["blocked"] = None
            except pipeline.PipelineError as e:
                info["ahead"] = None
                info["can_promote"] = False
                info["blocked"] = str(e)
            promotions.append(info)
        return render_template(
            "pipeline.html", user=user, envs=envs, labels=pipeline.ENVIRONMENTS,
            promotions=promotions, istoric=pipeline.history(conn),
            eroare=request.args.get("eroare"), mesaj=request.args.get("mesaj"))

    @app.post("/master/pipeline/promoveaza")
    def promote_environment():
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        source = request.form.get("source", "")
        target = request.form.get("target", "")
        try:
            commit = pipeline.promote(source, target)
        except pipeline.PipelineError as e:
            return redirect(url_for("pipeline_dashboard", eroare=str(e)))
        pipeline.log_promotion(conn, source, target, commit, user["username"])
        return redirect(url_for(
            "pipeline_dashboard",
            mesaj=f"{source} -> {target} promovat la commit-ul {commit}. "
                 f"Reporneste manual serverul din '{target}'."))

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
        return {"id": rid, "mode": "invoices",
                "totals_company": result.totals_company,
                "totals_anaf": result.totals_anaf,
                "differences": result.differences,
                "suggestions": suggest_d300(result)}

    def _persist_lines(fc, username, client_id, period, company_lines, anaf_lines):
        cur = fc.execute(
            "INSERT INTO reconciliations(client_id, period, created_at, "
            "created_by) VALUES(?,?,?,?)",
            (client_id, period,
             datetime.now(timezone.utc).isoformat(), username))
        rid = cur.lastrowid
        for table, lines in (("invoices_company", company_lines),
                            ("invoices_anaf", anaf_lines)):
            fc.executemany(
                f"INSERT INTO {table}(reconciliation_id, category, base, vat) "
                "VALUES(?,?,?,?)",
                [(rid, line_no, v["base"], v["vat"]) for line_no, v in lines.items()])
        fc.commit()
        return rid

    def _result_payload_lines(fc, rid, company_lines, anaf_lines, unmapped=None):
        result = reconcile_d300(company_lines, anaf_lines)
        fc.execute("DELETE FROM differences WHERE reconciliation_id=?", (rid,))
        fc.executemany(
            "INSERT INTO differences(reconciliation_id, diff_type, details) "
            "VALUES(?,?,?)",
            [(rid, d["diff_type"], json.dumps(d)) for d in result.differences])
        fc.commit()
        payload = {"id": rid, "mode": "d300_lines",
                   "totals_company": result.totals_company,
                   "totals_anaf": result.totals_anaf,
                   "differences": result.differences,
                   "suggestions": suggest_d300_lines(result)}
        if unmapped:
            payload["unmapped"] = unmapped
        return payload

    @app.post("/api/reconciliations")
    @require("reconciliere.creare")
    def new_reconciliation(ident):
        fc = firm_conn(ident["firm_id"])
        client_id = int(request.form["client_id"])
        period = request.form["period"]
        anaf_file = request.files["anaf_file"]
        company_files = request.files.getlist("company_file")
        if not company_files:
            return jsonify({"errors": ["Lipseste jurnalul firmei."]}), 400

        if anaf_file.filename.lower().endswith(".pdf"):
            try:
                anaf_doc = parse_p300_pdf(_save_upload(anaf_file))
            except NotAnafP300 as e:
                return jsonify({"errors": [str(e)]}), 400

            cod_mapping = None
            if request.form.get("cod_mapping"):
                cod_mapping = json.loads(request.form["cod_mapping"])

            company_lines: dict = {}
            unmapped = []
            try:
                for f in company_files:
                    journal = parse_saga_journal(_save_upload(f))
                    mapped, unmapped_here = classify_legend(
                        journal.direction, journal.legend, cod_mapping)
                    unmapped.extend(unmapped_here)
                    for line_no, v in mapped.items():
                        acc = company_lines.setdefault(
                            line_no, {"base": 0.0, "vat": 0.0})
                        acc["base"] += v["base"]
                        acc["vat"] += v["vat"]
            except NotSagaFormat as e:
                return jsonify({"errors": [str(e)]}), 400
            company_lines = expand_derived_lines(company_lines)

            rid = _persist_lines(fc, ident["username"], client_id, period,
                                 company_lines, anaf_doc.lines)
            audit.log(fc, ident["username"], "reconciliere.creare",
                      "reconciliation", str(rid))
            return jsonify(_result_payload_lines(
                fc, rid, company_lines, anaf_doc.lines, unmapped))

        mapping = None
        if request.form.get("anaf_mapping"):
            mapping = json.loads(request.form["anaf_mapping"])
        try:
            comp_rows = parse_company_journal(_save_upload(company_files[0]))
            anaf_rows = FileAnafDataSource(
                _save_upload(anaf_file), mapping).get_etva_data("", period)
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
            f"FROM {table} WHERE reconciliation_id=? AND partner_cui IS NOT NULL",
            (rid,))
        return [dict(r) for r in rows]

    def _load_lines(fc, rid, table):
        rows = fc.execute(
            f"SELECT category, base, vat FROM {table} "
            f"WHERE reconciliation_id=? AND partner_cui IS NULL", (rid,))
        return {r["category"]: {"base": r["base"], "vat": r["vat"]} for r in rows}

    def _reconciliation_mode(fc, rid):
        row = fc.execute(
            "SELECT partner_cui FROM invoices_anaf WHERE reconciliation_id=? "
            "LIMIT 1", (rid,)).fetchone()
        if row is None:
            return "invoices"
        return "invoices" if row["partner_cui"] is not None else "d300_lines"

    @app.get("/api/reconciliations/<int:rid>")
    @require()
    def get_reconciliation(ident, rid):
        fc = firm_conn(ident["firm_id"])
        if _reconciliation_mode(fc, rid) == "d300_lines":
            comp = _load_lines(fc, rid, "invoices_company")
            anaf = _load_lines(fc, rid, "invoices_anaf")
            return jsonify(_result_payload_lines(fc, rid, comp, anaf))
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
        path = os.path.join(upload_dir, f"raport_{ident['firm_id']}_{rid}.xlsx")
        if _reconciliation_mode(fc, rid) == "d300_lines":
            comp = _load_lines(fc, rid, "invoices_company")
            anaf = _load_lines(fc, rid, "invoices_anaf")
            result = reconcile_d300(comp, anaf)
            export_mod.write_report_lines(result, suggest_d300_lines(result),
                                          path, row["name"], row["period"])
        else:
            comp = _load_rows(fc, rid, "invoices_company")
            anaf = _load_rows(fc, rid, "invoices_anaf")
            result = reconcile(comp, anaf)
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
