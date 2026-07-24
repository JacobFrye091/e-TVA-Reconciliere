"""e-TVA Reconciliere — the web platform.

One Flask app: public landing + firm accounts + the full reconciliation
product served in the browser. Each firm's working data lives in its own
SQLCipher-encrypted database on the server, opened with the firm's data key.
"""
import json, os, pathlib, re, secrets, threading
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Flask, request, session, redirect, url_for, jsonify,
                   render_template, send_file, Response)

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
from etva.importer.anaf_p300_json import parse_p300_json, NotAnafP300Json
from etva.d300 import classify_legend, expand_derived_lines
from etva.engine import reconcile, reconcile_d300
from etva.advisor import suggest_d300, suggest_d300_lines

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_LANDING = _ROOT / "docs" / "index.html"
_FAVICON = _ROOT / "docs" / "favicon.svg"
_GHID = _ROOT / "docs" / "ghid.html"
_TERMENI = _ROOT / "docs" / "termeni.html"
_CONFIDENTIALITATE = _ROOT / "docs" / "confidentialitate.html"
_COOKIE_URI = _ROOT / "docs" / "cookie-uri.html"
_SPA = _ROOT / "web" / "index.html"

FIRM_SUBROLES = ["manager", "contabil", "junior"]

_AVATAR_PALETTE = ["#0d5c63", "#12777f", "#9a6700", "#1a7f4b", "#5b4fc4", "#b0473e"]


def _avatar_color(username: str) -> str:
    return _AVATAR_PALETTE[sum(map(ord, username)) % len(_AVATAR_PALETTE)]


def _bar_pct(value: int, maximum: int) -> int:
    return round(100 * value / maximum) if maximum else 0


def _donut_segments(counts: list[tuple[str, int]]) -> list[dict]:
    """SVG donut segments for a circle with r=15.9155 (circumference == 100),
    so each segment's share of the total doubles as its stroke-dasharray
    length - no separate angle math needed."""
    total = sum(n for _, n in counts)
    segments = []
    offset = 0.0
    for label, n in counts:
        pct = (n / total * 100) if total else 0.0
        segments.append({"label": label, "n": n, "pct": round(pct),
                         "dasharray": f"{pct:.3f} {100 - pct:.3f}",
                         "dashoffset": f"{25 - offset:.3f}"})
        offset += pct
    return segments


def create_app(data_dir: str) -> Flask:
    os.makedirs(data_dir, exist_ok=True)
    firms_dir = os.path.join(data_dir, "firms")
    upload_dir = os.path.join(data_dir, "uploads")
    os.makedirs(firms_dir, exist_ok=True)
    os.makedirs(upload_dir, exist_ok=True)
    conn = pdb.open_db(os.path.join(data_dir, "portal.db"))
    secret = psec.load_secret(os.path.join(data_dir, "secret.key"))

    app = Flask(__name__)
    # Persisted (not regenerated per process) so a login survives a server
    # restart: session cookies are signed with this key, so a fresh random
    # key on every start would silently invalidate every open session.
    app.secret_key = psec.load_secret(os.path.join(data_dir, "flask_secret.key"))
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=365)

    firm_conns = {}

    # portal.db and every firm_conns[...] are single sqlite3/sqlcipher
    # connections opened once and reused across requests (see portal/db.py,
    # etva/db.py). check_same_thread=False only lifts sqlite3's same-thread
    # assertion - it does not make concurrent statement execution on one
    # connection safe. Interleaved multi-statement writes from two threads
    # (e.g. two /inregistrare calls) have produced orphaned rows and
    # lastrowid races in practice, so every request is serialized around
    # its DB work.
    db_lock = threading.RLock()

    @app.before_request
    def _acquire_db_lock():
        db_lock.acquire()

    @app.teardown_request
    def _release_db_lock(exc=None):
        db_lock.release()

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
            "SELECT f.id, f.name, f.cui, f.tip, uf.role FROM user_firms uf "
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
            "SELECT uf.role, f.id, f.name, f.tip FROM user_firms uf "
            "JOIN firms f ON f.id = uf.firm_id "
            "WHERE uf.user_id=? AND uf.firm_id=? AND uf.active=1 AND f.active=1",
            (user["id"], active_firm_id)).fetchone()
        if row is None:
            return None
        return {"username": user["username"], "role": row["role"],
                "firm_id": row["id"], "firm_name": row["name"],
                "firm_tip": row["tip"],
                "onboarding_completat": bool(user["onboarding_completat"]),
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

    @app.get("/termeni.html")
    def termeni():
        return send_file(_TERMENI)

    @app.get("/confidentialitate.html")
    def confidentialitate():
        return send_file(_CONFIDENTIALITATE)

    @app.get("/cookie-uri.html")
    def cookie_uri():
        return send_file(_COOKIE_URI)

    def _anaf_lookup(cui: str) -> tuple[dict | None, str | None]:
        """Look up a CUI at ANAF. Returns (info, None) on success, or
        (None, mesaj_de_eroare) if the CUI is invalid, unknown, or the
        service can't be reached right now."""
        try:
            info = anaf_cui.verify_cui(cui)
        except ValueError:
            return None, "CUI-ul introdus nu este valid."
        except anaf_cui.AnafCuiError:
            return None, ("Nu am putut verifica CUI-ul la ANAF chiar acum. "
                          "Incearca din nou peste cateva momente.")
        if info is None:
            return None, ("CUI-ul introdus nu a fost gasit la ANAF. "
                          "Verifica-l si incearca din nou.")
        return info, None

    def _verify_cui_or_error(cui: str) -> str | None:
        """Return an error message if the CUI isn't a real, ANAF-registered
        CUI, or None if it checks out."""
        return _anaf_lookup(cui)[1]

    @app.get("/api/anaf/denumire")
    def anaf_denumire():
        """Used by the registration/add-firm forms' 'Cod CUI Completat'
        checkbox to auto-fill the (readonly) firm-name field from ANAF."""
        cui = request.args.get("cui", "").strip()
        if not cui:
            return jsonify({"denumire": None, "eroare": "Introdu un CUI."})
        info, eroare = _anaf_lookup(cui)
        if eroare:
            return jsonify({"denumire": None, "eroare": eroare})
        return jsonify({"denumire": info["denumire"], "eroare": None})

    def _slugify(text: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
        return slug or "firma"

    def _unique_username(desired: str) -> str:
        """Real people share first names/surnames often enough that a
        collision shouldn't block signup - append the next free number
        onto the requested name instead of rejecting it outright."""
        if not conn.execute("SELECT 1 FROM users WHERE username=?",
                            (desired,)).fetchone():
            return desired
        n = 2
        while conn.execute("SELECT 1 FROM users WHERE username=?",
                           (f"{desired}{n}",)).fetchone():
            n += 1
        return f"{desired}{n}"

    def _create_firm(name: str, cui: str, tip: str, user_id: int, role: str) -> int:
        """Create a firm and link it to user_id with the given role. A
        self-reconciling ('direct') firm has no clients at all - it
        reconciles as itself, not as its own client - so nothing further
        happens here for that case; only a 'contabilitate' firm ever
        gets real clients, added by hand afterwards."""
        if tip not in pdb.FIRM_TIPURI:
            tip = pdb.FIRM_TIP_CONTABILITATE
        cur = conn.execute("INSERT INTO firms(name, cui, tip) VALUES(?,?,?)",
                           (name, cui, tip))
        firm_id = cur.lastrowid
        conn.execute(
            "INSERT INTO user_firms(user_id, firm_id, role, active) "
            "VALUES(?,?,?,1)", (user_id, firm_id, role))
        conn.execute("INSERT INTO firm_keys(firm_id, wrapped_key) VALUES(?,?)",
                     (firm_id, psec.wrap_key(secret, os.urandom(32))))
        conn.commit()
        return firm_id

    @app.route("/inregistrare", methods=["GET", "POST"])
    def register():
        if request.method == "GET":
            return render_template("inregistrare.html", eroare=None)
        f = request.form
        name, cui = f.get("name", "").strip(), f.get("cui", "").strip()
        password = f.get("password", "")
        tip = f.get("tip", "").strip()
        if not all([name, cui, password]) or tip not in pdb.FIRM_TIPURI:
            return render_template(
                "inregistrare.html",
                eroare="Toate campurile sunt obligatorii - inclusiv "
                      "denumirea, completata automat din CUI.")
        if not f.get("accept_termeni"):
            return render_template(
                "inregistrare.html",
                eroare="Trebuie sa accepti Termenii si Conditiile si Politica "
                      "de confidentialitate pentru a crea un cont.")
        if len(password) < 10:
            return render_template("inregistrare.html",
                                   eroare="Parola trebuie sa aiba minim 10 caractere.")
        if conn.execute("SELECT 1 FROM firms WHERE cui=?", (cui,)).fetchone():
            return render_template("inregistrare.html",
                                   eroare="Exista deja o firma cu acest CUI.")
        eroare = _verify_cui_or_error(cui)
        if eroare:
            return render_template("inregistrare.html", eroare=eroare)
        # Login identifies people by CUI + parola, not de un nume ales de ei -
        # username ramane doar o eticheta interna (audit, panoul de echipa).
        username = _unique_username(_slugify(name))
        cur = conn.execute(
            "INSERT INTO users(username, pw_hash) VALUES(?,?)",
            (username, psec.hash_password(password)))
        user_id = cur.lastrowid
        firm_id = _create_firm(name, cui, tip, user_id, "admin")
        session.permanent = True
        session["user_id"] = user_id
        session["active_firm_id"] = firm_id
        return redirect(url_for("aplicatie"))

    @app.route("/autentificare", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            return render_template("autentificare.html", eroare=None)
        identificator = request.form.get("cui", "").strip()
        password = request.form.get("password", "")
        eroare_autentificare = "CUI sau parola incorecta."

        # Master nu apartine niciunei firme (nu are CUI), asa ca ramane
        # singurul cont care se autentifica prin numele lui de utilizator.
        row = conn.execute(
            "SELECT * FROM users WHERE username=? AND is_master=1",
            (identificator,)).fetchone()
        if row is None or not psec.verify_password(row["pw_hash"], password):
            row = None
            # O firma poate avea mai multi colegi (admin/contabil/junior)
            # care ii impart CUI-ul la autentificare - parola singura ii
            # distinge (add_member impiedica doi colegi sa aiba aceeasi).
            candidati = conn.execute(
                "SELECT u.* FROM users u "
                "JOIN user_firms uf ON uf.user_id = u.id AND uf.active = 1 "
                "JOIN firms f ON f.id = uf.firm_id "
                "WHERE f.cui = ? AND u.active = 1",
                (identificator,)).fetchall()
            row = next((r for r in candidati
                       if psec.verify_password(r["pw_hash"], password)), None)
        if row is None or not row["active"]:
            return render_template("autentificare.html",
                                   eroare=eroare_autentificare)
        session.permanent = True
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
                               eroare=request.args.get("eroare"),
                               mesaj=request.args.get("mesaj"))

    @app.post("/panou/firme")
    def add_firm():
        user = current_user()
        if user is None or user["is_master"]:
            return redirect(url_for("login"))
        name = request.form.get("name", "").strip()
        cui = request.form.get("cui", "").strip()
        tip = request.form.get("tip", "").strip()
        if not name or not cui or tip not in pdb.FIRM_TIPURI:
            return redirect(url_for(
                "panou", eroare="Denumirea, CUI-ul si tipul firmei sunt obligatorii."))
        if conn.execute("SELECT 1 FROM firms WHERE cui=?", (cui,)).fetchone():
            return redirect(url_for(
                "panou", eroare="Exista deja o firma cu acest CUI."))
        eroare = _verify_cui_or_error(cui)
        if eroare:
            return redirect(url_for("panou", eroare=eroare))
        firm_id = _create_firm(name, cui, tip, user["id"], "admin")
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
        # Colegii aceleiasi firme se autentifica toti cu acelasi CUI, deci
        # parola trebuie sa fie unica intre ei ca sa se stie cine e cine.
        colegi = conn.execute(
            "SELECT u.pw_hash FROM users u "
            "JOIN user_firms uf ON uf.user_id = u.id "
            "WHERE uf.firm_id=? AND uf.active=1", (active_firm_id,)).fetchall()
        if any(psec.verify_password(c["pw_hash"], password) for c in colegi):
            return redirect(url_for(
                "panou", eroare="Aceasta parola este deja folosita de un alt "
                                "cont din aceasta firma. Alege alta parola, "
                                "ca fiecare coleg sa poata fi recunoscut unic "
                                "la autentificare doar cu CUI-ul firmei."))
        username_atribuit = _unique_username(username)
        cur = conn.execute(
            "INSERT INTO users(username, pw_hash) VALUES(?,?)",
            (username_atribuit, psec.hash_password(password)))
        conn.execute(
            "INSERT INTO user_firms(user_id, firm_id, role, active) "
            "VALUES(?,?,?,1)", (cur.lastrowid, active_firm_id, role))
        conn.commit()
        mesaj = (f"Cont creat: {username_atribuit}."
                if username_atribuit != username else None)
        if mesaj:
            mesaj += (f" Numele '{username}' era deja folosit de alt cont, "
                     "asa ca a fost atribuit acesta ca eticheta - la "
                     "autentificare colegul foloseste tot CUI-ul firmei, "
                     "cu parola lui.")
        return redirect(url_for("panou", mesaj=mesaj))

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
        return render_template("master.html", user=user, firms=firms,
                               versiune=pipeline.running_vs_current())

    @app.post("/master/firma/<int:firm_id>/comutare")
    def toggle_firm(firm_id):
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        conn.execute("UPDATE firms SET active = 1 - active WHERE id=?", (firm_id,))
        conn.commit()
        return redirect(url_for("master"))

    @app.get("/master/utilizatori")
    def master_users():
        """Everything about every account in one page: which firms they
        belong to, with what role, how many clients/reconciliations they
        have, and when they were last active - no per-firm clicking around."""
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        users_rows = conn.execute(
            "SELECT * FROM users ORDER BY is_master DESC, username").fetchall()
        overview = []
        for u in users_rows:
            memberships = conn.execute(
                "SELECT f.id AS firm_id, f.name AS firm_name, f.cui, f.tip, "
                "f.active AS firm_active, uf.role, uf.active AS membership_active "
                "FROM user_firms uf JOIN firms f ON f.id = uf.firm_id "
                "WHERE uf.user_id=? ORDER BY f.name", (u["id"],)).fetchall()
            firme = []
            n_reconcilieri_total = 0
            ultima_activitate = None
            for m in memberships:
                fc = firm_conn(m["firm_id"])
                n_clienti = fc.execute(
                    "SELECT COUNT(*) AS n FROM client_assignments "
                    "WHERE username=?", (u["username"],)).fetchone()["n"]
                n_reconcilieri = fc.execute(
                    "SELECT COUNT(*) AS n FROM reconciliations "
                    "WHERE created_by=?", (u["username"],)).fetchone()["n"]
                n_reconcilieri_total += n_reconcilieri
                ultima = fc.execute(
                    "SELECT action, ts FROM audit_log WHERE user_id=? "
                    "ORDER BY ts DESC LIMIT 1", (u["username"],)).fetchone()
                if ultima and (ultima_activitate is None
                              or ultima["ts"] > ultima_activitate["ts_raw"]):
                    ultima_activitate = {
                        "action": ultima["action"], "ts_raw": ultima["ts"],
                        "ts": datetime.fromisoformat(ultima["ts"])
                                     .strftime("%Y-%m-%d %H:%M")}
                firme.append({
                    "firm_id": m["firm_id"], "firm_name": m["firm_name"],
                    "cui": m["cui"], "tip": m["tip"],
                    "firm_active": bool(m["firm_active"]), "role": m["role"],
                    "membership_active": bool(m["membership_active"]),
                    "n_clienti": n_clienti, "n_reconcilieri": n_reconcilieri,
                })
            firme_max = max((f["n_reconcilieri"] for f in firme), default=0)
            for f in firme:
                f["bar_pct"] = _bar_pct(f["n_reconcilieri"], firme_max)
            overview.append({
                "user": u, "firme": firme,
                "n_reconcilieri_total": n_reconcilieri_total,
                "ultima_activitate": ultima_activitate,
                "avatar_color": _avatar_color(u["username"]),
            })

        conturi = [o for o in overview if not o["user"]["is_master"]]
        total_reconcilieri = sum(o["n_reconcilieri_total"] for o in conturi)
        kpi = {
            "total_conturi": len(conturi),
            "total_firme_active": conn.execute(
                "SELECT COUNT(*) AS n FROM firms WHERE active=1"
            ).fetchone()["n"],
            "total_reconcilieri": total_reconcilieri,
            "medie_reconcilieri": round(total_reconcilieri / len(conturi), 1)
                                 if conturi else 0,
        }
        top_conturi = sorted(conturi, key=lambda o: -o["n_reconcilieri_total"])[:8]
        top_max = max((o["n_reconcilieri_total"] for o in top_conturi), default=0)
        top_conturi = [{"username": o["user"]["username"],
                       "n": o["n_reconcilieri_total"],
                       "bar_pct": _bar_pct(o["n_reconcilieri_total"], top_max)}
                      for o in top_conturi]
        tip_counts = conn.execute(
            "SELECT tip, COUNT(*) AS n FROM firms WHERE active=1 "
            "GROUP BY tip").fetchall()
        tip_by_key = {r["tip"]: r["n"] for r in tip_counts}
        firm_tip_dist = _donut_segments([
            ("Contabilitate", tip_by_key.get(pdb.FIRM_TIP_CONTABILITATE, 0)),
            ("Firma/PFA directa", tip_by_key.get(pdb.FIRM_TIP_DIRECT, 0)),
        ])

        return render_template("master_utilizatori.html", user=user,
                               overview=overview, kpi=kpi,
                               top_conturi=top_conturi,
                               firm_tip_dist=firm_tip_dist)

    def _istoric_utilizator(target) -> list[dict]:
        """Every audit-log action by this user, across every firm they
        belong to, newest first. The audit log lives inside each firm's
        own (encrypted) database, keyed by username, so this fans out
        across firms and merges by timestamp."""
        firme = conn.execute(
            "SELECT f.id, f.name FROM user_firms uf JOIN firms f "
            "ON f.id = uf.firm_id WHERE uf.user_id=?", (target["id"],)).fetchall()
        evenimente = []
        for firma in firme:
            for e in audit.entries(firm_conn(firma["id"]), limit=5000,
                                   user_id=target["username"]):
                evenimente.append({**e, "firm_id": firma["id"],
                                   "firm_name": firma["name"]})
        evenimente.sort(key=lambda e: e["ts"], reverse=True)
        return evenimente

    def _istoric_la_xml(target, evenimente) -> bytes:
        root = ET.Element("istoric_utilizator", utilizator=target["username"])
        for e in evenimente:
            actiune = ET.SubElement(root, "actiune")
            ET.SubElement(actiune, "data").text = e["ts"]
            ET.SubElement(actiune, "tip").text = e["action"]
            ET.SubElement(actiune, "firma").text = e["firm_name"]
            if e.get("entity"):
                ET.SubElement(actiune, "entitate").text = str(e["entity"])
            if e.get("entity_id"):
                ET.SubElement(actiune, "entitate_id").text = str(e["entity_id"])
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    @app.get("/master/utilizatori/<int:user_id>/istoric")
    def master_user_history(user_id):
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        target = conn.execute("SELECT * FROM users WHERE id=?",
                              (user_id,)).fetchone()
        if target is None:
            return redirect(url_for("master_users"))
        evenimente = _istoric_utilizator(target)
        return render_template("master_istoric.html", user=user,
                               target=target, evenimente=evenimente)

    @app.get("/master/utilizatori/<int:user_id>/istoric.xml")
    def master_user_history_xml(user_id):
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        target = conn.execute("SELECT * FROM users WHERE id=?",
                              (user_id,)).fetchone()
        if target is None:
            return redirect(url_for("master_users"))
        evenimente = _istoric_utilizator(target)
        xml_bytes = _istoric_la_xml(target, evenimente)
        return Response(
            xml_bytes, mimetype="application/xml",
            headers={"Content-Disposition":
                     f'attachment; filename="istoric_{target["username"]}.xml"'})

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
            result = pipeline.promote(source, target)
        except pipeline.PipelineError as e:
            return redirect(url_for("pipeline_dashboard", eroare=str(e)))
        commit = result["commit"]
        pipeline.log_promotion(conn, source, target, commit, user["username"])
        if result["pushed"]:
            mesaj = (f"{source} -> {target} promovat la commit-ul {commit} "
                    f"si trimis pe GitHub. Reporneste manual serverul din '{target}'.")
            return redirect(url_for("pipeline_dashboard", mesaj=mesaj))
        eroare = (f"{source} -> {target} promovat local la commit-ul {commit}, "
                 f"dar push-ul pe GitHub a esuat: {result['push_error']}. "
                 f"Codul e promovat corect local - ruleaza manual "
                 f"'git push origin {pipeline.ENVIRONMENTS[target]['branch']}' "
                 f"din folderul {target}.")
        return redirect(url_for("pipeline_dashboard", eroare=eroare))

    # ---------- product API (session-based) ----------
    @app.get("/api/me")
    @require()
    def me(ident):
        return jsonify({"username": ident["username"], "role": ident["role"],
                        "firm_name": ident["firm_name"],
                        "firm_tip": ident["firm_tip"],
                        "onboarding_completat": ident["onboarding_completat"],
                        "permissions": sorted(ident["permissions"])})

    @app.post("/api/onboarding/completat")
    @require()
    def onboarding_completat(ident):
        conn.execute("UPDATE users SET onboarding_completat=1 WHERE username=?",
                    (ident["username"],))
        conn.commit()
        return jsonify({"ok": True})

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

    _EROARE_FIRMA_DIRECTA = ("Firmele directe (PFA/SRL care isi fac singure "
                            "calculele) nu au clienti - reconciliezi direct, "
                            "ca firma. Doar firmele de contabilitate au clienti.")

    @app.post("/api/clients")
    @require("clienti.creare")
    def add_client(ident):
        if ident["firm_tip"] == pdb.FIRM_TIP_DIRECT:
            return jsonify({"error": _EROARE_FIRMA_DIRECTA}), 403
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
        if ident["firm_tip"] == pdb.FIRM_TIP_DIRECT:
            return jsonify({"error": _EROARE_FIRMA_DIRECTA}), 403
        fc = firm_conn(ident["firm_id"])
        clients.delete_client(fc, cid)
        audit.log(fc, ident["username"], "client.stergere", "client", str(cid))
        return jsonify({"ok": True})

    @app.post("/api/assignments")
    @require("useri.gestionare")
    def assign_client(ident):
        if ident["firm_tip"] == pdb.FIRM_TIP_DIRECT:
            return jsonify({"error": _EROARE_FIRMA_DIRECTA}), 403
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
        # O firma directa reconciliaza ca ea insasi, fara client - doar o
        # firma de contabilitate alege un client dintr-o lista.
        client_id = (None if ident["firm_tip"] == pdb.FIRM_TIP_DIRECT
                    else int(request.form["client_id"]))
        period = request.form["period"]
        anaf_file = request.files["anaf_file"]
        company_files = request.files.getlist("company_file")
        if not company_files:
            return jsonify({"errors": ["Lipseste jurnalul firmei."]}), 400

        if anaf_file.filename.lower().endswith((".pdf", ".json")):
            saved_anaf_path = _save_upload(anaf_file)
            try:
                if anaf_file.filename.lower().endswith(".json"):
                    anaf_doc = parse_p300_json(saved_anaf_path)
                else:
                    anaf_doc = parse_p300_pdf(saved_anaf_path)
            except (NotAnafP300, NotAnafP300Json) as e:
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
            "LEFT JOIN clients c ON c.id = r.client_id WHERE r.id=?",
            (rid,)).fetchone()
        if row is None:
            return jsonify({"error": "Reconciliere inexistenta"}), 404
        # O firma directa nu are client (reconciliaza ca ea insasi) - numele
        # de afisat pe raport e atunci al firmei, nu al unui client.
        nume_raport = row["name"] or ident["firm_name"]
        path = os.path.join(upload_dir, f"raport_{ident['firm_id']}_{rid}.xlsx")
        if _reconciliation_mode(fc, rid) == "d300_lines":
            comp = _load_lines(fc, rid, "invoices_company")
            anaf = _load_lines(fc, rid, "invoices_anaf")
            result = reconcile_d300(comp, anaf)
            export_mod.write_report_lines(result, suggest_d300_lines(result),
                                          path, nume_raport, row["period"])
        else:
            comp = _load_rows(fc, rid, "invoices_company")
            anaf = _load_rows(fc, rid, "invoices_anaf")
            result = reconcile(comp, anaf)
            export_mod.write_report(result, suggest_d300(result), path,
                                    nume_raport, row["period"])
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
