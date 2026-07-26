"""Portal database (plain SQLite): users, firms, per-user firm memberships.

A user account (login identity) is independent of any single firm: the
same person can be linked to several firms (SRL/PFA) through user_firms,
each with its own role there ('admin' firm owner, 'manager', 'contabil',
'junior'). 'master' is not a role but a separate is_master flag on the
user, since the platform owner has no firm membership at all. App
permissions per role come from etva.db so both sides stay in sync.
"""
import sqlite3
from datetime import datetime, timezone

from etva.db import PERMISSIONS, DEFAULT_ROLES

ROLE_PERMISSIONS = {
    "admin": list(PERMISSIONS),
    "manager": DEFAULT_ROLES["Manager"],
    "contabil": DEFAULT_ROLES["Contabil"],
    "junior": DEFAULT_ROLES["Junior"],
}

# A firm is either its own taxpayer (self-reconciling PFA/SRL, no clients -
# see etva.clients) or an accounting firm juggling several clients' own
# reconciliations.
FIRM_TIP_DIRECT = "direct"
FIRM_TIP_CONTABILITATE = "contabilitate"
FIRM_TIPURI = (FIRM_TIP_DIRECT, FIRM_TIP_CONTABILITATE)

# Categories a master-posted announcement can have - each gets its own
# banner styling (see master_anunturi.html / the banner partial).
ANUNT_TIP_MENTENANTA = "mentenanta"
ANUNT_TIP_INCIDENT = "incident"
ANUNT_TIP_LANSARE = "lansare"
ANUNT_TIP_INFORMATIV = "informativ"
ANUNT_TIPURI = (ANUNT_TIP_MENTENANTA, ANUNT_TIP_INCIDENT,
               ANUNT_TIP_LANSARE, ANUNT_TIP_INFORMATIV)

# Tema unei cereri trimise prin formularul de contact - determina si carui
# departament/proces ii e relevanta (vezi CONTACT_ETICHETE in app.py).
CONTACT_TIP_GENERAL = "general"
CONTACT_TIP_SUPORT = "suport"
CONTACT_TIP_FACTURARE = "facturare"
CONTACT_TIP_GDPR = "gdpr"
CONTACT_TIP_RECLAMATIE = "reclamatie"
CONTACT_TIP_ALTELE = "altele"
CONTACT_TIPURI = (CONTACT_TIP_GENERAL, CONTACT_TIP_SUPORT, CONTACT_TIP_FACTURARE,
                  CONTACT_TIP_GDPR, CONTACT_TIP_RECLAMATIE, CONTACT_TIP_ALTELE)

# Termenul (in zile) in care o cerere de stergere trebuie rezolvata, conform
# politicii de confidentialitate publicata - vezi finalizeaza_cerere_stergere.
DELETION_TERMEN_ZILE = 30
DELETION_STARE_IN_ASTEPTARE = "in_asteptare"
DELETION_STARE_FINALIZATA = "finalizata"
DELETION_STARE_ANULATA = "anulata"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS firms(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, cui TEXT UNIQUE NOT NULL,
  tip TEXT NOT NULL DEFAULT 'contabilitate',
  active INTEGER NOT NULL DEFAULT 1,
  email_verificat INTEGER NOT NULL DEFAULT 0,
  email_verificare_token TEXT,
  creat_la TEXT,
  trial_expira_la TEXT,
  ciclu_facturare TEXT);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL,
  email TEXT,
  is_master INTEGER NOT NULL DEFAULT 0,
  onboarding_completat INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS user_firms(
  user_id INTEGER NOT NULL REFERENCES users(id),
  firm_id INTEGER NOT NULL REFERENCES firms(id),
  role TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (user_id, firm_id));
CREATE TABLE IF NOT EXISTS firm_keys(
  firm_id INTEGER PRIMARY KEY, wrapped_key BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS pipeline_log(
  id INTEGER PRIMARY KEY, source_env TEXT NOT NULL, target_env TEXT NOT NULL,
  commit_hash TEXT NOT NULL, promoted_by TEXT NOT NULL, promoted_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS announcements(
  id INTEGER PRIMARY KEY AUTOINCREMENT, mesaj TEXT NOT NULL,
  tip TEXT NOT NULL DEFAULT 'informativ',
  incepe_la TEXT NOT NULL, se_termina_la TEXT NOT NULL,
  creat_de TEXT NOT NULL, creat_la TEXT NOT NULL,
  activ INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS contact_messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nume TEXT NOT NULL, email TEXT NOT NULL,
  tip TEXT NOT NULL DEFAULT 'general',
  mesaj TEXT NOT NULL,
  trimis_de TEXT, firma TEXT,
  creat_la TEXT NOT NULL,
  citit INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS master_actions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actiune TEXT NOT NULL, detalii TEXT,
  creat_de TEXT NOT NULL, creat_la TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS deletion_requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL, username TEXT NOT NULL,
  firm_id INTEGER, firm_name TEXT,
  creat_la TEXT NOT NULL, termen_la TEXT NOT NULL,
  stare TEXT NOT NULL DEFAULT 'in_asteptare',
  procesat_la TEXT, procesat_de TEXT);
CREATE TABLE IF NOT EXISTS anaf_oauth_tokens(
  firm_id INTEGER PRIMARY KEY REFERENCES firms(id),
  wrapped_access_token BLOB NOT NULL,
  wrapped_refresh_token BLOB NOT NULL,
  obtinut_la TEXT NOT NULL,
  expira_la TEXT NOT NULL,
  autorizat_de TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS invoices(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  serie TEXT NOT NULL, numar INTEGER NOT NULL,
  firm_id INTEGER NOT NULL REFERENCES firms(id),
  firm_name TEXT NOT NULL, firm_cui TEXT NOT NULL,
  descriere TEXT NOT NULL,
  perioada_inceput TEXT, perioada_sfarsit TEXT,
  data_emiterii TEXT NOT NULL, data_scadentei TEXT,
  valoare_neta REAL NOT NULL, cota_tva REAL NOT NULL DEFAULT 19,
  valoare_tva REAL NOT NULL, valoare_totala REAL NOT NULL,
  moneda TEXT NOT NULL DEFAULT 'RON',
  stare TEXT NOT NULL DEFAULT 'emisa',
  creat_de TEXT NOT NULL, creat_la TEXT NOT NULL,
  anaf_index_incarcare TEXT, anaf_stare TEXT NOT NULL DEFAULT 'netrimisa',
  anaf_id_descarcare TEXT, anaf_raspuns BLOB, anaf_trimis_la TEXT,
  UNIQUE(serie, numar));
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  firm_id INTEGER NOT NULL REFERENCES firms(id),
  ciclu_facturare TEXT NOT NULL,
  suma REAL NOT NULL, moneda TEXT NOT NULL DEFAULT 'RON',
  recurent INTEGER NOT NULL DEFAULT 0,
  stare TEXT NOT NULL DEFAULT 'in_asteptare',
  creat_la TEXT NOT NULL,
  validat_de TEXT, validat_la TEXT,
  invoice_id INTEGER REFERENCES invoices(id));
CREATE TABLE IF NOT EXISTS planuri_facturare(
  tip TEXT NOT NULL, ciclu_facturare TEXT NOT NULL,
  pret_lunar_ron REAL NOT NULL,
  actualizat_de TEXT, actualizat_la TEXT,
  PRIMARY KEY (tip, ciclu_facturare));
CREATE TABLE IF NOT EXISTS contracts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  firm_id INTEGER NOT NULL REFERENCES firms(id),
  numar INTEGER NOT NULL UNIQUE,
  ciclu_facturare TEXT NOT NULL,
  suma REAL NOT NULL,
  continut TEXT NOT NULL,
  stare TEXT NOT NULL DEFAULT 'in_asteptare',
  creat_la TEXT NOT NULL,
  metoda_semnatura TEXT,
  pdf_semnat BLOB,
  semnatura_verificata INTEGER NOT NULL DEFAULT 0,
  semnatura_detalii TEXT,
  semnat_la TEXT,
  reziliere_solicitata_la TEXT,
  reziliat_la TEXT,
  reziliat_de TEXT,
  ramburs_procent REAL);
"""

# Starile posibile ale unei facturi in raport cu RO e-Factura - vezi
# etva/anaf_oauth.py (upload_invoice/check_upload_status/download_response).
EFACTURA_NETRIMISA = "netrimisa"
EFACTURA_IN_PROCESARE = "in_procesare"
EFACTURA_ACCEPTATA = "acceptata"
EFACTURA_RESPINSA = "respinsa"

# O cerere de plata e auto-declarata de firma (fara procesator de plati
# integrat inca - vezi TODO-ul din portal/app.py despre FGO/Netopia) si
# ramane in_asteptare pana master o valideaza manual dupa ce confirma
# incasarea pe alta cale.
PLATA_IN_ASTEPTARE = "in_asteptare"
PLATA_VALIDATA = "validata"

# Starile contractului de prestari servicii dintre VML si firma abonata -
# vezi portal/contract.py pentru generarea textului si etva/digital_signature.py
# pentru verificarea semnaturii electronice.
CONTRACT_STARE_IN_ASTEPTARE = "in_asteptare"
CONTRACT_STARE_SEMNAT = "semnat"
CONTRACT_STARE_REZILIERE_SOLICITATA = "reziliere_solicitata"
CONTRACT_STARE_REZILIAT = "reziliat"

CONTRACT_METODA_MOUSE = "mouse"
CONTRACT_METODA_CERTIFICAT = "certificat"

# Cerut explicit: daca firma reziliaza contractul inainte de finalul
# perioadei de facturare deja platite, rambursul nu poate depasi jumatate
# din suma achitata pentru acel ciclu.
CONTRACT_RAMBURS_MAX_PROCENT = 50

# Seria unica de facturare a platformei - un singur emitent (VML EXPERT
# ADVISOR SRL), deci o singura serie e suficienta; numerotarea e secventiala
# si fara goluri in cadrul ei (obligatoriu legal, art. 319 alin. 20 lit. a
# Cod Fiscal), calculata sub acelasi db_lock care serializeaza deja toate
# cererile catre portal.db.
FACTURA_SERIE = "ETVA"

# Perioada gratuita de la inregistrare (firms.creat_la) - dupa TRIAL_ZILE,
# firma trebuie sa aleaga un ciclu de facturare (vezi CICLURI_FACTURARE) ca
# sa continue sa foloseasca platforma. Alegerea e auto-declarata, fara plata
# reala - nu exista inca o poarta de plati integrata.
TRIAL_ZILE = 30
CICLU_LUNAR = "lunar"
CICLU_6_LUNI = "6luni"
CICLU_AN = "an"
CICLURI_FACTURARE = (CICLU_LUNAR, CICLU_6_LUNI, CICLU_AN)

# Preturile initiale (RON, per luna echivalenta) folosite doar o singura
# data, ca sa semene tabela planuri_facturare de mai jos la prima pornire -
# vezi _migrate_seed_planuri_facturare. Dupa aceea, sursa de adevar e
# tabela, editabila din /master/nomenclator (vezi get_preturi/set_pret);
# acest dict nu mai e citit de nimeni altcineva.
_PRETURI_INITIALE_RON = {
    FIRM_TIP_DIRECT: {CICLU_LUNAR: 59, CICLU_6_LUNI: 49, CICLU_AN: 39},
    FIRM_TIP_CONTABILITATE: {CICLU_LUNAR: 25, CICLU_6_LUNI: 20, CICLU_AN: 15},
}


def _migrate_legacy_users(conn: sqlite3.Connection) -> None:
    """Fold a pre-multi-firm users(firm_id, role) table into user_firms.

    Older portal.db files have firm_id/role directly on users (one firm
    per account). Detect that shape and migrate in place so existing
    local accounts (including the master account) survive the upgrade.
    """
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "users" not in tables:
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "firm_id" not in cols:
        return
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS user_firms("
        "  user_id INTEGER NOT NULL, firm_id INTEGER NOT NULL,"
        "  role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,"
        "  PRIMARY KEY (user_id, firm_id));")
    conn.execute(
        "INSERT INTO user_firms(user_id, firm_id, role, active) "
        "SELECT id, firm_id, role, active FROM users WHERE firm_id IS NOT NULL")
    conn.executescript(
        "CREATE TABLE users_new("
        "  id INTEGER PRIMARY KEY,"
        "  username TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL,"
        "  is_master INTEGER NOT NULL DEFAULT 0,"
        "  active INTEGER NOT NULL DEFAULT 1);")
    conn.execute(
        "INSERT INTO users_new(id, username, pw_hash, is_master, active) "
        "SELECT id, username, pw_hash, "
        "CASE WHEN role='master' THEN 1 ELSE 0 END, active FROM users")
    conn.executescript("DROP TABLE users; ALTER TABLE users_new RENAME TO users;")
    conn.commit()


def _migrate_add_firm_tip(conn: sqlite3.Connection) -> None:
    """Older portal.db files predate the firms.tip column - add it,
    defaulting existing rows to 'contabilitate' (their prior behavior:
    a manually-managed client list, unchanged)."""
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "firms" not in tables:
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(firms)")}
    if "tip" in cols:
        return
    conn.execute(
        f"ALTER TABLE firms ADD COLUMN tip TEXT NOT NULL "
        f"DEFAULT '{FIRM_TIP_CONTABILITATE}'")
    conn.commit()


def _migrate_add_onboarding_flag(conn: sqlite3.Connection) -> None:
    """Older portal.db files predate onboarding_completat - add it,
    defaulting existing accounts to 0 (unseen) since the guided tour
    prompt is harmless to show once more; it can always be dismissed."""
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "users" not in tables:
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "onboarding_completat" in cols:
        return
    conn.execute(
        "ALTER TABLE users ADD COLUMN onboarding_completat INTEGER NOT NULL DEFAULT 0")
    conn.commit()


def _migrate_add_efactura_columns(conn: sqlite3.Connection) -> None:
    """Older portal.db files predate the RO e-Factura submission columns on
    invoices - add them, defaulting existing rows to 'netrimisa' (not yet
    submitted), which is accurate since submission didn't exist before."""
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "invoices" not in tables:
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(invoices)")}
    if "anaf_stare" in cols:
        return
    conn.executescript(
        "ALTER TABLE invoices ADD COLUMN anaf_index_incarcare TEXT;"
        "ALTER TABLE invoices ADD COLUMN anaf_stare TEXT NOT NULL DEFAULT 'netrimisa';"
        "ALTER TABLE invoices ADD COLUMN anaf_id_descarcare TEXT;"
        "ALTER TABLE invoices ADD COLUMN anaf_raspuns BLOB;"
        "ALTER TABLE invoices ADD COLUMN anaf_trimis_la TEXT;")
    conn.commit()


def _migrate_firms_autoincrement(conn: sqlite3.Connection) -> None:
    """firms.id was a plain INTEGER PRIMARY KEY (no AUTOINCREMENT), so
    SQLite reuses the lowest deleted id for the next INSERT. A firm can be
    soft-deleted (its firms/user_firms rows removed but firm_keys kept on
    purpose, so the old encrypted database stays recoverable) - meaning a
    brand new firm can silently be handed a deleted firm's old id and
    collide with its still-there firm_keys row (IntegrityError on
    firm_keys.firm_id). AUTOINCREMENT keeps a monotonic counter so an id,
    once used, is never handed out again."""
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "firms" not in tables:
        return
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='firms'"
    ).fetchone()["sql"]
    if "AUTOINCREMENT" in ddl.upper():
        return
    max_id = conn.execute(
        "SELECT COALESCE(MAX(id), 0) AS m FROM ("
        "  SELECT id FROM firms"
        "  UNION SELECT firm_id FROM firm_keys"
        "  UNION SELECT firm_id FROM user_firms)").fetchone()["m"]
    conn.executescript(
        "CREATE TABLE firms_new("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,"
        "  cui TEXT UNIQUE NOT NULL, tip TEXT NOT NULL DEFAULT 'contabilitate',"
        "  active INTEGER NOT NULL DEFAULT 1);")
    conn.execute(
        "INSERT INTO firms_new(id, name, cui, tip, active) "
        "SELECT id, name, cui, tip, active FROM firms")
    conn.executescript("DROP TABLE firms; ALTER TABLE firms_new RENAME TO firms;")
    conn.execute(
        "DELETE FROM sqlite_sequence WHERE name IN ('firms', 'firms_new')")
    conn.execute(
        "INSERT INTO sqlite_sequence(name, seq) VALUES ('firms', ?)", (max_id,))
    conn.commit()


def _migrate_add_users_email(conn: sqlite3.Connection) -> None:
    """Older portal.db files predate users.email - add it, defaulting
    existing accounts to NULL (unknown). Was never collected before
    e-Factura/trial email verification needed somewhere to send to."""
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "users" not in tables:
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "email" in cols:
        return
    conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    conn.commit()


def _migrate_add_firms_verificare_trial(conn: sqlite3.Connection) -> None:
    """Older portal.db files predate email verification and the trial/
    billing-cycle columns on firms - add them, defaulting existing rows to
    already-verified (email_verificat=1) since they predate the
    requirement entirely and shouldn't retroactively get locked out."""
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "firms" not in tables:
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(firms)")}
    if "email_verificat" in cols:
        return
    conn.executescript(
        "ALTER TABLE firms ADD COLUMN email_verificat INTEGER NOT NULL DEFAULT 1;"
        "ALTER TABLE firms ADD COLUMN email_verificare_token TEXT;"
        "ALTER TABLE firms ADD COLUMN creat_la TEXT;"
        "ALTER TABLE firms ADD COLUMN trial_expira_la TEXT;"
        "ALTER TABLE firms ADD COLUMN ciclu_facturare TEXT;")
    conn.commit()


def _migrate_seed_planuri_facturare(conn: sqlite3.Connection) -> None:
    """Semeaza nomenclatorul de preturi la prima pornire, cu sumele care
    erau hardcodate inainte (_PRETURI_INITIALE_RON) - doar daca tabela e
    inca goala, ca un master care a modificat deja preturile sa nu le vada
    resetate la valorile istorice la un restart."""
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM planuri_facturare").fetchone()["n"]
    if n:
        return
    acum = datetime.now(timezone.utc).isoformat()
    for tip, cicluri in _PRETURI_INITIALE_RON.items():
        for ciclu, pret in cicluri.items():
            conn.execute(
                "INSERT INTO planuri_facturare(tip, ciclu_facturare, "
                "pret_lunar_ron, actualizat_de, actualizat_la) "
                "VALUES (?, ?, ?, ?, ?)",
                (tip, ciclu, pret, "sistem", acum))
    conn.commit()


def get_preturi(conn: sqlite3.Connection) -> dict:
    """Preturile curente din nomenclator, in aceeasi forma ca fostul dict
    hardcodat PRETURI_LUNARE_RON: {tip: {ciclu: pret_lunar_ron}}."""
    preturi: dict = {tip: {} for tip in FIRM_TIPURI}
    for row in conn.execute(
            "SELECT tip, ciclu_facturare, pret_lunar_ron FROM planuri_facturare"):
        preturi.setdefault(row["tip"], {})[row["ciclu_facturare"]] = row["pret_lunar_ron"]
    return preturi


def set_pret(conn: sqlite3.Connection, tip: str, ciclu: str,
             pret_lunar_ron: float, actualizat_de: str) -> None:
    conn.execute(
        "UPDATE planuri_facturare SET pret_lunar_ron=?, actualizat_de=?, "
        "actualizat_la=? WHERE tip=? AND ciclu_facturare=?",
        (pret_lunar_ron, actualizat_de, datetime.now(timezone.utc).isoformat(),
         tip, ciclu))
    conn.commit()


def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _migrate_legacy_users(conn)
    _migrate_add_firm_tip(conn)
    _migrate_add_onboarding_flag(conn)
    conn.executescript(_SCHEMA)
    _migrate_firms_autoincrement(conn)
    _migrate_add_efactura_columns(conn)
    _migrate_add_users_email(conn)
    _migrate_add_firms_verificare_trial(conn)
    _migrate_seed_planuri_facturare(conn)
    conn.commit()
    return conn
