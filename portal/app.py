"""Portal Flask app: firm registration, login, master oversight, app API."""
import os, pathlib, secrets

from flask import (Flask, request, session, redirect, url_for, jsonify,
                   render_template, send_file)

from portal import db as pdb
from portal import security as psec

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_LANDING = _ROOT / "docs" / "index.html"
_FAVICON = _ROOT / "docs" / "favicon.svg"

FIRM_SUBROLES = ["manager", "contabil", "junior"]


def create_app(data_dir: str) -> Flask:
    os.makedirs(data_dir, exist_ok=True)
    conn = pdb.open_db(os.path.join(data_dir, "portal.db"))
    secret = psec.load_secret(os.path.join(data_dir, "secret.key"))

    app = Flask(__name__)
    app.secret_key = secrets.token_hex(32)

    def current_user():
        uid = session.get("user_id")
        if uid is None:
            return None
        row = conn.execute("SELECT * FROM users WHERE id=? AND active=1",
                           (uid,)).fetchone()
        return row

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
        return redirect(url_for("panou"))

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
        return redirect(url_for("master" if row["role"] == "master" else "panou"))

    @app.get("/iesire")
    def logout():
        session.clear()
        return redirect(url_for("landing"))

    # ---------- firm dashboard ----------
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

    # ---------- master dashboard ----------
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

    # ---------- API for the desktop app ----------
    @app.post("/api/auth")
    def api_auth():
        data = request.get_json(force=True)
        row = conn.execute("SELECT * FROM users WHERE username=?",
                           (data.get("username", ""),)).fetchone()
        if (row is None or not row["active"] or row["role"] == "master"
                or not psec.verify_password(row["pw_hash"],
                                            data.get("password", ""))):
            return jsonify({"error": "Utilizator sau parola incorecta."}), 401
        firm = conn.execute("SELECT * FROM firms WHERE id=? AND active=1",
                            (row["firm_id"],)).fetchone()
        if firm is None:
            return jsonify({"error": "Contul firmei este dezactivat."}), 403
        wrapped = conn.execute(
            "SELECT wrapped_key FROM firm_keys WHERE firm_id=?",
            (firm["id"],)).fetchone()["wrapped_key"]
        return jsonify({
            "username": row["username"],
            "role": row["role"],
            "firm_id": firm["id"],
            "firm_name": firm["name"],
            "firm_cui": firm["cui"],
            "permissions": pdb.ROLE_PERMISSIONS[row["role"]],
            "data_key": psec.unwrap_key(secret, wrapped).hex(),
        })

    app.portal_conn = conn  # exposed for tests/seeding
    app.portal_secret = secret
    return app
