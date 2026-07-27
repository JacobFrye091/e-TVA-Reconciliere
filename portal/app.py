"""e-TVA Reconciliere — the web platform.

One Flask app: public landing + firm accounts + the full reconciliation
product served in the browser. Each firm's working data lives in its own
SQLCipher-encrypted database on the server, opened with the firm's data key.
"""
import json, os, pathlib, re, secrets, smtplib, threading
import xml.etree.ElementTree as ET
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Flask, request, session, redirect, url_for, jsonify,
                   render_template, send_file, Response, g)
from flask_wtf.csrf import CSRFProtect, generate_csrf

from portal import db as pdb
from portal import security as psec
from portal import pipeline
from portal import invoicing
from portal import contract as contract_mod
from portal import backup as backup_mod
from portal import trial_reminders as remind_mod
from etva import db as fdb
from etva import audit, clients
from etva import anaf_cui
from etva import anaf_oauth
from etva import digital_signature
from etva import efactura_xml
from etva import esemneaza
from etva import export as export_mod
from etva.importer.company import parse_company_journal, ImportError_
from etva.importer.anaf import FileAnafDataSource
from etva.importer.saga import parse_saga_journal, NotSagaFormat
from etva.importer.anaf_p300 import parse_p300_pdf, NotAnafP300
from etva.importer.anaf_p300_json import (parse_p300_json, parse_p300_json_data,
                                          NotAnafP300Json)
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
_CONTACT = _ROOT / "docs" / "contact.html"
_SPA = _ROOT / "web" / "index.html"

CONTACT_EMAIL_TO = os.environ.get("CONTACT_EMAIL_TO", "office@ereconciliere.ro")

ANAF_OAUTH_CLIENT_ID = os.environ.get("ANAF_OAUTH_CLIENT_ID")
ANAF_OAUTH_CLIENT_SECRET = os.environ.get("ANAF_OAUTH_CLIENT_SECRET")
ANAF_OAUTH_REDIRECT_URI = os.environ.get(
    "ANAF_OAUTH_REDIRECT_URI", "https://ereconciliere.ro/api/anaf/callback")
ANAF_TOKEN_VALIDITY_ZILE = 90

# "test" (implicit) foloseste sandbox-ul ANAF (api.anaf.ro/test/FCTEL) - nu
# trimite nimic cu valoare legala. Se schimba explicit in "prod" abia dupa
# ce un XML de test a fost validat cu adevarat impotriva sandbox-ului.
ANAF_EFACTURA_MEDIU = os.environ.get("ANAF_EFACTURA_MEDIU", "test")

# Semnarea contractului de prestari servicii (vezi semneaza_contract) - fara
# aceasta cheie, optiunea de semnare ramane indisponibila (mesaj explicit,
# nu o eroare oarba). ESEMNEAZA_WEBHOOK_SECRET/HEADER verifica evenimentele
# primite pe /api/esemneaza/webhook - configurate manual, in oglinda, si in
# contul eSemneaza (pagina Setari API); fara ele, verificarea starii se
# bazeaza doar pe polling (vezi _verifica_finalizare_esemneaza).
ESEMNEAZA_API_KEY = os.environ.get("ESEMNEAZA_API_KEY")
ESEMNEAZA_WEBHOOK_HEADER = os.environ.get("ESEMNEAZA_WEBHOOK_HEADER", "X-Webhook-Secret")
ESEMNEAZA_WEBHOOK_SECRET = os.environ.get("ESEMNEAZA_WEBHOOK_SECRET")

# Implicit dezactivat: emailul de confirmare e singura cale de livrare a
# link-ului de validare (spre deosebire de formularul de contact, unde o
# eroare de trimitere nu blocheaza nimic), asa ca blocarea reala a
# conturilor neverificate ramane oprita pana se confirma ca SMTP chiar
# livreaza pe serverul de productie - altfel un client nou ar ramane blocat
# definitiv fara nicio alternativa.
EMAIL_VERIFICARE_OBLIGATORIE = os.environ.get("EMAIL_VERIFICARE_OBLIGATORIE") == "1"

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


def create_app(data_dir: str, enable_backup_scheduler: bool = False,
               enable_trial_reminder_scheduler: bool = False) -> Flask:
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
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # Ramane False pana serverul ruleaza real sub HTTPS (deploy pe VPS inca
    # blocat pe acces root) - altfel cookie-ul de sesiune nu s-ar mai trimite
    # deloc peste conexiunea locala HTTP folosita azi de toate cele trei
    # medii, ceea ce ar rupe login-ul complet. De activat prin variabila de
    # mediu, nu prin schimbare de cod, cand exista TLS real.
    app.config["SESSION_COOKIE_SECURE"] = (
        os.environ.get("SESSION_COOKIE_SECURE", "0") == "1")
    csrf = CSRFProtect(app)

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
        g.db_lock_acquired = True

    @app.teardown_request
    def _release_db_lock(exc=None):
        # teardown_request ruleaza mereu, chiar daca before_request-ul de
        # mai sus n-a apucat sa ruleze (ex: CSRFProtect isi inregistreaza
        # propriul before_request, care poate respinge cererea cu 400
        # inainte ca acesta sa apuce lock-ul) - fara verificarea de mai jos,
        # eliberarea unui lock neachizitionat de cererea curenta arunca
        # RuntimeError.
        if g.pop("db_lock_acquired", False):
            db_lock.release()

    if enable_backup_scheduler:
        backup_mod.start_scheduler(data_dir, db_lock)

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
            "SELECT uf.role, f.id, f.name, f.tip, f.cui FROM user_firms uf "
            "JOIN firms f ON f.id = uf.firm_id "
            "WHERE uf.user_id=? AND uf.firm_id=? AND uf.active=1 AND f.active=1 "
            "AND f.arhivata_la IS NULL",
            (user["id"], active_firm_id)).fetchone()
        if row is None:
            return None
        return {"username": user["username"], "role": row["role"],
                "firm_id": row["id"], "firm_name": row["name"],
                "firm_tip": row["tip"], "firm_cui": row["cui"],
                "onboarding_completat": bool(user["onboarding_completat"]),
                "permissions": pdb.ROLE_PERMISSIONS[row["role"]]}

    def _log_master_action(user, actiune: str, detalii: str | None = None) -> None:
        conn.execute(
            "INSERT INTO master_actions(actiune, detalii, creat_de, creat_la) "
            "VALUES(?,?,?,?)",
            (actiune, detalii, user["username"],
             datetime.now(timezone.utc).isoformat()))
        conn.commit()

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
    @app.get("/index.html")
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

    @app.get("/contact.html")
    def contact_page():
        return send_file(_CONTACT)

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

    # ---------- ANAF OAuth2 (decontul precompletat) ----------
    def _store_anaf_tokens(firm_id: int, tokens: dict, username: str) -> None:
        acum = datetime.now()
        expira = acum + timedelta(days=ANAF_TOKEN_VALIDITY_ZILE)
        conn.execute(
            "INSERT INTO anaf_oauth_tokens(firm_id, wrapped_access_token, "
            "wrapped_refresh_token, obtinut_la, expira_la, autorizat_de) "
            "VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(firm_id) DO UPDATE SET "
            "wrapped_access_token=excluded.wrapped_access_token, "
            "wrapped_refresh_token=excluded.wrapped_refresh_token, "
            "obtinut_la=excluded.obtinut_la, expira_la=excluded.expira_la, "
            "autorizat_de=excluded.autorizat_de",
            (firm_id, psec.wrap_key(secret, tokens["access_token"].encode()),
             psec.wrap_key(secret, tokens["refresh_token"].encode()),
             acum.isoformat(), expira.isoformat(), username))
        conn.commit()

    def get_valid_anaf_access_token(firm_id: int) -> str | None:
        """The firm's current ANAF access token, refreshing it first if it's
        expired (or close to it). None if the firm never authorized access."""
        row = conn.execute(
            "SELECT * FROM anaf_oauth_tokens WHERE firm_id=?", (firm_id,)).fetchone()
        if row is None:
            return None
        expira = datetime.fromisoformat(row["expira_la"])
        if expira > datetime.now() + timedelta(days=1):
            return psec.unwrap_key(secret, row["wrapped_access_token"]).decode()
        refresh_token = psec.unwrap_key(secret, row["wrapped_refresh_token"]).decode()
        tokens = anaf_oauth.refresh_access_token(
            ANAF_OAUTH_CLIENT_ID, ANAF_OAUTH_CLIENT_SECRET, refresh_token)
        _store_anaf_tokens(firm_id, tokens, row["autorizat_de"])
        return tokens["access_token"]

    @app.get("/panou/anaf/autorizare")
    def anaf_oauth_autorizare():
        """Un admin de firma porneste aici fluxul prin care ANAF ii cere
        certificatul digital calificat al firmei si, daca il accepta,
        autorizeaza e-TVA Reconciliere sa ii citeasca decontul precompletat -
        fara ca noi sa vedem vreodata acel certificat."""
        user = current_user()
        active_firm_id = session.get("active_firm_id")
        if (user is None or user["is_master"] or active_firm_id is None
                or _role_in_firm(user["id"], active_firm_id) != "admin"):
            return redirect(url_for("login"))
        if not ANAF_OAUTH_CLIENT_ID or not ANAF_OAUTH_CLIENT_SECRET:
            return redirect(url_for(
                "panou", eroare="Integrarea ANAF nu este configurata pe acest server."))
        state = secrets.token_urlsafe(16)
        session["anaf_oauth_pending_firm_id"] = active_firm_id
        session["anaf_oauth_state"] = state
        return redirect(anaf_oauth.build_authorize_url(
            ANAF_OAUTH_CLIENT_ID, ANAF_OAUTH_REDIRECT_URI, state))

    @app.get("/api/anaf/callback")
    def anaf_oauth_callback():
        firm_id = session.pop("anaf_oauth_pending_firm_id", None)
        expected_state = session.pop("anaf_oauth_state", None)
        code = request.args.get("code")
        state = request.args.get("state")
        if firm_id is None or not code or state != expected_state:
            return redirect(url_for(
                "panou", eroare="Autorizarea ANAF a esuat sau a expirat - "
                                "incearca din nou."))
        try:
            tokens = anaf_oauth.exchange_code_for_tokens(
                ANAF_OAUTH_CLIENT_ID, ANAF_OAUTH_CLIENT_SECRET, code,
                ANAF_OAUTH_REDIRECT_URI)
        except anaf_oauth.AnafOAuthError as e:
            return redirect(url_for("panou", eroare=f"ANAF: {e}"))
        user = current_user()
        _store_anaf_tokens(firm_id, tokens,
                           user["username"] if user else "necunoscut")
        return redirect(url_for(
            "panou", mesaj="Accesul la decontul ANAF a fost autorizat cu succes."))

    def _zile_trial_ramase(trial_expira_la: "str | None") -> "int | None":
        if not trial_expira_la:
            return None
        expira = datetime.fromisoformat(trial_expira_la)
        return max(0, (expira - datetime.now(timezone.utc)).days)

    def _luni_pentru_ciclu(ciclu: str) -> int:
        return {pdb.CICLU_LUNAR: 1, pdb.CICLU_6_LUNI: 6, pdb.CICLU_AN: 12}[ciclu]

    def _calculeaza_suma_plata(firm, ciclu: str) -> float:
        """Pretul de baza, fara TVA - firma 'contabilitate' plateste per
        client gestionat (minim 1, ca o firma abia inregistrata - fara
        clienti inca - sa nu ajunga la o factura de 0 RON); firma 'direct'
        are tarif fix per firma. Folosit ca atare pentru contracts.suma
        (contractul afiseaza explicit "exclusiv TVA" langa aceasta suma) -
        suma efectiv ceruta clientului la plata trece prin _suma_cu_tva."""
        pret_lunar = pdb.get_preturi(conn)[firm["tip"]][ciclu]
        luni = _luni_pentru_ciclu(ciclu)
        if firm["tip"] == pdb.FIRM_TIP_CONTABILITATE:
            n_clienti = firm_conn(firm["id"]).execute(
                "SELECT COUNT(*) AS n FROM clients").fetchone()["n"]
            return round(pret_lunar * luni * max(n_clienti, 1), 2)
        return round(pret_lunar * luni, 2)

    def _suma_cu_tva(suma_neta: float) -> float:
        """Suma efectiv ceruta/incasata de la client - pretul de baza
        (exclusiv TVA, vezi _calculeaza_suma_plata) plus cota de TVA curenta
        (pdb.get_cota_tva - editabila din /master/nomenclator, nu hardcodata,
        ca sa poata fi corectata instant cand legea se schimba). Rezultatul e
        cel stocat in payments.suma, ca sa coincida exact cu valoare_totala
        din factura emisa la valideaza_plata - fara aceasta corectie,
        clientul era rugat sa plateasca doar pretul de baza, in timp ce
        factura declara automat un total mai mare (cu TVA), nefiind
        niciodata incasat integral."""
        return round(suma_neta * (1 + pdb.get_cota_tva(conn) / 100), 2)

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

    def _create_firm(name: str, cui: str, tip: str, user_id: int, role: str,
                     email_verificat: bool = False) -> tuple[int, "str | None"]:
        """Create a firm and link it to user_id with the given role. A
        self-reconciling ('direct') firm has no clients at all - it
        reconciles as itself, not as its own client - so nothing further
        happens here for that case; only a 'contabilitate' firm ever
        gets real clients, added by hand afterwards.

        Every new firm gets a 30-day trial from creation. email_verificat
        controls whether it starts pre-confirmed (an already-authenticated,
        already-verified user adding a second firm via /panou/firme) or
        needs its own confirmation link (a brand new /inregistrare) -
        returns (firm_id, token) where token is None in the former case."""
        if tip not in pdb.FIRM_TIPURI:
            tip = pdb.FIRM_TIP_CONTABILITATE
        now = datetime.now(timezone.utc)
        creat_la = now.isoformat()
        trial_expira_la = (now + timedelta(days=pdb.TRIAL_ZILE)).isoformat()
        token = None if email_verificat else secrets.token_urlsafe(32)
        cur = conn.execute(
            "INSERT INTO firms(name, cui, tip, email_verificat, "
            "email_verificare_token, creat_la, trial_expira_la) "
            "VALUES(?,?,?,?,?,?,?)",
            (name, cui, tip, 1 if email_verificat else 0, token,
             creat_la, trial_expira_la))
        firm_id = cur.lastrowid
        conn.execute(
            "INSERT INTO user_firms(user_id, firm_id, role, active) "
            "VALUES(?,?,?,1)", (user_id, firm_id, role))
        conn.execute("INSERT INTO firm_keys(firm_id, wrapped_key) VALUES(?,?)",
                     (firm_id, psec.wrap_key(secret, os.urandom(32))))
        conn.commit()
        return firm_id, token

    @app.route("/inregistrare", methods=["GET", "POST"])
    def register():
        if request.method == "GET":
            return render_template("inregistrare.html", eroare=None)
        f = request.form
        name, cui = f.get("name", "").strip(), f.get("cui", "").strip()
        email = f.get("email", "").strip()
        password = f.get("password", "")
        tip = f.get("tip", "").strip()
        if not all([name, cui, email, password]) or tip not in pdb.FIRM_TIPURI:
            return render_template(
                "inregistrare.html",
                eroare="Toate campurile sunt obligatorii - inclusiv "
                      "denumirea, completata automat din CUI.")
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            return render_template("inregistrare.html",
                                   eroare="Adresa de email nu pare valida.")
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
            "INSERT INTO users(username, pw_hash, email) VALUES(?,?,?)",
            (username, psec.hash_password(password), email))
        user_id = cur.lastrowid
        firm_id, token = _create_firm(name, cui, tip, user_id, "admin")
        if token:
            _trimite_email_verificare(email, name, token)
        session.permanent = True
        session["user_id"] = user_id
        session["active_firm_id"] = firm_id
        return redirect(url_for("aplicatie"))

    @app.get("/verifica-email/<token>")
    def verifica_email(token):
        firm = conn.execute(
            "SELECT * FROM firms WHERE email_verificare_token=?", (token,)).fetchone()
        if firm is None:
            return redirect(url_for(
                "login", eroare="Link de confirmare invalid sau deja folosit."))
        conn.execute(
            "UPDATE firms SET email_verificat=1, email_verificare_token=NULL "
            "WHERE id=?", (firm["id"],))
        conn.commit()
        # Ordinea conteaza - clientul primeste confirmarea intai, abia apoi
        # ajunge si notificarea interna la master.
        admin = conn.execute(
            "SELECT u.email FROM user_firms uf "
            "JOIN users u ON u.id = uf.user_id "
            "WHERE uf.firm_id=? AND uf.role='admin' LIMIT 1", (firm["id"],)).fetchone()
        if admin and admin["email"]:
            _trimite_email(
                admin["email"], "Contul tau e-TVA Reconciliere e confirmat",
                f"Salut,\n\nContul firmei {firm['name']} a fost confirmat cu "
                f"succes. Te poti autentifica oricand pe platforma.")
        _trimite_email(
            CONTACT_EMAIL_TO, "[e-TVA] Cont nou confirmat",
            f"Firma {firm['name']} (CUI {firm['cui']}) si-a confirmat adresa de email.")
        return redirect(url_for(
            "login", mesaj="Adresa de email a fost confirmata - te poti autentifica."))

    @app.get("/asteapta-verificare-email")
    def asteapta_verificare_email():
        user = current_user()
        if user is None:
            return redirect(url_for("login"))
        return render_template(
            "asteapta_verificare_email.html", user=user,
            mesaj=request.args.get("mesaj"))

    @app.post("/retrimite-verificare-email")
    def retrimite_verificare_email():
        user = current_user()
        active_firm_id = session.get("active_firm_id")
        if user is None or active_firm_id is None:
            return redirect(url_for("login"))
        firm = conn.execute(
            "SELECT * FROM firms WHERE id=?", (active_firm_id,)).fetchone()
        if firm is None or firm["email_verificat"] or not user["email"]:
            return redirect(url_for("asteapta_verificare_email"))
        token = firm["email_verificare_token"] or secrets.token_urlsafe(32)
        conn.execute("UPDATE firms SET email_verificare_token=? WHERE id=?",
                    (token, firm["id"]))
        conn.commit()
        _trimite_email_verificare(user["email"], firm["name"], token)
        return redirect(url_for(
            "asteapta_verificare_email", mesaj="Email retrimis."))

    def _login_blocat(identificator: str) -> "str | None":
        """Mesajul de blocare daca identificatorul (CUI sau username master)
        a esuat de prea multe ori recent, altfel None. Verificat inaintea
        oricarei comparatii de parola - un identificator blocat nu ajunge
        deloc la bcrypt, ca sa nu incurajam brute-force-ul sa continue."""
        row = conn.execute(
            "SELECT blocat_pana FROM login_lockouts WHERE identificator=?",
            (identificator,)).fetchone()
        if row is None or row["blocat_pana"] is None:
            return None
        blocat_pana = datetime.fromisoformat(row["blocat_pana"])
        acum = datetime.now(timezone.utc)
        if acum >= blocat_pana:
            return None
        minute = int((blocat_pana - acum).total_seconds() // 60) + 1
        return (f"Prea multe incercari esuate. Incearca din nou peste "
                f"{minute} minute.")

    def _inregistreaza_login_esuat(identificator: str) -> None:
        acum = datetime.now(timezone.utc)
        row = conn.execute(
            "SELECT incercari FROM login_lockouts WHERE identificator=?",
            (identificator,)).fetchone()
        incercari = (row["incercari"] if row else 0) + 1
        blocat_pana = (
            (acum + timedelta(minutes=pdb.LOGIN_BLOCARE_MINUTE)).isoformat()
            if incercari >= pdb.LOGIN_MAX_INCERCARI else None)
        if row is None:
            conn.execute(
                "INSERT INTO login_lockouts(identificator, incercari, "
                "ultima_incercare, blocat_pana) VALUES(?,?,?,?)",
                (identificator, incercari, acum.isoformat(), blocat_pana))
        else:
            conn.execute(
                "UPDATE login_lockouts SET incercari=?, ultima_incercare=?, "
                "blocat_pana=? WHERE identificator=?",
                (incercari, acum.isoformat(), blocat_pana, identificator))
        conn.commit()

    def _reseteaza_login_esuat(identificator: str) -> None:
        conn.execute("DELETE FROM login_lockouts WHERE identificator=?",
                    (identificator,))
        conn.commit()

    @app.route("/autentificare", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            return render_template(
                "autentificare.html", eroare=request.args.get("eroare"),
                mesaj=request.args.get("mesaj"))
        identificator = request.form.get("cui", "").strip()
        password = request.form.get("password", "")
        eroare_autentificare = "CUI sau parola incorecta."

        mesaj_blocare = _login_blocat(identificator)
        if mesaj_blocare:
            return render_template("autentificare.html", eroare=mesaj_blocare)

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
            _inregistreaza_login_esuat(identificator)
            return render_template("autentificare.html",
                                   eroare=eroare_autentificare)
        _reseteaza_login_esuat(identificator)
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
        # Verificat inaintea current_identity() - o firma arhivata are un
        # motiv specific si actionabil (alege un ciclu si plateste), diferit
        # de "neautentificat", pe care current_identity() l-ar fi intors
        # oricum (interogarea ei exclude firmele arhivate din alt motiv).
        user = current_user()
        active_firm_id = session.get("active_firm_id")
        if user is not None and not user["is_master"] and active_firm_id is not None:
            firma = conn.execute(
                "SELECT arhivata_la FROM firms WHERE id=?",
                (active_firm_id,)).fetchone()
            if firma is not None and firma["arhivata_la"]:
                return redirect(url_for(
                    "panou",
                    eroare="Contul acestei firme e arhivat - alege un ciclu de "
                          "facturare si plateste ca sa il reactivezi."))
        ident = current_identity()
        if ident is None:
            return redirect(url_for("login"))
        if EMAIL_VERIFICARE_OBLIGATORIE:
            firma = conn.execute(
                "SELECT email_verificat FROM firms WHERE id=?",
                (ident["firm_id"],)).fetchone()
            if firma is not None and not firma["email_verificat"]:
                return redirect(url_for("asteapta_verificare_email"))
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
        cerere_stergere = conn.execute(
            "SELECT * FROM deletion_requests WHERE user_id=? "
            "ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
        anaf_autorizare = None
        plan_activ = None
        zile_trial = None
        contract_activ = None
        if active is not None:
            anaf_autorizare = conn.execute(
                "SELECT * FROM anaf_oauth_tokens WHERE firm_id=?",
                (active["id"],)).fetchone()
            plan_activ = conn.execute(
                "SELECT email_verificat, trial_expira_la, ciclu_facturare, "
                "arhivata_la FROM firms WHERE id=?", (active["id"],)).fetchone()
            zile_trial = _zile_trial_ramase(plan_activ["trial_expira_la"])
            contract_activ = conn.execute(
                "SELECT * FROM contracts WHERE firm_id=? "
                "ORDER BY id DESC LIMIT 1", (active["id"],)).fetchone()
        return render_template("panou.html", user=user, firms=firms,
                               active=active, members=members,
                               subroles=FIRM_SUBROLES,
                               eroare=request.args.get("eroare"),
                               mesaj=request.args.get("mesaj"),
                               anunt=_anunt_activ(), anunt_eticheta=ANUNT_ETICHETE,
                               cerere_stergere=cerere_stergere,
                               anaf_autorizare=anaf_autorizare,
                               plan_activ=plan_activ, zile_trial=zile_trial,
                               contract_activ=contract_activ,
                               email_verificare_obligatorie=EMAIL_VERIFICARE_OBLIGATORIE)

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
        # Deja un cont autentificat (deci deja o adresa de email cunoscuta) -
        # nu mai cerem o a doua confirmare de email pentru firma aditionala.
        firm_id, _token = _create_firm(name, cui, tip, user["id"], "admin",
                                       email_verificat=True)
        session["active_firm_id"] = firm_id
        return redirect(url_for("panou"))

    @app.get("/panou/plan")
    def alege_plan():
        user = current_user()
        active_firm_id = session.get("active_firm_id")
        if (user is None or user["is_master"]
                or not _role_in_firm(user["id"], active_firm_id)):
            return redirect(url_for("login"))
        firm = conn.execute("SELECT * FROM firms WHERE id=?",
                            (active_firm_id,)).fetchone()
        zile_trial = _zile_trial_ramase(firm["trial_expira_la"])
        de_un_an = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        plati = conn.execute(
            "SELECT * FROM payments WHERE firm_id=? AND creat_la>=? "
            "ORDER BY creat_la DESC", (active_firm_id, de_un_an)).fetchall()
        suma_neta_curenta = (_calculeaza_suma_plata(firm, firm["ciclu_facturare"])
                            if firm["ciclu_facturare"] else None)
        suma_curenta = (_suma_cu_tva(suma_neta_curenta)
                       if suma_neta_curenta is not None else None)
        suma_tva_curenta = (round(suma_curenta - suma_neta_curenta, 2)
                           if suma_curenta is not None else None)
        return render_template(
            "alege_plan.html", user=user, firm=firm,
            preturi=pdb.get_preturi(conn)[firm["tip"]],
            zile_trial=zile_trial, suma_neta_curenta=suma_neta_curenta,
            suma_curenta=suma_curenta, suma_tva_curenta=suma_tva_curenta,
            cota_tva=pdb.get_cota_tva(conn), plati=plati,
            arata_plata=(firm["ciclu_facturare"] and zile_trial is not None
                        and zile_trial <= 1),
            eroare=request.args.get("eroare"), mesaj=request.args.get("mesaj"))

    @app.post("/panou/plan")
    def salveaza_plan():
        user = current_user()
        active_firm_id = session.get("active_firm_id")
        if (user is None or user["is_master"]
                or _role_in_firm(user["id"], active_firm_id) != "admin"):
            return redirect(url_for("login"))
        ciclu = request.form.get("ciclu", "")
        if ciclu not in pdb.CICLURI_FACTURARE:
            return redirect(url_for(
                "alege_plan", eroare="Alege un ciclu de facturare valid."))
        conn.execute("UPDATE firms SET ciclu_facturare=? WHERE id=?",
                    (ciclu, active_firm_id))
        conn.commit()
        return redirect(url_for("panou", mesaj="Planul a fost salvat."))

    @app.post("/panou/plata")
    def creeaza_cerere_plata():
        """Inregistreaza intentia de plata a firmei - nu proceseaza nimic
        real inca (vezi TODO integrare FGO/Netopia in _calculeaza_suma_plata
        si mai jos). Master valideaza manual incasarea din /master/plati
        dupa ce o confirma pe alta cale, si abia atunci se emite factura."""
        user = current_user()
        active_firm_id = session.get("active_firm_id")
        if (user is None or user["is_master"]
                or _role_in_firm(user["id"], active_firm_id) != "admin"):
            return redirect(url_for("login"))
        firm = conn.execute("SELECT * FROM firms WHERE id=?",
                            (active_firm_id,)).fetchone()
        if firm is None or not firm["ciclu_facturare"]:
            return redirect(url_for(
                "alege_plan", eroare="Alege intai un ciclu de facturare."))
        contract_curent = conn.execute(
            "SELECT * FROM contracts WHERE firm_id=? ORDER BY id DESC LIMIT 1",
            (active_firm_id,)).fetchone()
        if (contract_curent is None
                or contract_curent["stare"] != pdb.CONTRACT_STARE_SEMNAT
                or contract_curent["ciclu_facturare"] != firm["ciclu_facturare"]):
            return redirect(url_for(
                "vezi_contract",
                eroare="Trebuie sa semnezi contractul de prestari servicii "
                      "inainte de a trimite o cerere de plata."))
        suma = _suma_cu_tva(_calculeaza_suma_plata(firm, firm["ciclu_facturare"]))
        recurent = 1 if request.form.get("recurent") else 0
        # TODO integrare FGO: aici ar trebui creata factura+link de plata
        # prin API-ul FGO (conectat la Netopia Payments) si redirectionat
        # clientul catre acel link, in loc sa marcam direct in_asteptare.
        # Ramane asa pana exista un cont FGO/Netopia real de integrat.
        conn.execute(
            "INSERT INTO payments(firm_id, ciclu_facturare, suma, recurent, "
            "stare, creat_la) VALUES(?,?,?,?,?,?)",
            (active_firm_id, firm["ciclu_facturare"], suma, recurent,
             pdb.PLATA_IN_ASTEPTARE, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return redirect(url_for(
            "alege_plan",
            mesaj="Cererea de plata a fost inregistrata - va fi procesata "
                 "in curand."))

    # ---------- contract de prestari servicii ----------
    def _contract_curent(firm_id: int):
        return conn.execute(
            "SELECT * FROM contracts WHERE firm_id=? ORDER BY id DESC LIMIT 1",
            (firm_id,)).fetchone()

    def _regenereaza_pdf_contract(contract) -> bytes:
        """PDF-ul contractului. Pentru eSemneaza, documentul semnat e un
        artefact real primit de la un tert - servit exact cum a fost primit,
        NU regenerat (spre deosebire de celelalte metode, unde nu exista
        niciun fisier original de pastrat). Pentru semnatura cu mouse-ul (
        metoda veche, pastrata doar pentru contracte semnate inainte de
        eSemneaza) re-embedam PNG-ul desenat; pentru semnatura cu certificat
        nu mai exista fisierul original incarcat, asa ca atasam in schimb
        rezultatul verificarii facute la momentul semnarii."""
        if (contract["metoda_semnatura"] == pdb.CONTRACT_METODA_ESEMNEAZA
                and contract["esemneaza_document_pdf"]):
            return bytes(contract["esemneaza_document_pdf"])
        continut = contract_mod.genereaza_text_din_rand(contract)
        if contract["metoda_semnatura"] == pdb.CONTRACT_METODA_MOUSE:
            semnatura_img = (bytes(contract["semnatura_mouse_img"])
                             if contract["semnatura_mouse_img"] else None)
            return contract_mod.genereaza_pdf(continut, semnatura_img=semnatura_img)
        if contract["metoda_semnatura"] == pdb.CONTRACT_METODA_CERTIFICAT:
            detalii = json.loads(contract["semnatura_detalii"] or "{}")
            nota = contract_mod.nota_verificare_certificat(
                detalii, contract["semnat_la"])
            return contract_mod.genereaza_pdf(continut, nota_semnatura=nota)
        return contract_mod.genereaza_pdf(continut)

    def _genereaza_contract(firm) -> "tuple[object, str | None]":
        """Contractul curent al firmei, generat din nou daca nu exista inca
        unul sau daca firma si-a schimbat ciclul de facturare de atunci.
        Intoarce (rand, eroare) - eroare != None daca ANAF nu a putut fi
        contactat (contractul vechi, daca exista, ramane cel curent)."""
        existent = _contract_curent(firm["id"])
        if (existent is not None
                and existent["ciclu_facturare"] == firm["ciclu_facturare"]):
            return existent, None
        try:
            beneficiar = contract_mod.date_beneficiar(firm["cui"])
        except contract_mod.ContractError as e:
            return existent, str(e)
        suma = _calculeaza_suma_plata(firm, firm["ciclu_facturare"])
        numar = contract_mod.next_contract_number(conn)
        cur = conn.execute(
            "INSERT INTO contracts(firm_id, numar, ciclu_facturare, suma, "
            "beneficiar_denumire, beneficiar_cui, beneficiar_adresa, stare, "
            "creat_la) VALUES(?,?,?,?,?,?,?,?,?)",
            (firm["id"], numar, firm["ciclu_facturare"], suma,
             beneficiar["denumire"], beneficiar["cui"], beneficiar["adresa"],
             pdb.CONTRACT_STARE_IN_ASTEPTARE,
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return conn.execute(
            "SELECT * FROM contracts WHERE id=?", (cur.lastrowid,)).fetchone(), None

    def _finalizeaza_contract_esemneaza(contract, request_id: str):
        """Marcheaza contractul semnat si pastreaza documentul final + (daca
        e disponibil) certificatul de semnatura primite de la eSemneaza -
        spre deosebire de celelalte metode, aici NU se regenereaza nimic:
        documentul semnat e artefactul legal real, controlat de un tert, deci
        trebuie pastrat exact cum a fost primit (aceeasi logica ca la
        arhivarea raspunsului sigilat ANAF pentru e-Factura)."""
        doc = esemneaza.get_completed_document_url(ESEMNEAZA_API_KEY, request_id)
        pdf_bytes = esemneaza.fetch_url_bytes(doc["docUrl"])
        cert_bytes = None
        try:
            cert = esemneaza.get_certificate_download_url(ESEMNEAZA_API_KEY, request_id)
            cert_bytes = esemneaza.fetch_url_bytes(cert["certificateUrl"])
        except esemneaza.EsemneazaError:
            pass
        conn.execute(
            "UPDATE contracts SET stare=?, semnatura_verificata=1, "
            "semnatura_detalii=?, semnat_la=?, esemneaza_document_pdf=?, "
            "esemneaza_certificate_pdf=? WHERE id=?",
            (pdb.CONTRACT_STARE_SEMNAT,
             json.dumps({"metoda": "esemneaza", "request_id": request_id}),
             datetime.now(timezone.utc).isoformat(), pdf_bytes, cert_bytes,
             contract["id"]))
        conn.commit()

    def _verifica_finalizare_esemneaza(contract):
        """Verifica manual la eSemneaza daca cererea de semnare in asteptare
        s-a incheiat - necesar pana serverul are o adresa publica pentru
        webhook (vezi /api/esemneaza/webhook mai jos); apelat automat la
        fiecare vizualizare a paginii de contract, nu doar prin webhook."""
        if (not ESEMNEAZA_API_KEY or not contract
                or contract["stare"] != pdb.CONTRACT_STARE_IN_ASTEPTARE
                or not contract["esemneaza_request_id"]):
            return contract
        try:
            stare = esemneaza.get_sign_request(
                ESEMNEAZA_API_KEY, contract["esemneaza_request_id"])
        except esemneaza.EsemneazaError:
            return contract
        recipienti = stare.get("recipients") or []
        sig = recipienti[0].get("sigStatus") if recipienti else None
        if sig == esemneaza.SIGSTATUS_APPLIED:
            _finalizeaza_contract_esemneaza(contract, contract["esemneaza_request_id"])
        elif sig == esemneaza.SIGSTATUS_REJECTED:
            conn.execute(
                "UPDATE contracts SET esemneaza_request_id=NULL WHERE id=?",
                (contract["id"],))
            conn.commit()
        else:
            return contract
        return conn.execute(
            "SELECT * FROM contracts WHERE id=?", (contract["id"],)).fetchone()

    @app.get("/panou/contract")
    def vezi_contract():
        user = current_user()
        active_firm_id = session.get("active_firm_id")
        if (user is None or user["is_master"]
                or not _role_in_firm(user["id"], active_firm_id)):
            return redirect(url_for("login"))
        firm = conn.execute("SELECT * FROM firms WHERE id=?",
                            (active_firm_id,)).fetchone()
        if firm is None or not firm["ciclu_facturare"]:
            return redirect(url_for(
                "alege_plan", eroare="Alege intai un ciclu de facturare."))
        contract, eroare_generare = _genereaza_contract(firm)
        contract = _verifica_finalizare_esemneaza(contract)
        continut = (contract_mod.genereaza_text_din_rand(contract)
                   if contract is not None else None)
        return render_template(
            "contract_semneaza.html", user=user, firm=firm, contract=contract,
            continut=continut,
            eroare=eroare_generare or request.args.get("eroare"),
            mesaj=request.args.get("mesaj"))

    @app.get("/panou/contract/pdf")
    def descarca_contract_pdf():
        user = current_user()
        active_firm_id = session.get("active_firm_id")
        if (user is None or user["is_master"]
                or not _role_in_firm(user["id"], active_firm_id)):
            return redirect(url_for("login"))
        contract = _contract_curent(active_firm_id)
        if contract is None:
            return redirect(url_for("vezi_contract"))
        pdf_bytes = _regenereaza_pdf_contract(contract)
        return Response(
            pdf_bytes, mimetype="application/pdf",
            headers={"Content-Disposition":
                    f"inline; filename=contract-{contract['numar']}.pdf"})

    @app.get("/panou/contract/xml")
    def descarca_contract_xml():
        user = current_user()
        active_firm_id = session.get("active_firm_id")
        if (user is None or user["is_master"]
                or not _role_in_firm(user["id"], active_firm_id)):
            return redirect(url_for("login"))
        contract = _contract_curent(active_firm_id)
        if contract is None:
            return redirect(url_for("vezi_contract"))
        xml_bytes = contract_mod.date_contract_xml(contract)
        return Response(
            xml_bytes, mimetype="application/xml",
            headers={"Content-Disposition":
                    f'attachment; filename="contract-{contract["numar"]}.xml"'})

    @app.get("/panou/contract/certificat")
    def descarca_certificat_esemneaza():
        user = current_user()
        active_firm_id = session.get("active_firm_id")
        if (user is None or user["is_master"]
                or not _role_in_firm(user["id"], active_firm_id)):
            return redirect(url_for("login"))
        contract = _contract_curent(active_firm_id)
        if contract is None or not contract["esemneaza_certificate_pdf"]:
            return redirect(url_for("vezi_contract"))
        return Response(
            bytes(contract["esemneaza_certificate_pdf"]), mimetype="application/pdf",
            headers={"Content-Disposition":
                    f"inline; filename=certificat-contract-{contract['numar']}.pdf"})

    @app.post("/panou/contract/semneaza")
    def semneaza_contract():
        user = current_user()
        active_firm_id = session.get("active_firm_id")
        if (user is None or user["is_master"]
                or _role_in_firm(user["id"], active_firm_id) != "admin"):
            return redirect(url_for("login"))
        contract = _contract_curent(active_firm_id)
        if contract is None or contract["stare"] != pdb.CONTRACT_STARE_IN_ASTEPTARE:
            return redirect(url_for(
                "vezi_contract", eroare="Nu exista niciun contract de semnat."))
        metoda = request.form.get("metoda")
        acum = datetime.now(timezone.utc).isoformat()

        if metoda == pdb.CONTRACT_METODA_ESEMNEAZA:
            if not ESEMNEAZA_API_KEY:
                return redirect(url_for(
                    "vezi_contract",
                    eroare="Semnarea electronica nu este configurata inca pe acest server."))
            admin = conn.execute(
                "SELECT u.email FROM user_firms uf JOIN users u ON u.id = uf.user_id "
                "WHERE uf.firm_id=? AND uf.role='admin' AND u.email IS NOT NULL "
                "LIMIT 1", (active_firm_id,)).fetchone()
            if admin is None or not admin["email"]:
                return redirect(url_for(
                    "vezi_contract",
                    eroare="Adminul firmei nu are o adresa de email inregistrata "
                          "- necesara pentru trimiterea contractului spre semnare."))
            firm = conn.execute("SELECT * FROM firms WHERE id=?",
                                (active_firm_id,)).fetchone()
            continut = contract_mod.genereaza_text_din_rand(contract)
            # tag_semnatura_esemneaza=True adauga "{{s:1}}" invizibil dupa
            # BENEFICIAR - eSemneaza il detecteaza singur (extract_tags=True
            # mai jos) si calculeaza pozitia reala a campului, fara sa
            # ghicim noi coordonate fixe.
            pdf_bytes = contract_mod.genereaza_pdf(
                continut, tag_semnatura_esemneaza=True)
            try:
                file_name = esemneaza.upload_document(
                    ESEMNEAZA_API_KEY, pdf_bytes, f"contract-{contract['numar']}.pdf")
                # Semnatura PRESTATORULUI (VML) e deja inclusa in textul
                # contractului la generare (vezi contract.genereaza_text) -
                # doar BENEFICIARUL semneaza efectiv prin eSemneaza.
                rezultat = esemneaza.create_sign_request(
                    ESEMNEAZA_API_KEY, file_name,
                    recipients=[{"email": admin["email"], "name": firm["name"]}],
                    sender_name="e-TVA Reconciliere", extract_tags=True)
            except esemneaza.EsemneazaError as e:
                return redirect(url_for(
                    "vezi_contract",
                    eroare=f"Nu am putut trimite contractul spre semnare: {e}"))
            conn.execute(
                "UPDATE contracts SET metoda_semnatura=?, esemneaza_request_id=? "
                "WHERE id=?",
                (pdb.CONTRACT_METODA_ESEMNEAZA, rezultat.get("id"), contract["id"]))
            conn.commit()
            audit_fc = firm_conn(active_firm_id)
            audit.log(audit_fc, user["username"], "contract.trimis_spre_semnare",
                      "contract", str(contract["id"]))
            return redirect(url_for(
                "vezi_contract",
                mesaj=f"Am trimis contractul spre semnare la {admin['email']} - "
                     f"verifica emailul primit de la eSemneaza.ro."))
        elif metoda == pdb.CONTRACT_METODA_CERTIFICAT:
            fisier = request.files.get("semnatura_fisier")
            if fisier is None or not fisier.filename:
                return redirect(url_for(
                    "vezi_contract", eroare="Incarca fisierul PDF semnat cu certificatul tau."))
            pdf_bytes = fisier.read()
            try:
                verificare = digital_signature.verifica_semnatura_pdf(pdf_bytes)
            except digital_signature.SignatureVerificationError as e:
                return redirect(url_for("vezi_contract", eroare=str(e)))
            if not verificare["valid"]:
                return redirect(url_for(
                    "vezi_contract",
                    eroare=f"Semnatura nu a putut fi validata: {verificare['eroare']}"))
            conn.execute(
                "UPDATE contracts SET stare=?, metoda_semnatura=?, "
                "semnatura_verificata=?, semnatura_detalii=?, "
                "semnat_la=? WHERE id=?",
                (pdb.CONTRACT_STARE_SEMNAT, pdb.CONTRACT_METODA_CERTIFICAT,
                 1 if verificare["trusted"] else 0,
                 json.dumps(verificare), acum, contract["id"]))
            conn.commit()
        else:
            return redirect(url_for(
                "vezi_contract", eroare="Alege o metoda de semnatura valida."))

        audit_fc = firm_conn(active_firm_id)
        audit.log(audit_fc, user["username"], "contract.semnare",
                  "contract", str(contract["id"]))
        return redirect(url_for(
            "vezi_contract", mesaj="Contractul a fost semnat cu succes."))

    @app.post("/api/esemneaza/webhook")
    @csrf.exempt
    def webhook_esemneaza():
        """Cale alternativa/mai rapida decat polling-ul din
        _verifica_finalizare_esemneaza - functioneaza doar odata ce serverul
        are o adresa publica configurata in contul eSemneaza (Setari API ->
        URL Webhook), ceea ce nu e inca cazul in dev/testare. Forma exacta a
        payload-ului NU e documentata nicaieri (nu exista o pagina de
        referinta pentru webhook-uri) - scris defensiv, incearca cateva nume
        de campuri plauzibile si nu se prabuseste niciodata pe o forma
        neasteptata, fiindca fluxul functioneaza oricum si fara el."""
        if (not ESEMNEAZA_WEBHOOK_SECRET
                or request.headers.get(ESEMNEAZA_WEBHOOK_HEADER) != ESEMNEAZA_WEBHOOK_SECRET):
            return jsonify({"error": "Neautorizat"}), 401
        payload = request.get_json(silent=True) or {}
        request_id = (payload.get("requestId") or payload.get("id")
                     or payload.get("request_id"))
        eveniment = payload.get("event") or payload.get("type") or payload.get("eventType")
        if not request_id:
            return jsonify({"ok": True})
        contract = conn.execute(
            "SELECT * FROM contracts WHERE esemneaza_request_id=?",
            (request_id,)).fetchone()
        if contract is None or contract["stare"] != pdb.CONTRACT_STARE_IN_ASTEPTARE:
            return jsonify({"ok": True})
        if eveniment in ("RECIPIENT_SIGNED", "REQUEST_COMPLETED"):
            _finalizeaza_contract_esemneaza(contract, request_id)
        elif eveniment == "RECIPIENT_REJECTED":
            conn.execute(
                "UPDATE contracts SET esemneaza_request_id=NULL WHERE id=?",
                (contract["id"],))
            conn.commit()
        return jsonify({"ok": True})

    @app.post("/panou/contract/reziliaza")
    def reziliaza_contract():
        user = current_user()
        active_firm_id = session.get("active_firm_id")
        if (user is None or user["is_master"]
                or _role_in_firm(user["id"], active_firm_id) != "admin"):
            return redirect(url_for("login"))
        contract = _contract_curent(active_firm_id)
        if contract is None or contract["stare"] != pdb.CONTRACT_STARE_SEMNAT:
            return redirect(url_for(
                "vezi_contract", eroare="Nu exista niciun contract semnat activ."))
        conn.execute(
            "UPDATE contracts SET stare=?, reziliere_solicitata_la=? WHERE id=?",
            (pdb.CONTRACT_STARE_REZILIERE_SOLICITATA,
             datetime.now(timezone.utc).isoformat(), contract["id"]))
        conn.commit()
        audit_fc = firm_conn(active_firm_id)
        audit.log(audit_fc, user["username"], "contract.reziliere_solicitata",
                  "contract", str(contract["id"]))
        return redirect(url_for(
            "vezi_contract",
            mesaj="Cererea de reziliere a fost inregistrata - va fi procesata "
                 "de echipa noastra."))

    @app.post("/panou/comutare-firma")
    def switch_firm():
        user = current_user()
        if user is None or user["is_master"]:
            return redirect(url_for("login"))
        firm_id = request.form.get("firm_id", type=int)
        if firm_id is None or not _role_in_firm(user["id"], firm_id):
            return redirect(url_for("panou"))
        session["active_firm_id"] = firm_id
        # Confirmare explicita, nu doar un reload tacut - utila mai ales cand
        # schimbarea vine din dropdown-ul rapid, departe de tabelul cu
        # detalii, unde altfel n-ar fi evident ca s-a schimbat ceva.
        firma = conn.execute(
            "SELECT name FROM firms WHERE id=?", (firm_id,)).fetchone()
        mesaj = f"Acum lucrezi cu {firma['name']}." if firma else None
        return redirect(url_for("panou", mesaj=mesaj))

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

    @app.post("/panou/cerere-stergere")
    def cerere_stergere():
        """Cerere de stergere a datelor contului, cu acordul explicit al
        utilizatorului - masterul o rezolva (vezi finalizeaza_cerere_stergere)
        in cel mult DELETION_TERMEN_ZILE de la data acesteia, conform
        politicii de confidentialitate publicate."""
        user = current_user()
        if user is None or user["is_master"]:
            return redirect(url_for("login"))
        if not request.form.get("accept"):
            return redirect(url_for(
                "panou", eroare="Trebuie sa bifezi acordul pentru a solicita "
                                "stergerea datelor contului."))
        deja_in_asteptare = conn.execute(
            "SELECT 1 FROM deletion_requests WHERE user_id=? AND stare=?",
            (user["id"], pdb.DELETION_STARE_IN_ASTEPTARE)).fetchone()
        if deja_in_asteptare:
            return redirect(url_for("panou"))
        active_firm_id = session.get("active_firm_id")
        firma = (conn.execute("SELECT name FROM firms WHERE id=?",
                              (active_firm_id,)).fetchone()
                if active_firm_id else None)
        acum = datetime.now()
        termen = acum + timedelta(days=pdb.DELETION_TERMEN_ZILE)
        conn.execute(
            "INSERT INTO deletion_requests(user_id, username, firm_id, "
            "firm_name, creat_la, termen_la, stare) VALUES(?,?,?,?,?,?,?)",
            (user["id"], user["username"], active_firm_id,
             firma["name"] if firma else None, acum.isoformat(),
             termen.isoformat(), pdb.DELETION_STARE_IN_ASTEPTARE))
        conn.commit()
        return redirect(url_for(
            "panou", mesaj="Cererea de stergere a fost inregistrata. Datele "
                          f"contului tau vor fi sterse pana cel tarziu la "
                          f"{termen.strftime('%Y-%m-%d')}."))

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
        n_mesaje_necitite = conn.execute(
            "SELECT COUNT(*) AS n FROM contact_messages WHERE citit=0"
        ).fetchone()["n"]
        n_cereri_in_asteptare = conn.execute(
            "SELECT COUNT(*) AS n FROM deletion_requests WHERE stare=?",
            (pdb.DELETION_STARE_IN_ASTEPTARE,)).fetchone()["n"]
        n_cereri_intarziate = conn.execute(
            "SELECT COUNT(*) AS n FROM deletion_requests WHERE stare=? "
            "AND termen_la<?", (pdb.DELETION_STARE_IN_ASTEPTARE,
                                datetime.now().isoformat())).fetchone()["n"]
        return render_template("master.html", user=user, firms=firms,
                               versiune=pipeline.running_vs_current(),
                               n_mesaje_necitite=n_mesaje_necitite,
                               n_cereri_in_asteptare=n_cereri_in_asteptare,
                               n_cereri_intarziate=n_cereri_intarziate)

    @app.post("/master/firma/<int:firm_id>/comutare")
    def toggle_firm(firm_id):
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        firma = conn.execute("SELECT * FROM firms WHERE id=?", (firm_id,)).fetchone()
        conn.execute("UPDATE firms SET active = 1 - active WHERE id=?", (firm_id,))
        conn.commit()
        if firma is not None:
            stare_noua = "dezactivata" if firma["active"] else "activata"
            _log_master_action(user, "firma.comutare",
                               f"{firma['name']} (CUI {firma['cui']}) -> {stare_noua}")
        return redirect(url_for("master"))

    @app.get("/master/statistici")
    def master_statistici():
        """Vedere de business peste toate firmele - cate sunt active, cate
        platesc deja vs. inca in proba, un MRR estimat (nu real - nu exista
        inca procesator de plati, e doar preturile din nomenclator aplicate
        firmelor active cu ciclu ales) si incasarile validate real pana
        acum, plus inscrieri saptamanale ca semnal de crestere."""
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        firms_all = conn.execute("SELECT * FROM firms").fetchall()
        n_total = len(firms_all)
        n_active = sum(1 for f in firms_all if f["active"])
        n_cu_ciclu = sum(1 for f in firms_all if f["ciclu_facturare"])

        preturi = pdb.get_preturi(conn)
        mrr = 0.0
        for f in firms_all:
            if not f["active"] or not f["ciclu_facturare"]:
                continue
            pret_lunar = preturi[f["tip"]][f["ciclu_facturare"]]
            if f["tip"] == pdb.FIRM_TIP_CONTABILITATE:
                n_clienti = firm_conn(f["id"]).execute(
                    "SELECT COUNT(*) AS n FROM clients").fetchone()["n"]
                mrr += pret_lunar * max(n_clienti, 1)
            else:
                mrr += pret_lunar
        mrr = round(mrr, 2)

        incasari_totale = conn.execute(
            "SELECT COALESCE(SUM(suma), 0) AS s FROM payments WHERE stare=?",
            (pdb.PLATA_VALIDATA,)).fetchone()["s"]

        firm_tip_dist = _donut_segments([
            ("Contabilitate", sum(1 for f in firms_all
                                  if f["tip"] == pdb.FIRM_TIP_CONTABILITATE)),
            ("Direct", sum(1 for f in firms_all
                           if f["tip"] == pdb.FIRM_TIP_DIRECT)),
        ])
        stare_dist = _donut_segments([
            ("Platitoare (ciclu ales)", n_cu_ciclu),
            ("In perioada de proba", n_total - n_cu_ciclu),
        ])

        # Inscrieri pe saptamana, ultimele 12 saptamani - firmele fara
        # creat_la (inregistrate inainte ca aceasta coloana sa existe) nu
        # pot fi plasate pe axa timpului, deci sunt omise, nu ghicite.
        acum = datetime.now(timezone.utc)
        saptamani = []
        for i in range(11, -1, -1):
            inceput = (acum - timedelta(weeks=i + 1)).isoformat()
            sfarsit = (acum - timedelta(weeks=i)).isoformat()
            n = sum(1 for f in firms_all
                   if f["creat_la"] and inceput <= f["creat_la"] < sfarsit)
            saptamani.append({
                "eticheta": (acum - timedelta(weeks=i)).strftime("%d.%m"), "n": n})
        max_saptamana = max((s["n"] for s in saptamani), default=0)
        for s in saptamani:
            s["bar_pct"] = _bar_pct(s["n"], max_saptamana)

        return render_template(
            "master_statistici.html", user=user,
            n_total=n_total, n_active=n_active, n_inactive=n_total - n_active,
            n_cu_ciclu=n_cu_ciclu, n_in_proba=n_total - n_cu_ciclu,
            mrr=mrr, incasari_totale=incasari_totale,
            firm_tip_dist=firm_tip_dist, stare_dist=stare_dist,
            saptamani=saptamani)

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

    @app.get("/master/firme/<int:firm_id>/istoric.xml")
    def master_firma_istoric_xml(firm_id):
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        firma = conn.execute("SELECT * FROM firms WHERE id=?", (firm_id,)).fetchone()
        if firma is None:
            return redirect(url_for("master"))
        evenimente = audit.entries(firm_conn(firm_id), limit=1000000)
        root = ET.Element("istoric_firma", firma=firma["name"], cui=firma["cui"])
        for e in evenimente:
            actiune = ET.SubElement(root, "actiune")
            ET.SubElement(actiune, "data").text = e["ts"]
            ET.SubElement(actiune, "tip").text = e["action"]
            ET.SubElement(actiune, "utilizator").text = e["user_id"]
            if e.get("entity"):
                ET.SubElement(actiune, "entitate").text = str(e["entity"])
            if e.get("entity_id"):
                ET.SubElement(actiune, "entitate_id").text = str(e["entity_id"])
        xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        return Response(
            xml_bytes, mimetype="application/xml",
            headers={"Content-Disposition":
                     f'attachment; filename="istoric_{_slugify(firma["name"])}.xml"'})

    def _istoric_master() -> list[dict]:
        """Actiunile masterului insusi - propriile lui actiuni de administrare
        (master_actions), plus promovarile intre medii (deja logate separat
        in pipeline_log de multa vreme) - unite intr-un singur istoric,
        cele mai recente primele."""
        evenimente = [
            {"ts": r["creat_la"], "action": r["actiune"], "detalii": r["detalii"] or ""}
            for r in conn.execute(
                "SELECT actiune, detalii, creat_la FROM master_actions "
                "ORDER BY id DESC")]
        for p in pipeline.history(conn, limit=1000000):
            evenimente.append({
                "ts": p["promoted_at"], "action": "pipeline.promovare",
                "detalii": f"{p['source_env']} -> {p['target_env']} "
                          f"(commit {p['commit_hash']})"})
        evenimente.sort(key=lambda e: e["ts"], reverse=True)
        return evenimente

    @app.get("/master/istoric")
    def master_istoric_propriu():
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        return render_template("master_istoric_propriu.html", user=user,
                               evenimente=_istoric_master())

    @app.get("/master/istoric.xml")
    def master_istoric_propriu_xml():
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        root = ET.Element("istoric_master", utilizator=user["username"])
        for e in _istoric_master():
            actiune = ET.SubElement(root, "actiune")
            ET.SubElement(actiune, "data").text = e["ts"]
            ET.SubElement(actiune, "tip").text = e["action"]
            if e.get("detalii"):
                ET.SubElement(actiune, "detalii").text = str(e["detalii"])
        xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        return Response(
            xml_bytes, mimetype="application/xml",
            headers={"Content-Disposition":
                     f'attachment; filename="istoric_{user["username"]}.xml"'})

    # ---------- master: anunturi in mediul de productie ----------
    ANUNT_ETICHETE = {
        pdb.ANUNT_TIP_MENTENANTA: "Mentenanță",
        pdb.ANUNT_TIP_INCIDENT: "Incident",
        pdb.ANUNT_TIP_LANSARE: "Lansare",
        pdb.ANUNT_TIP_INFORMATIV: "Informativ",
    }

    def _anunt_activ():
        """Cel mai recent anunt activ chiar acum (in fereastra lui de timp),
        daca exista - afisat ca banner tuturor utilizatorilor autentificati.
        Orele sunt luate ca atare (ora locala a serverului), fara conversie
        de fus orar - masterul seteaza fereastra in ora lui, pe acelasi
        calculator pe care ruleaza si serverul."""
        acum = datetime.now().isoformat()
        return conn.execute(
            "SELECT * FROM announcements WHERE activ=1 "
            "AND incepe_la<=? AND se_termina_la>=? "
            "ORDER BY incepe_la DESC LIMIT 1", (acum, acum)).fetchone()

    @app.get("/master/anunturi")
    def master_anunturi():
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        anunturi = conn.execute(
            "SELECT * FROM announcements ORDER BY incepe_la DESC").fetchall()
        return render_template("master_anunturi.html", user=user,
                               anunturi=anunturi, tipuri=pdb.ANUNT_TIPURI,
                               etichete=ANUNT_ETICHETE, acum=datetime.now().isoformat(),
                               eroare=request.args.get("eroare"))

    @app.post("/master/anunturi")
    def creeaza_anunt():
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        mesaj = request.form.get("mesaj", "").strip()
        tip = request.form.get("tip", "")
        incepe_la = request.form.get("incepe_la", "")
        se_termina_la = request.form.get("se_termina_la", "")
        if not mesaj or tip not in pdb.ANUNT_TIPURI or not incepe_la or not se_termina_la:
            return redirect(url_for(
                "master_anunturi", eroare="Toate campurile sunt obligatorii."))
        try:
            inceput_dt = datetime.fromisoformat(incepe_la)
            sfarsit_dt = datetime.fromisoformat(se_termina_la)
        except ValueError:
            return redirect(url_for(
                "master_anunturi", eroare="Data sau ora introdusa nu este valida."))
        if sfarsit_dt <= inceput_dt:
            return redirect(url_for(
                "master_anunturi", eroare="Sfarsitul trebuie sa fie dupa inceput."))
        conn.execute(
            "INSERT INTO announcements(mesaj, tip, incepe_la, se_termina_la, "
            "creat_de, creat_la) VALUES(?,?,?,?,?,?)",
            (mesaj, tip, inceput_dt.isoformat(), sfarsit_dt.isoformat(),
             user["username"], datetime.now().isoformat()))
        conn.commit()
        _log_master_action(user, "anunt.creare", f"{tip}: {mesaj[:80]}")
        return redirect(url_for("master_anunturi"))

    @app.post("/master/anunturi/<int:anunt_id>/dezactivare")
    def dezactiveaza_anunt(anunt_id):
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        conn.execute("UPDATE announcements SET activ=0 WHERE id=?", (anunt_id,))
        conn.commit()
        _log_master_action(user, "anunt.dezactivare", f"anunt #{anunt_id}")
        return redirect(url_for("master_anunturi"))

    @app.get("/api/anunt-activ")
    def anunt_activ_api():
        """Public in interiorul aplicatiei autentificate - orice cont poate
        intreba daca e vreun anunt activ acum, ca sa arate bannerul."""
        if current_user() is None:
            return jsonify(None)
        anunt = _anunt_activ()
        if anunt is None:
            return jsonify(None)
        return jsonify({"mesaj": anunt["mesaj"], "tip": anunt["tip"],
                        "eticheta": ANUNT_ETICHETE.get(anunt["tip"], anunt["tip"])})

    # ---------- formular de contact ----------
    CONTACT_ETICHETE = {
        pdb.CONTACT_TIP_GENERAL: "Întrebare generală",
        pdb.CONTACT_TIP_SUPORT: "Suport tehnic / cont",
        pdb.CONTACT_TIP_FACTURARE: "Facturare",
        pdb.CONTACT_TIP_GDPR: "Datele mele personale (GDPR)",
        pdb.CONTACT_TIP_RECLAMATIE: "Reclamație",
        pdb.CONTACT_TIP_ALTELE: "Altele",
    }

    def _trimite_email(destinatar: str, subiect: str, continut: str,
                       reply_to: str | None = None) -> None:
        """Trimite un email simplu prin SMTP, daca serverul are configurate
        variabilele de mediu SMTP_HOST/SMTP_USER/SMTP_PASSWORD - altfel nu
        face nimic. Apelantii care depind de livrare (verificarea de email)
        trebuie sa tina cont ca fara SMTP configurat, aceasta functie e un
        no-op tacut - vezi EMAIL_VERIFICARE_OBLIGATORIE."""
        host = os.environ.get("SMTP_HOST")
        if not host:
            return
        try:
            msg = EmailMessage()
            msg["Subject"] = subiect
            msg["From"] = os.environ.get("SMTP_FROM", CONTACT_EMAIL_TO)
            msg["To"] = destinatar
            if reply_to:
                msg["Reply-To"] = reply_to
            msg.set_content(continut)
            port = int(os.environ.get("SMTP_PORT", "587"))
            with smtplib.SMTP(host, port, timeout=10) as s:
                s.starttls()
                user = os.environ.get("SMTP_USER")
                password = os.environ.get("SMTP_PASSWORD")
                if user and password:
                    s.login(user, password)
                s.send_message(msg)
        except (smtplib.SMTPException, OSError) as e:
            app.logger.warning("Trimiterea emailului catre %s a esuat: %s", destinatar, e)

    def _trimite_email_contact(nume, email, tip, mesaj):
        """Retransmite mesajul catre CONTACT_EMAIL_TO - mesajul e oricum
        pastrat in contact_messages, trimiterea prin email e un bonus de
        notificare, nu singura cale prin care ajunge la master."""
        _trimite_email(
            CONTACT_EMAIL_TO, f"[e-TVA Contact] {CONTACT_ETICHETE.get(tip, tip)}",
            f"De la: {nume} <{email}>\nTema: {CONTACT_ETICHETE.get(tip, tip)}\n\n{mesaj}",
            reply_to=email)

    def _trimite_email_verificare(email: str, nume_firma: str, token: str) -> None:
        """Spre deosebire de _trimite_email_contact, aici emailul e singura
        cale prin care clientul primeste link-ul - de asta accesul nu se
        blocheaza real (vezi EMAIL_VERIFICARE_OBLIGATORIE) pana nu e
        confirmat ca SMTP-ul chiar livreaza pe serverul de productie."""
        link = url_for("verifica_email", token=token, _external=True)
        _trimite_email(
            email, "Confirma contul e-TVA Reconciliere",
            f"Salut,\n\nCa sa activezi contul firmei {nume_firma} pe "
            f"e-TVA Reconciliere, confirma adresa de email accesand linkul "
            f"de mai jos:\n\n{link}\n\nDaca nu ai creat tu acest cont, "
            f"poti ignora acest mesaj.")

    @app.post("/api/contact")
    @csrf.exempt
    def trimite_contact():
        """Disponibil atat pentru vizitatori anonimi (pagina publica de
        contact), cat si pentru conturi autentificate (panoul contului) -
        acelasi endpoint, acelasi tabel, aceeasi retransmitere prin email.
        Exceptat de la CSRF: docs/contact.html e servit static (send_file),
        fara acces la un token randat de Jinja - vizitatorul anonim nu are
        cum sa primeasca unul."""
        date = request.get_json(silent=True) or request.form
        nume = (date.get("nume") or "").strip()
        email = (date.get("email") or "").strip()
        tip = (date.get("tip") or "").strip()
        mesaj = (date.get("mesaj") or "").strip()
        if not nume or not email or not mesaj or tip not in pdb.CONTACT_TIPURI:
            return jsonify({"eroare": "Completeaza toate campurile obligatorii."}), 400
        if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
            return jsonify({"eroare": "Adresa de email nu pare valida."}), 400
        user = current_user()
        ident = current_identity()
        trimis_de = user["username"] if user else None
        firma = ident["firm_name"] if ident else None
        conn.execute(
            "INSERT INTO contact_messages(nume, email, tip, mesaj, trimis_de, "
            "firma, creat_la) VALUES(?,?,?,?,?,?,?)",
            (nume, email, tip, mesaj, trimis_de, firma, datetime.now().isoformat()))
        conn.commit()
        _trimite_email_contact(nume, email, tip, mesaj)
        return jsonify({"ok": True})

    @app.get("/master/mesaje")
    def master_mesaje():
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        mesaje = conn.execute(
            "SELECT * FROM contact_messages ORDER BY citit ASC, creat_la DESC"
        ).fetchall()
        return render_template("master_mesaje.html", user=user, mesaje=mesaje,
                               etichete=CONTACT_ETICHETE)

    @app.post("/master/mesaje/<int:mesaj_id>/citit")
    def marcheaza_mesaj_citit(mesaj_id):
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        conn.execute("UPDATE contact_messages SET citit=1 WHERE id=?", (mesaj_id,))
        conn.commit()
        _log_master_action(user, "mesaj.citire", f"mesaj #{mesaj_id}")
        return redirect(url_for("master_mesaje"))

    # ---------- master: cereri de stergere a datelor ----------
    @app.get("/master/cereri-stergere")
    def master_cereri_stergere():
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        acum = datetime.now()
        rows = conn.execute(
            "SELECT * FROM deletion_requests ORDER BY "
            "(stare=?) DESC, creat_la DESC",
            (pdb.DELETION_STARE_IN_ASTEPTARE,)).fetchall()
        cereri = []
        for r in rows:
            zile_ramase = (datetime.fromisoformat(r["termen_la"]) - acum).days
            cereri.append({**dict(r), "zile_ramase": zile_ramase,
                           "intarziata": r["stare"] == pdb.DELETION_STARE_IN_ASTEPTARE
                                        and zile_ramase < 0})
        return render_template("master_cereri_stergere.html", user=user, cereri=cereri)

    @app.post("/master/cereri-stergere/<int:cerere_id>/finalizare")
    def finalizeaza_cerere_stergere(cerere_id):
        """Anonimizeaza contul (username -> marker anonim, parola invalidata,
        cont dezactivat, scos din toate firmele) - jurnalul de audit ramane
        neschimbat, pastrat permanent, conform politicii de confidentialitate
        (referinte istorice la vechiul username raman, cum e normal intr-un
        jurnal de audit)."""
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        cerere = conn.execute("SELECT * FROM deletion_requests WHERE id=?",
                              (cerere_id,)).fetchone()
        if cerere is None or cerere["stare"] != pdb.DELETION_STARE_IN_ASTEPTARE:
            return redirect(url_for("master_cereri_stergere"))
        anonim = f"utilizator-sters-{cerere['user_id']}"
        conn.execute(
            "UPDATE users SET username=?, pw_hash=?, active=0 WHERE id=?",
            (anonim, psec.hash_password(secrets.token_hex(32)), cerere["user_id"]))
        conn.execute("UPDATE user_firms SET active=0 WHERE user_id=?",
                     (cerere["user_id"],))
        conn.execute(
            "UPDATE deletion_requests SET stare=?, procesat_la=?, procesat_de=? "
            "WHERE id=?",
            (pdb.DELETION_STARE_FINALIZATA, datetime.now().isoformat(),
             user["username"], cerere_id))
        conn.commit()
        _log_master_action(user, "cerere_stergere.finalizare",
                           f"cont #{cerere['user_id']} ({cerere['username']})")
        return redirect(url_for("master_cereri_stergere"))

    @app.post("/master/cereri-stergere/<int:cerere_id>/anulare")
    def anuleaza_cerere_stergere(cerere_id):
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        cerere = conn.execute("SELECT * FROM deletion_requests WHERE id=?",
                              (cerere_id,)).fetchone()
        if cerere is None or cerere["stare"] != pdb.DELETION_STARE_IN_ASTEPTARE:
            return redirect(url_for("master_cereri_stergere"))
        conn.execute(
            "UPDATE deletion_requests SET stare=?, procesat_la=?, procesat_de=? "
            "WHERE id=?",
            (pdb.DELETION_STARE_ANULATA, datetime.now().isoformat(),
             user["username"], cerere_id))
        conn.commit()
        _log_master_action(user, "cerere_stergere.anulare",
                           f"cont #{cerere['user_id']} ({cerere['username']})")
        return redirect(url_for("master_cereri_stergere"))

    # ---------- master: facturare ----------
    @app.get("/master/facturi")
    def master_facturi():
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        facturi = conn.execute(
            "SELECT * FROM invoices ORDER BY serie DESC, numar DESC").fetchall()
        firme = conn.execute(
            "SELECT id, name, cui FROM firms WHERE active=1 ORDER BY name"
        ).fetchall()
        return render_template("master_facturi.html", user=user, facturi=facturi,
                               firme=firme, eroare=request.args.get("eroare"),
                               mesaj=request.args.get("mesaj"),
                               mediu_anaf=ANAF_EFACTURA_MEDIU)

    def _suma_scurta(valoare: float) -> str:
        return f"{valoare:.2f}"

    @app.post("/master/facturi")
    def creeaza_factura():
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        f = request.form
        firm_id = f.get("firm_id", type=int)
        descriere = f.get("descriere", "").strip()
        valoare_neta = f.get("valoare_neta", type=float)
        cota_tva = f.get("cota_tva", type=float)
        perioada_inceput = f.get("perioada_inceput", "").strip() or None
        perioada_sfarsit = f.get("perioada_sfarsit", "").strip() or None
        data_scadentei = f.get("data_scadentei", "").strip() or None
        if (not firm_id or not descriere or valoare_neta is None
                or valoare_neta <= 0 or cota_tva is None or cota_tva < 0):
            return redirect(url_for(
                "master_facturi",
                eroare="Firma, descrierea si o valoare neta pozitiva sunt obligatorii."))
        firma = conn.execute("SELECT * FROM firms WHERE id=?", (firm_id,)).fetchone()
        if firma is None:
            return redirect(url_for("master_facturi", eroare="Firma nu a fost gasita."))
        numar = invoicing.next_invoice_number(conn, pdb.FACTURA_SERIE)
        valoare_tva = round(valoare_neta * cota_tva / 100, 2)
        valoare_totala = round(valoare_neta + valoare_tva, 2)
        acum = datetime.now()
        conn.execute(
            "INSERT INTO invoices(serie, numar, firm_id, firm_name, firm_cui, "
            "descriere, perioada_inceput, perioada_sfarsit, data_emiterii, "
            "data_scadentei, valoare_neta, cota_tva, valoare_tva, "
            "valoare_totala, creat_de, creat_la) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pdb.FACTURA_SERIE, numar, firm_id, firma["name"], firma["cui"],
             descriere, perioada_inceput, perioada_sfarsit, acum.isoformat(),
             data_scadentei, valoare_neta, cota_tva, valoare_tva,
             valoare_totala, user["username"], acum.isoformat()))
        conn.commit()
        _log_master_action(
            user, "factura.emitere",
            f"{pdb.FACTURA_SERIE} {numar} -> {firma['name']} "
            f"({_suma_scurta(valoare_totala)} RON)")
        return redirect(url_for("master_facturi"))

    @app.get("/master/facturi/<int:factura_id>/pdf")
    def descarca_factura_pdf(factura_id):
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        factura = conn.execute("SELECT * FROM invoices WHERE id=?",
                               (factura_id,)).fetchone()
        if factura is None:
            return redirect(url_for("master_facturi"))
        pdf_bytes = invoicing.generate_pdf(dict(factura))
        nume_fisier = f"factura_{factura['serie']}{factura['numar']}.pdf"
        return Response(
            pdf_bytes, mimetype="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{nume_fisier}"'})

    @app.get("/master/facturi/<int:factura_id>/xml")
    def descarca_factura_xml(factura_id):
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        factura = conn.execute("SELECT * FROM invoices WHERE id=?",
                               (factura_id,)).fetchone()
        if factura is None:
            return redirect(url_for("master_facturi"))
        xml_bytes = efactura_xml.build_invoice_xml(dict(factura), invoicing.FURNIZOR)
        nume_fisier = f"factura_{factura['serie']}{factura['numar']}.xml"
        return Response(
            xml_bytes, mimetype="application/xml",
            headers={"Content-Disposition": f'attachment; filename="{nume_fisier}"'})

    def _vml_firm_id():
        """Firma emitentului insusi (VML) trebuie sa fie inregistrata ca
        firma in platforma si sa fi trecut prin /panou/anaf/autorizare cu
        propriul ei certificat digital, la fel ca orice alta firma - nu
        exista un mecanism separat de autorizare doar pentru emitere."""
        row = conn.execute("SELECT id FROM firms WHERE cui=?",
                           (invoicing.FURNIZOR["cui"],)).fetchone()
        return row["id"] if row else None

    @app.post("/master/facturi/<int:factura_id>/trimite-anaf")
    def trimite_factura_anaf(factura_id):
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        factura = conn.execute("SELECT * FROM invoices WHERE id=?",
                               (factura_id,)).fetchone()
        if factura is None:
            return redirect(url_for("master_facturi"))
        vml_firm_id = _vml_firm_id()
        access_token = get_valid_anaf_access_token(vml_firm_id) if vml_firm_id else None
        if access_token is None:
            return redirect(url_for(
                "master_facturi",
                eroare="Contul VML (emitentul) nu are acces ANAF autorizat inca "
                      "- autorizeaza-l din panoul acelei firme, la fel ca la "
                      "orice alta firma."))
        xml_bytes = efactura_xml.build_invoice_xml(dict(factura), invoicing.FURNIZOR)
        cif = str(anaf_cui.normalize_cui(invoicing.FURNIZOR["cui"]))
        try:
            rezultat = anaf_oauth.upload_invoice(
                access_token, cif, xml_bytes, mediu=ANAF_EFACTURA_MEDIU)
        except anaf_oauth.AnafOAuthError as e:
            return redirect(url_for("master_facturi", eroare=f"ANAF: {e}"))
        conn.execute(
            "UPDATE invoices SET anaf_index_incarcare=?, anaf_stare=?, "
            "anaf_trimis_la=? WHERE id=?",
            (rezultat["index_incarcare"], pdb.EFACTURA_IN_PROCESARE,
             datetime.now().isoformat(), factura_id))
        conn.commit()
        _log_master_action(
            user, "factura.trimitere_anaf",
            f"{factura['serie']} {factura['numar']} -> index "
            f"{rezultat['index_incarcare']} ({ANAF_EFACTURA_MEDIU})")
        return redirect(url_for(
            "master_facturi",
            mesaj="Factura a fost trimisa la ANAF. Verifica starea peste "
                  "cateva minute."))

    @app.post("/master/facturi/<int:factura_id>/verifica-stare")
    def verifica_stare_factura_anaf(factura_id):
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        factura = conn.execute("SELECT * FROM invoices WHERE id=?",
                               (factura_id,)).fetchone()
        if factura is None or not factura["anaf_index_incarcare"]:
            return redirect(url_for("master_facturi"))
        vml_firm_id = _vml_firm_id()
        access_token = get_valid_anaf_access_token(vml_firm_id) if vml_firm_id else None
        if access_token is None:
            return redirect(url_for(
                "master_facturi", eroare="Contul VML nu mai are acces ANAF autorizat."))
        try:
            stare = anaf_oauth.check_upload_status(
                access_token, factura["anaf_index_incarcare"], mediu=ANAF_EFACTURA_MEDIU)
        except anaf_oauth.AnafOAuthError as e:
            return redirect(url_for("master_facturi", eroare=f"ANAF: {e}"))
        if stare["stare"] == "in prelucrare":
            return redirect(url_for(
                "master_facturi", mesaj="Factura e inca in procesare la ANAF."))
        noua_stare = (pdb.EFACTURA_ACCEPTATA if stare["stare"] == "ok"
                     else pdb.EFACTURA_RESPINSA)
        raspuns = None
        if stare.get("id_descarcare"):
            try:
                raspuns = anaf_oauth.download_response(
                    access_token, stare["id_descarcare"], mediu=ANAF_EFACTURA_MEDIU)
            except anaf_oauth.AnafOAuthError:
                raspuns = None
        conn.execute(
            "UPDATE invoices SET anaf_stare=?, anaf_id_descarcare=?, "
            "anaf_raspuns=? WHERE id=?",
            (noua_stare, stare.get("id_descarcare"), raspuns, factura_id))
        conn.commit()
        _log_master_action(
            user, "factura.verificare_stare",
            f"{factura['serie']} {factura['numar']} -> {noua_stare}")
        mesaj = ("Factura a fost acceptata de ANAF." if noua_stare == pdb.EFACTURA_ACCEPTATA
                else "Factura a fost respinsa de ANAF - vezi raspunsul descarcat.")
        return redirect(url_for("master_facturi", mesaj=mesaj))

    @app.get("/master/facturi/<int:factura_id>/raspuns-anaf")
    def descarca_raspuns_anaf(factura_id):
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        factura = conn.execute("SELECT * FROM invoices WHERE id=?",
                               (factura_id,)).fetchone()
        if factura is None or not factura["anaf_raspuns"]:
            return redirect(url_for("master_facturi"))
        nume_fisier = f"raspuns_anaf_{factura['serie']}{factura['numar']}.zip"
        return Response(
            factura["anaf_raspuns"], mimetype="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{nume_fisier}"'})

    # ---------- master: backup date productie ----------
    @app.get("/master/backup")
    def master_backup():
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        return render_template(
            "master_backup.html", user=user, backups=backup_mod.list_backups(data_dir),
            mediu=pipeline.own_environment(),
            eroare=request.args.get("eroare"), mesaj=request.args.get("mesaj"))

    @app.post("/master/backup/creeaza")
    def creeaza_backup():
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        try:
            # Already inside the per-request db_lock (see _acquire_db_lock),
            # so portal.db and every open firm connection are quiet for the
            # duration of the zip.
            path = backup_mod.create_backup(data_dir)
            backup_mod.prune_old_backups(data_dir)
        except OSError as e:
            return redirect(url_for("master_backup", eroare=f"Backup esuat: {e}"))
        _log_master_action(user, "backup_creat", path.name)
        marime = round(path.stat().st_size / 1_048_576, 2)
        return redirect(url_for(
            "master_backup", mesaj=f"Backup creat: {path.name} ({marime} MB)."))

    @app.get("/master/backup/<nume>/descarca")
    def descarca_backup(nume):
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        path = backup_mod.backup_path(data_dir, nume)
        if path is None:
            return redirect(url_for("master_backup", eroare="Backup inexistent."))
        return send_file(path, as_attachment=True, download_name=path.name)

    @app.post("/master/backup/restaureaza")
    def restaureaza_backup():
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        if pipeline.own_environment() == "productie":
            return redirect(url_for(
                "master_backup",
                eroare="Restaurarea e dezactivata in productie - ar suprascrie datele live."))
        if request.form.get("confirm") != "da":
            return redirect(url_for(
                "master_backup", eroare="Trebuie sa confirmi explicit inainte de restaurare."))
        fisier = request.files.get("fisier")
        if fisier is None or not fisier.filename:
            return redirect(url_for("master_backup", eroare="Alege un fisier de backup (.zip)."))
        try:
            backup_mod.validate_backup_zip(fisier)
        except backup_mod.BackupError as e:
            return redirect(url_for("master_backup", eroare=f"Restaurare esuata: {e}"))
        try:
            # Safety snapshot of this environment's current state first -
            # already inside the per-request db_lock (see _acquire_db_lock).
            backup_mod.create_backup(data_dir)
            backup_mod.prune_old_backups(data_dir)
        except OSError as e:
            return redirect(url_for("master_backup", eroare=f"Backup de siguranta esuat: {e}"))
        _log_master_action(user, "backup_restaurat", fisier.filename)

        # Restoring overwrites portal.db/firm_*.db on disk, so every open
        # connection this process holds must close first - os.replace()
        # can't swap a file Windows still has open, and even on POSIX a
        # connection left open would keep reading pre-restore content
        # forever without ever really being "stale". Once closed, nothing
        # in this process can touch the database again until it's
        # restarted, so the rest of this response is deliberately a plain
        # page instead of a redirect - a redirect's follow-up GET would
        # hit current_user()'s now-closed connection and crash.
        conn.close()
        for fc in firm_conns.values():
            fc.close()
        firm_conns.clear()
        try:
            backup_mod.restore_backup(data_dir, fisier)
        except (OSError, backup_mod.BackupError) as e:
            return (f"<h1>Restaurare partial esuata</h1><p>{e}</p>"
                   "<p>Conexiunile catre baza de date sunt deja inchise - "
                   "<b>reporneste serverul acestui mediu</b> indiferent de rezultat.</p>", 500)
        return ("<h1>Backup restaurat</h1>"
               "<p>Fisierele au fost inlocuite pe disc. "
               "<b>Reporneste manual serverul acestui mediu</b> ca sa aiba efect - "
               "orice alta actiune in aceasta sesiune va esua pana atunci, pentru ca "
               "aplicatia tine conexiunile catre bazele de date deja deschise.</p>", 200)

    # ---------- master: remindere expirare trial ----------
    @app.get("/master/remindere-trial")
    def master_remindere_trial():
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        firme_brute = conn.execute(
            "SELECT id, name, cui, trial_expira_la, trial_reminder_ultim_prag, "
            "arhivata_la FROM firms WHERE active=1 AND ciclu_facturare IS NULL "
            "AND trial_expira_la IS NOT NULL ORDER BY trial_expira_la").fetchall()
        firme = [dict(f, zile_ramase=remind_mod.zile_ramase_trial(f["trial_expira_la"]))
                for f in firme_brute]
        return render_template(
            "master_remindere_trial.html", user=user, firme=firme,
            praguri=pdb.TRIAL_REMINDER_PRAGURI_ZILE,
            eroare=request.args.get("eroare"), mesaj=request.args.get("mesaj"))

    @app.post("/master/remindere-trial/trimite")
    def trimite_remindere_trial():
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        n = remind_mod.verifica_si_trimite(conn, _trimite_email)
        _log_master_action(user, "remindere_trial_trimise", str(n))
        return redirect(url_for(
            "master_remindere_trial",
            mesaj=f"{n} {'reminder trimis' if n == 1 else 'remindere trimise'}."))

    @app.post("/master/remindere-trial/arhiveaza")
    def arhiveaza_firme_trial():
        """Declanseaza manual arhivarea firmelor al caror trial s-a incheiat
        fara ciclu de facturare ales - acelasi lucru pe care il face oricum
        fir-ul de fundal periodic (vezi start_scheduler), util pentru
        testare/verificare imediata din panoul master."""
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        n = remind_mod.arhiveaza_firme_neplatitoare(conn)
        _log_master_action(user, "firme_arhivate", str(n))
        return redirect(url_for(
            "master_remindere_trial",
            mesaj=f"{n} {'firma arhivata' if n == 1 else 'firme arhivate'}."))

    # ---------- master: validare incasari ----------
    @app.get("/master/plati")
    def master_plati():
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        plati = conn.execute(
            "SELECT p.*, f.name AS firm_name, f.cui AS firm_cui FROM payments p "
            "JOIN firms f ON f.id = p.firm_id ORDER BY p.creat_la DESC").fetchall()
        return render_template(
            "master_plati.html", user=user, plati=plati,
            eroare=request.args.get("eroare"), mesaj=request.args.get("mesaj"))

    @app.post("/master/plati/<int:plata_id>/valideaza")
    def valideaza_plata(plata_id):
        """Confirma manual o incasare (nu exista inca procesare automata -
        vezi TODO FGO in creeaza_cerere_plata) si emite automat factura
        aferenta, reutilizand exact acelasi tabel/numerotare ca facturile
        create manual din /master/facturi."""
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        plata = conn.execute("SELECT * FROM payments WHERE id=?",
                             (plata_id,)).fetchone()
        if plata is None or plata["stare"] == pdb.PLATA_VALIDATA:
            return redirect(url_for(
                "master_plati", eroare="Plata nu exista sau e deja validata."))
        firma = conn.execute("SELECT * FROM firms WHERE id=?",
                             (plata["firm_id"],)).fetchone()
        if firma is None:
            return redirect(url_for("master_plati", eroare="Firma nu a fost gasita."))
        eticheta_ciclu = {"lunar": "lunar", "6luni": "la 6 luni",
                         "an": "anual"}[plata["ciclu_facturare"]]
        numar = invoicing.next_invoice_number(conn, pdb.FACTURA_SERIE)
        # payments.suma e deja suma cu TVA inclus (vezi _suma_cu_tva) - cea
        # chiar ceruta/incasata de la client - asa ca aici desprindem
        # baza/TVA din ea, nu mai adaugam TVA peste, ca sa nu-l numaram de
        # doua ori si valoare_totala sa coincida exact cu ce s-a incasat.
        # Foloseste cota curenta din setari_tva, nu una hardcodata - vezi
        # _suma_cu_tva pentru acelasi motiv.
        cota_tva = pdb.get_cota_tva(conn)
        valoare_totala = plata["suma"]
        valoare_neta = round(valoare_totala / (1 + cota_tva / 100), 2)
        valoare_tva = round(valoare_totala - valoare_neta, 2)
        acum = datetime.now()
        cur = conn.execute(
            "INSERT INTO invoices(serie, numar, firm_id, firm_name, firm_cui, "
            "descriere, data_emiterii, valoare_neta, cota_tva, valoare_tva, "
            "valoare_totala, creat_de, creat_la) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pdb.FACTURA_SERIE, numar, firma["id"], firma["name"], firma["cui"],
             f"Abonament e-TVA Reconciliere - {eticheta_ciclu}",
             acum.isoformat(), valoare_neta, cota_tva, valoare_tva,
             valoare_totala, user["username"], acum.isoformat()))
        invoice_id = cur.lastrowid
        conn.execute(
            "UPDATE payments SET stare=?, validat_de=?, validat_la=?, "
            "invoice_id=? WHERE id=?",
            (pdb.PLATA_VALIDATA, user["username"], acum.isoformat(),
             invoice_id, plata_id))
        # O plata validata e exact "revenirea prin plata" care reactiveaza o
        # firma arhivata (vezi trial_reminders.arhiveaza_firme_neplatitoare) -
        # UPDATE-ul e un no-op sigur daca firma nu era arhivata.
        conn.execute(
            "UPDATE firms SET arhivata_la=NULL WHERE id=?", (firma["id"],))
        conn.commit()
        _log_master_action(
            user, "plata.validare",
            f"{firma['name']} - {eticheta_ciclu} ({_suma_scurta(valoare_totala)} RON) "
            f"-> factura {pdb.FACTURA_SERIE} {numar}")
        return redirect(url_for(
            "master_plati",
            mesaj="Incasarea a fost validata si factura a fost emisa."))

    @app.get("/master/contracte")
    def master_contracte():
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        contracte = conn.execute(
            "SELECT c.*, f.name AS firm_name, f.cui AS firm_cui FROM contracts c "
            "JOIN firms f ON f.id = c.firm_id ORDER BY c.creat_la DESC").fetchall()
        return render_template(
            "master_contracte.html", user=user, contracte=contracte,
            eroare=request.args.get("eroare"), mesaj=request.args.get("mesaj"))

    @app.get("/master/contracte/<int:contract_id>/pdf")
    def descarca_contract_pdf_master(contract_id):
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        contract = conn.execute("SELECT * FROM contracts WHERE id=?",
                                (contract_id,)).fetchone()
        if contract is None:
            return redirect(url_for("master_contracte"))
        pdf_bytes = _regenereaza_pdf_contract(contract)
        return Response(
            pdf_bytes, mimetype="application/pdf",
            headers={"Content-Disposition":
                    f"inline; filename=contract-{contract['numar']}.pdf"})

    @app.get("/master/contracte/<int:contract_id>/xml")
    def descarca_contract_xml_master(contract_id):
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        contract = conn.execute("SELECT * FROM contracts WHERE id=?",
                                (contract_id,)).fetchone()
        if contract is None:
            return redirect(url_for("master_contracte"))
        xml_bytes = contract_mod.date_contract_xml(contract)
        return Response(
            xml_bytes, mimetype="application/xml",
            headers={"Content-Disposition":
                    f'attachment; filename="contract-{contract["numar"]}.xml"'})

    @app.get("/master/contracte/<int:contract_id>/certificat")
    def descarca_certificat_esemneaza_master(contract_id):
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        contract = conn.execute("SELECT * FROM contracts WHERE id=?",
                                (contract_id,)).fetchone()
        if contract is None or not contract["esemneaza_certificate_pdf"]:
            return redirect(url_for("master_contracte"))
        return Response(
            bytes(contract["esemneaza_certificate_pdf"]), mimetype="application/pdf",
            headers={"Content-Disposition":
                    f"inline; filename=certificat-contract-{contract['numar']}.pdf"})

    @app.post("/master/contracte/<int:contract_id>/reziliaza")
    def finalizeaza_reziliere_contract(contract_id):
        """Master proceseaza manual reziliere - fie ceruta de firma, fie
        initiata direct - cu un ramburs care nu poate depasi jumatate din
        suma achitata pentru ciclul curent (CONTRACT_RAMBURS_MAX_PROCENT)."""
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        contract = conn.execute("SELECT * FROM contracts WHERE id=?",
                                (contract_id,)).fetchone()
        if contract is None or contract["stare"] == pdb.CONTRACT_STARE_REZILIAT:
            return redirect(url_for(
                "master_contracte", eroare="Contractul nu exista sau e deja reziliat."))
        try:
            ramburs_procent = float(request.form.get("ramburs_procent", ""))
        except ValueError:
            ramburs_procent = None
        if (ramburs_procent is None or ramburs_procent < 0
                or ramburs_procent > pdb.CONTRACT_RAMBURS_MAX_PROCENT):
            return redirect(url_for(
                "master_contracte",
                eroare=f"Rambursul trebuie sa fie intre 0 si "
                      f"{pdb.CONTRACT_RAMBURS_MAX_PROCENT}%."))
        acum = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE contracts SET stare=?, reziliat_la=?, reziliat_de=?, "
            "ramburs_procent=? WHERE id=?",
            (pdb.CONTRACT_STARE_REZILIAT, acum, user["username"],
             ramburs_procent, contract_id))
        conn.commit()
        _log_master_action(
            user, "contract.reziliere",
            f"contract #{contract['numar']} - ramburs {ramburs_procent:g}%")
        return redirect(url_for(
            "master_contracte", mesaj="Contractul a fost reziliat."))

    @app.get("/master/nomenclator")
    def master_nomenclator():
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        return render_template(
            "master_nomenclator.html", user=user, preturi=pdb.get_preturi(conn),
            cota_tva=pdb.get_cota_tva(conn), istoric_tva=pdb.listeaza_cote_tva(conn),
            eroare=request.args.get("eroare"), mesaj=request.args.get("mesaj"))

    @app.post("/master/nomenclator")
    def salveaza_nomenclator():
        """Actualizeaza nomenclatorul de preturi - o singura pagina cu toate
        combinatiile tip x ciclu, validate integral inainte de a scrie ceva
        (ca o eroare la un camp sa nu lase restul preturilor pe jumatate
        actualizate)."""
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        valori = {}
        for tip in pdb.FIRM_TIPURI:
            for ciclu in pdb.CICLURI_FACTURARE:
                camp = f"pret_{tip}_{ciclu}"
                bruta = request.form.get(camp, "").strip().replace(",", ".")
                try:
                    pret = float(bruta)
                except ValueError:
                    pret = None
                if pret is None or pret <= 0:
                    return redirect(url_for(
                        "master_nomenclator",
                        eroare=f"Pretul pentru {tip}/{ciclu} trebuie sa fie "
                              "un numar pozitiv."))
                valori[(tip, ciclu)] = pret
        for (tip, ciclu), pret in valori.items():
            pdb.set_pret(conn, tip, ciclu, pret, user["username"])
        _log_master_action(
            user, "nomenclator.actualizare",
            ", ".join(f"{tip}/{ciclu}={pret:g}"
                     for (tip, ciclu), pret in valori.items()))
        return redirect(url_for(
            "master_nomenclator", mesaj="Preturile au fost actualizate."))

    @app.post("/master/nomenclator/tva")
    def salveaza_cota_tva():
        """Cota de TVA nu e hardcodata in cod - legea s-a schimbat deja o
        data in timpul acestui proiect (19% -> 21% din 01.08.2025), asa ca
        un master trebuie sa o poata corecta chiar in ziua schimbarii, fara
        sa astepte o livrare de cod (vezi pdb.get_cota_tva/set_cota_tva)."""
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        bruta = request.form.get("cota_tva", "").strip().replace(",", ".")
        try:
            procent = float(bruta)
        except ValueError:
            procent = None
        if procent is None or procent <= 0 or procent >= 100:
            return redirect(url_for(
                "master_nomenclator",
                eroare="Cota de TVA trebuie sa fie un numar intre 0 si 100."))
        pdb.set_cota_tva(conn, procent, user["username"])
        _log_master_action(user, "nomenclator.cota_tva", f"{procent:g}%")
        return redirect(url_for(
            "master_nomenclator", mesaj="Cota de TVA a fost actualizata."))

    @app.post("/master/nomenclator/tva/<int:cota_id>/activeaza")
    def activeaza_cota_tva(cota_id):
        """Reactiveaza o cota din istoric (ex: revenire dupa o greseala) -
        muta marcatorul `activa`, fara sa retasteze procentul."""
        user = current_user()
        if user is None or not user["is_master"]:
            return redirect(url_for("login"))
        if not pdb.activeaza_cota_tva(conn, cota_id, user["username"]):
            return redirect(url_for(
                "master_nomenclator", eroare="Cota de TVA nu a fost gasita."))
        _log_master_action(user, "nomenclator.cota_tva_reactivata", f"id {cota_id}")
        return redirect(url_for(
            "master_nomenclator", mesaj="Cota de TVA a fost reactivata."))

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
    @app.get("/api/csrf-token")
    @require()
    def csrf_token_pentru_spa(ident):
        """web/index.html e servit prin send_file, nu render_template - nu
        poate primi tokenul direct in pagina ca formularele randate de
        Jinja. SPA-ul cere tokenul o singura data si il ataseaza ca header
        X-CSRFToken pe orice apel POST/PUT/DELETE catre /api/*."""
        return jsonify({"csrf_token": generate_csrf()})

    @app.get("/api/me")
    @require()
    def me(ident):
        anaf_autorizat = conn.execute(
            "SELECT 1 FROM anaf_oauth_tokens WHERE firm_id=?",
            (ident["firm_id"],)).fetchone() is not None
        return jsonify({"username": ident["username"], "role": ident["role"],
                        "firm_name": ident["firm_name"],
                        "firm_tip": ident["firm_tip"],
                        "onboarding_completat": ident["onboarding_completat"],
                        "permissions": sorted(ident["permissions"]),
                        "anaf_autorizat": anaf_autorizat})

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
        company_files = request.files.getlist("company_file")
        if not company_files:
            return jsonify({"errors": ["Lipseste jurnalul firmei."]}), 400

        # Decontul ANAF poate veni fie dintr-un fisier incarcat manual (PDF/
        # JSON/CSV/xlsx), fie preluat automat prin OAuth2 (anaf_sursa=auto) -
        # vezi /panou/anaf/autorizare pentru cum obtine firma acel token.
        anaf_sursa = request.form.get("anaf_sursa", "upload")
        anaf_doc = None
        anaf_file = None
        if anaf_sursa == "auto":
            token = get_valid_anaf_access_token(ident["firm_id"])
            if token is None:
                return jsonify({"errors": [
                    "Firma nu are acces ANAF autorizat - autorizeaza din "
                    "panoul contului sau incarca decontul manual."]}), 400
            try:
                an, luna = (int(x) for x in period.split("-"))
            except ValueError:
                return jsonify({"errors": [
                    "Perioada trebuie sa fie in formatul AAAA-LL pentru "
                    "preluarea automata din ANAF."]}), 400
            try:
                anaf_doc = parse_p300_json_data(anaf_oauth.fetch_decont(
                    token, ident["firm_cui"], an, luna))
            except anaf_oauth.AnafOAuthError as e:
                return jsonify({"errors": [str(e)]}), 502
            except NotAnafP300Json as e:
                return jsonify({"errors": [str(e)]}), 400
        else:
            anaf_file = request.files["anaf_file"]
            if anaf_file.filename.lower().endswith((".pdf", ".json")):
                saved_anaf_path = _save_upload(anaf_file)
                try:
                    if anaf_file.filename.lower().endswith(".json"):
                        anaf_doc = parse_p300_json(saved_anaf_path)
                    else:
                        anaf_doc = parse_p300_pdf(saved_anaf_path)
                except (NotAnafP300, NotAnafP300Json) as e:
                    return jsonify({"errors": [str(e)]}), 400

        if anaf_doc is not None:
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

    if enable_trial_reminder_scheduler:
        # Dupa _trimite_email (mai sus in fisier) - fir-ul de fundal are
        # nevoie de closure-ul ei, deja legat de app.logger si SMTP.
        remind_mod.start_scheduler(conn, db_lock, _trimite_email)

    app.portal_conn = conn  # exposed for tests/seeding
    app.portal_secret = secret
    app.get_valid_anaf_access_token = get_valid_anaf_access_token  # exposed for tests
    return app
