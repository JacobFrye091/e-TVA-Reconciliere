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

from etva import dbcompat
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

# Protectie brute-force la /autentificare - dupa LOGIN_MAX_INCERCARI esecuri
# consecutive pe acelasi identificator (CUI sau username master), acel
# identificator e blocat LOGIN_BLOCARE_MINUTE minute, indiferent de parola
# incercata (vezi login_lockouts, portal/app.py).
LOGIN_MAX_INCERCARI = 5
LOGIN_BLOCARE_MINUTE = 15

_SCHEMA = """
CREATE TABLE IF NOT EXISTS firms(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, cui TEXT UNIQUE NOT NULL,
  tip TEXT NOT NULL DEFAULT 'contabilitate',
  active INTEGER NOT NULL DEFAULT 1,
  email_verificat INTEGER NOT NULL DEFAULT 0,
  email_verificare_token TEXT,
  creat_la TEXT,
  trial_expira_la TEXT,
  ciclu_facturare TEXT,
  trial_reminder_ultim_prag INTEGER,
  arhivata_la TEXT,
  reconcilieri_lunare_estimate INTEGER);
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
  valoare_neta REAL NOT NULL, cota_tva REAL NOT NULL DEFAULT 21,
  valoare_tva REAL NOT NULL, valoare_totala REAL NOT NULL,
  moneda TEXT NOT NULL DEFAULT 'RON',
  stare TEXT NOT NULL DEFAULT 'emisa',
  creat_de TEXT NOT NULL, creat_la TEXT NOT NULL,
  anaf_index_incarcare TEXT, anaf_stare TEXT NOT NULL DEFAULT 'netrimisa',
  anaf_id_descarcare TEXT, anaf_raspuns BLOB, anaf_trimis_la TEXT,
  fgo_serie TEXT, fgo_numar TEXT, fgo_link_pdf TEXT,
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
  beneficiar_denumire TEXT NOT NULL,
  beneficiar_cui TEXT NOT NULL,
  beneficiar_adresa TEXT NOT NULL,
  stare TEXT NOT NULL DEFAULT 'in_asteptare',
  creat_la TEXT NOT NULL,
  metoda_semnatura TEXT,
  semnatura_mouse_img BLOB,
  semnatura_verificata INTEGER NOT NULL DEFAULT 0,
  semnatura_detalii TEXT,
  semnat_la TEXT,
  reziliere_solicitata_la TEXT,
  reziliat_la TEXT,
  reziliat_de TEXT,
  ramburs_procent REAL,
  esemneaza_request_id TEXT,
  esemneaza_document_pdf BLOB,
  esemneaza_certificate_pdf BLOB,
  prestator_semnat_la TEXT,
  contract_xml_final BLOB);
CREATE TABLE IF NOT EXISTS login_lockouts(
  identificator TEXT PRIMARY KEY,
  incercari INTEGER NOT NULL DEFAULT 0,
  ultima_incercare TEXT,
  blocat_pana TEXT);
CREATE TABLE IF NOT EXISTS setari_tva(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cota_procent REAL NOT NULL,
  activa INTEGER NOT NULL DEFAULT 0,
  actualizat_de TEXT, actualizat_la TEXT);
CREATE TABLE IF NOT EXISTS pachete_reconcilieri(
  id INTEGER PRIMARY KEY CHECK (id = 1),
  reconcilieri_incluse INTEGER NOT NULL,
  marime_pachet INTEGER NOT NULL,
  pret_pachet_lunar_ron REAL NOT NULL,
  actualizat_de TEXT, actualizat_la TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS idx_setari_tva_activa
  ON setari_tva(activa) WHERE activa=1;
"""

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
# Inlocuieste CONTRACT_METODA_MOUSE ca metoda oferita in UI (2026-07-27) -
# semnatura desenata cu mouse-ul ("semneaza cu ce vrei") a fost considerata
# insuficient de robusta legal. CONTRACT_METODA_MOUSE ramane doar pentru
# contractele deja semnate asa inainte de aceasta schimbare.
CONTRACT_METODA_ESEMNEAZA = "esemneaza"

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

# Cota de TVA (Romania) NU e hardcodata - legea s-a schimbat deja o data in
# timpul acestui proiect (19% pana la 31.07.2025, 21% de la 01.08.2025), asa
# ca valoarea curenta e in tabela setari_tva, editabila din
# /master/nomenclator (vezi get_cota_tva/set_cota_tva) - un master poate
# corecta cota chiar in ziua in care legea se schimba, fara sa astepte o
# livrare de cod. setari_tva pastreaza istoricul complet al cotelor (nu
# doar valoarea curenta) - fiecare rand are un marcator `activa`, iar un
# index unic partial (idx_setari_tva_activa, WHERE activa=1) garanteaza la
# nivel de baza de date ca cel mult o singura inregistrare poate fi activa
# simultan. set_cota_tva adauga un rand nou si il activeaza (dezactivand
# automat cel vechi); activeaza_cota_tva muta marcatorul inapoi pe un rand
# mai vechi din istoric, fara sa retasteze procentul. _COTA_TVA_INITIALA e
# folosita o singura data, ca sa semene primul rand (deja activ) la prima
# pornire - vezi _migrate_seed_cota_tva. Dupa aceea, sursa de adevar e
# tabela, la fel ca la planuri_facturare/get_preturi.
#
# planuri_facturare.pret_lunar_ron (si deci contracts.suma - vezi
# contract.py, care afiseaza explicit "exclusiv TVA") raman pretul de baza,
# fara TVA. Suma efectiv ceruta/incasata de la client (payments.suma)
# include TVA-ul curent adaugat peste pretul de baza - vezi _suma_cu_tva in
# portal/app.py.
_COTA_TVA_INITIALA = 21

# Pragurile (zile ramase din trial) la care se trimite un email de avertizare
# firmelor care nu si-au ales inca un ciclu de facturare - in ordine
# descrescatoare de urgenta. O firma primeste un singur email per prag,
# niciodata retrimis - vezi firms.trial_reminder_ultim_prag si
# portal/trial_reminders.py.
TRIAL_REMINDER_PRAGURI_ZILE = (7, 1, 0)

# Preturile initiale (RON, per luna echivalenta) folosite doar o singura
# data, ca sa semene tabela planuri_facturare de mai jos la prima pornire -
# vezi _migrate_seed_planuri_facturare. Dupa aceea, sursa de adevar e
# tabela, editabila din /master/nomenclator (vezi get_preturi/set_pret);
# acest dict nu mai e citit de nimeni altcineva.
_PRETURI_INITIALE_RON = {
    FIRM_TIP_DIRECT: {CICLU_LUNAR: 59, CICLU_6_LUNI: 49, CICLU_AN: 39},
    FIRM_TIP_CONTABILITATE: {CICLU_LUNAR: 25, CICLU_6_LUNI: 20, CICLU_AN: 15},
}

# Nomenclatorul regandit ca "abonament standard + reconcilieri": abonamentul
# standard al unei firme directe (PFA/SRL) include un numar de reconcilieri
# pe luna (pragul de 100, cerut explicit); o firma care isi estimeaza mai
# multe (firms.reconcilieri_lunare_estimate, cerut la inregistrare) plateste
# in plus pachete extra de cate `marime_pachet` reconcilieri/luna. Valorile
# de mai jos doar semeaza tabela pachete_reconcilieri la prima pornire
# (acelasi pattern ca _PRETURI_INITIALE_RON) - dupa aceea sursa de adevar e
# tabela, editabila din /master/nomenclator.
_PACHET_RECONCILIERI_INITIAL = {
    "reconcilieri_incluse": 100,
    "marime_pachet": 50,
    "pret_pachet_lunar_ron": 19,
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


def _migrate_add_fgo_columns(conn: sqlite3.Connection) -> None:
    """Older portal.db files predate integrarea FGO - adauga seria/numarul
    REALE atribuite de FGO la factura/emitere (afisate firmei) + linkul
    catre PDF-ul FGO. serie/numar raman coloanele DB interne (id-ul de
    randare unic, generat local), neschimbate."""
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "invoices" not in tables:
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(invoices)")}
    if "fgo_serie" in cols:
        return
    conn.executescript(
        "ALTER TABLE invoices ADD COLUMN fgo_serie TEXT;"
        "ALTER TABLE invoices ADD COLUMN fgo_numar TEXT;"
        "ALTER TABLE invoices ADD COLUMN fgo_link_pdf TEXT;")
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


def _migrate_add_firms_trial_reminder(conn: sqlite3.Connection) -> None:
    """Older portal.db files predate the trial-expiry reminder emails - add
    the column that tracks the last threshold (TRIAL_REMINDER_PRAGURI_ZILE)
    already notified for each firm, so a fresh deploy doesn't immediately
    re-send every reminder to every existing firm."""
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "firms" not in tables:
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(firms)")}
    if "trial_reminder_ultim_prag" in cols:
        return
    conn.execute(
        "ALTER TABLE firms ADD COLUMN trial_reminder_ultim_prag INTEGER;")
    conn.commit()


def _migrate_add_firms_arhivare(conn: sqlite3.Connection) -> None:
    """Older portal.db files predate account archiving - add the column that
    marks when a firm was archived (trial expirat, fara ciclu de facturare
    ales - vezi portal/trial_reminders.py::arhiveaza_firme_neplatitoare).
    NULL inseamna firma nu e arhivata."""
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "firms" not in tables:
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(firms)")}
    if "arhivata_la" in cols:
        return
    conn.execute("ALTER TABLE firms ADD COLUMN arhivata_la TEXT;")
    conn.commit()


def _migrate_add_contracts_esemneaza(conn: sqlite3.Connection) -> None:
    """Older portal.db files predate the eSemneaza.ro integration (replaces
    the old mouse-drawn signature - vezi CONTRACT_METODA_ESEMNEAZA) - add
    the columns that track the pending sign request id and the final
    signed document/certificate fetched from eSemneaza once completed."""
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "contracts" not in tables:
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(contracts)")}
    if "esemneaza_request_id" in cols:
        return
    conn.execute("ALTER TABLE contracts ADD COLUMN esemneaza_request_id TEXT;")
    conn.execute("ALTER TABLE contracts ADD COLUMN esemneaza_document_pdf BLOB;")
    conn.execute("ALTER TABLE contracts ADD COLUMN esemneaza_certificate_pdf BLOB;")
    conn.commit()


def _migrate_add_contract_prestator_semnare(conn: sqlite3.Connection) -> None:
    """Adauga urmarirea separata a semnaturii PRESTATORULUI (recipient 1 la
    eSemneaza, ordine impusa prin signInOrder) si instantaneul XML inghetat
    la finalizare - vezi planning/specs/2026-07-28-contract-esemneaza-admin-
    review-design.md."""
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "contracts" not in tables:
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(contracts)")}
    if "prestator_semnat_la" in cols:
        return
    conn.execute("ALTER TABLE contracts ADD COLUMN prestator_semnat_la TEXT;")
    conn.execute("ALTER TABLE contracts ADD COLUMN contract_xml_final BLOB;")
    conn.commit()


def _migrate_add_firms_reconcilieri_estimate(conn: sqlite3.Connection) -> None:
    """Older portal.db files predate firms.reconcilieri_lunare_estimate -
    add it, defaulting existing rows to NULL (necunoscut): firmele
    inregistrate inainte de aceasta cerinta nu au declarat o estimare,
    deci nu li se factureaza pachete extra pana nu o completeaza."""
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "firms" not in tables:
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(firms)")}
    if "reconcilieri_lunare_estimate" in cols:
        return
    conn.execute(
        "ALTER TABLE firms ADD COLUMN reconcilieri_lunare_estimate INTEGER")
    conn.commit()


def _migrate_seed_pachet_reconcilieri(conn: sqlite3.Connection) -> None:
    """Semeaza randul unic din pachete_reconcilieri la prima pornire, cu
    valorile initiale (_PACHET_RECONCILIERI_INITIAL) - doar daca tabela e
    inca goala, ca un master care le-a modificat deja sa nu le vada
    resetate la un restart (acelasi pattern ca _migrate_seed_planuri_facturare)."""
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM pachete_reconcilieri").fetchone()["n"]
    if n:
        return
    p = _PACHET_RECONCILIERI_INITIAL
    conn.execute(
        "INSERT INTO pachete_reconcilieri(id, reconcilieri_incluse, "
        "marime_pachet, pret_pachet_lunar_ron, actualizat_de, actualizat_la) "
        "VALUES (1, ?, ?, ?, ?, ?)",
        (p["reconcilieri_incluse"], p["marime_pachet"],
         p["pret_pachet_lunar_ron"], "sistem",
         datetime.now(timezone.utc).isoformat()))
    conn.commit()


def get_pachet_reconcilieri(conn: sqlite3.Connection) -> dict:
    """Setarile curente 'abonament standard + reconcilieri': cate
    reconcilieri/luna include abonamentul standard, cat de mare e un pachet
    extra si cat costa pe luna."""
    return dict(conn.execute(
        "SELECT * FROM pachete_reconcilieri WHERE id=1").fetchone())


def set_pachet_reconcilieri(conn: sqlite3.Connection, reconcilieri_incluse: int,
                            marime_pachet: int, pret_pachet_lunar_ron: float,
                            actualizat_de: str) -> None:
    conn.execute(
        "UPDATE pachete_reconcilieri SET reconcilieri_incluse=?, "
        "marime_pachet=?, pret_pachet_lunar_ron=?, actualizat_de=?, "
        "actualizat_la=? WHERE id=1",
        (reconcilieri_incluse, marime_pachet, pret_pachet_lunar_ron,
         actualizat_de, datetime.now(timezone.utc).isoformat()))
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


def _migrate_contracts_fara_pdf(conn: sqlite3.Connection) -> None:
    """Contractele nu mai stocheaza textul inghetat (continut) sau PDF-uri
    (pdf_semnat) - sunt fisiere mari, inutile cand pot fi regenerate oricand
    din datele structurate (vezi portal/contract.py:genereaza_text_din_rand).
    In loc, contracts pastreaza doar snapshotul ANAF al beneficiarului
    (beneficiar_denumire/cui/adresa) si, pentru semnatura cu mouse-ul, doar
    PNG-ul brut al semnaturii (semnatura_mouse_img) - mic, spre deosebire de
    un PDF intreg. Pentru semnatura cu certificat, fisierul PDF incarcat de
    utilizator nu mai e pastrat deloc - doar semnatura_detalii (JSON cu
    semnatar/valabilitate/incredere) ramane ca proba de audit."""
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "contracts" not in tables:
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(contracts)")}
    if "beneficiar_denumire" in cols:
        return
    conn.execute("ALTER TABLE contracts RENAME TO contracts_old")
    conn.executescript(
        "CREATE TABLE contracts("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  firm_id INTEGER NOT NULL REFERENCES firms(id),"
        "  numar INTEGER NOT NULL UNIQUE,"
        "  ciclu_facturare TEXT NOT NULL,"
        "  suma REAL NOT NULL,"
        "  beneficiar_denumire TEXT NOT NULL,"
        "  beneficiar_cui TEXT NOT NULL,"
        "  beneficiar_adresa TEXT NOT NULL,"
        "  stare TEXT NOT NULL DEFAULT 'in_asteptare',"
        "  creat_la TEXT NOT NULL,"
        "  metoda_semnatura TEXT,"
        "  semnatura_mouse_img BLOB,"
        "  semnatura_verificata INTEGER NOT NULL DEFAULT 0,"
        "  semnatura_detalii TEXT,"
        "  semnat_la TEXT,"
        "  reziliere_solicitata_la TEXT,"
        "  reziliat_la TEXT,"
        "  reziliat_de TEXT,"
        "  ramburs_procent REAL);")
    if "continut" in cols:
        # Randuri dintr-o forma veche (inainte de aceasta migrare) - nu mai
        # avem de unde reconstitui adresa beneficiarului (nu era stocata
        # separat, doar inghetata in textul PDF-ului), asa ca marcam explicit
        # ca informatia nu a fost pastrata, in loc sa inventam o valoare.
        conn.execute(
            "INSERT INTO contracts(id, firm_id, numar, ciclu_facturare, suma, "
            "beneficiar_denumire, beneficiar_cui, beneficiar_adresa, stare, "
            "creat_la, metoda_semnatura, semnatura_verificata, "
            "semnatura_detalii, semnat_la, reziliere_solicitata_la, "
            "reziliat_la, reziliat_de, ramburs_procent) "
            "SELECT co.id, co.firm_id, co.numar, co.ciclu_facturare, co.suma, "
            "f.name, f.cui, "
            "'(adresa nepastrata - contract creat inainte de stocarea "
            "structurata a datelor beneficiarului)', "
            "co.stare, co.creat_la, co.metoda_semnatura, "
            "co.semnatura_verificata, co.semnatura_detalii, co.semnat_la, "
            "co.reziliere_solicitata_la, co.reziliat_la, co.reziliat_de, "
            "co.ramburs_procent "
            "FROM contracts_old co JOIN firms f ON f.id = co.firm_id")
    conn.execute("DROP TABLE contracts_old")
    conn.commit()


def _migrate_setari_tva_istoric(conn: sqlite3.Connection) -> None:
    """setari_tva a inceput ca un singur rand fixat (id=1, fara istoric) -
    userul a cerut pastrarea unui istoric al cotelor, fiecare cu un
    marcator `activa` editabil din admin, cu un index unic garantand ca
    doar o inregistrare poate fi activa. Migreaza randul unic vechi (daca
    exista) intr-un rand activ=1 in noua forma, la fel ca
    _migrate_contracts_fara_pdf pentru un rebuild de tabela."""
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "setari_tva" not in tables:
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(setari_tva)")}
    if "activa" in cols:
        return
    conn.execute("ALTER TABLE setari_tva RENAME TO setari_tva_old")
    conn.executescript(
        "CREATE TABLE setari_tva("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  cota_procent REAL NOT NULL,"
        "  activa INTEGER NOT NULL DEFAULT 0,"
        "  actualizat_de TEXT, actualizat_la TEXT);"
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_setari_tva_activa "
        "  ON setari_tva(activa) WHERE activa=1;")
    conn.execute(
        "INSERT INTO setari_tva(cota_procent, activa, actualizat_de, actualizat_la) "
        "SELECT cota_procent, 1, actualizat_de, actualizat_la FROM setari_tva_old")
    conn.execute("DROP TABLE setari_tva_old")
    conn.commit()


def _migrate_seed_cota_tva(conn: sqlite3.Connection) -> None:
    """Semeaza cota de TVA (activa) la prima pornire, cu valoarea curenta la
    data scrierii acestui cod (_COTA_TVA_INITIALA) - doar daca tabela e inca
    goala, ca un master care a adaugat deja cote sa nu le vada resetate la
    un restart (acelasi pattern ca _migrate_seed_planuri_facturare)."""
    n = conn.execute("SELECT COUNT(*) AS n FROM setari_tva").fetchone()["n"]
    if n:
        return
    conn.execute(
        "INSERT INTO setari_tva(cota_procent, activa, actualizat_de, actualizat_la) "
        "VALUES (?, TRUE, ?, ?)",
        (_COTA_TVA_INITIALA, "sistem", datetime.now(timezone.utc).isoformat()))
    conn.commit()


def get_cota_tva(conn: sqlite3.Connection) -> float:
    return conn.execute(
        "SELECT cota_procent FROM setari_tva WHERE activa=TRUE").fetchone()["cota_procent"]


def listeaza_cote_tva(conn: sqlite3.Connection) -> list:
    """Istoricul complet al cotelor de TVA, cea mai recenta prima - afisat
    in /master/nomenclator alaturi de butonul de (re)activare."""
    return conn.execute("SELECT * FROM setari_tva ORDER BY id DESC").fetchall()


def set_cota_tva(conn: sqlite3.Connection, procent: float, actualizat_de: str) -> None:
    """Adauga o cota noua si o activeaza - nu suprascrie istoricul existent,
    doar dezactiveaza orice alta cota activa (indexul unic ar respinge
    oricum doua randuri active simultan)."""
    acum = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE setari_tva SET activa=FALSE WHERE activa=TRUE")
    conn.execute(
        "INSERT INTO setari_tva(cota_procent, activa, actualizat_de, actualizat_la) "
        "VALUES (?, TRUE, ?, ?)",
        (procent, actualizat_de, acum))
    conn.commit()


def activeaza_cota_tva(conn: sqlite3.Connection, id: int, actualizat_de: str) -> bool:
    """Muta marcatorul `activa` inapoi pe o cota din istoric (ex: revenire
    dupa o greseala), fara sa retasteze procentul. Intoarce False daca id-ul
    nu exista in setari_tva."""
    if not conn.execute(
            "SELECT 1 FROM setari_tva WHERE id=?", (id,)).fetchone():
        return False
    conn.execute("UPDATE setari_tva SET activa=FALSE WHERE activa=TRUE")
    conn.execute(
        "UPDATE setari_tva SET activa=TRUE, actualizat_de=?, actualizat_la=? "
        "WHERE id=?",
        (actualizat_de, datetime.now(timezone.utc).isoformat(), id))
    conn.commit()
    return True


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


def _open_db_postgres():
    """Ramura Postgres a open_db: conexiunea vine din DATABASE_URL, schema
    nu se creeaza aici (sursa de adevar e etva/pg_schema.sql, aplicat ca
    pas de operare cu psql -f), ci doar se VERIFICA - pornirea esueaza
    zgomotos si devreme daca baza a derivat de la referinta. Migrarile
    _migrate_* raman exclusiv pe ramura SQLite; pe Postgres ruleaza doar
    cele doua seed-uri idempotente de date initiale."""
    from etva import pg
    conn = dbcompat.connect(pg.dsn_from_env())
    probleme = pg.verify_schema(conn.raw)
    if probleme:
        conn.close()
        raise RuntimeError(
            "Schema Postgres nu corespunde referintei (ruleaza "
            "etva/pg_schema.sql): " + "; ".join(probleme))
    conn.rollback()  # verify_schema a deschis o tranzactie de citire
    _migrate_seed_planuri_facturare(conn)
    _migrate_seed_cota_tva(conn)
    _migrate_seed_pachet_reconcilieri(conn)
    return conn


def open_db(path: str) -> sqlite3.Connection:
    if dbcompat.backend() == "postgres":
        return _open_db_postgres()
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _migrate_legacy_users(conn)
    _migrate_add_firm_tip(conn)
    _migrate_add_onboarding_flag(conn)
    # Trebuie rulata inainte de executescript(_SCHEMA): schema de mai jos
    # adauga un CREATE UNIQUE INDEX pe setari_tva.activa, care ar esua daca
    # tabela mai exista inca in forma veche (fara acea coloana) - la fel ca
    # celelalte migrari de mai sus, care repara forme vechi de tabele
    # inainte ca scriptul de schema sa presupuna forma noua.
    _migrate_setari_tva_istoric(conn)
    conn.executescript(_SCHEMA)
    _migrate_firms_autoincrement(conn)
    _migrate_add_efactura_columns(conn)
    _migrate_add_fgo_columns(conn)
    _migrate_add_users_email(conn)
    _migrate_add_firms_verificare_trial(conn)
    _migrate_add_firms_trial_reminder(conn)
    _migrate_add_firms_arhivare(conn)
    _migrate_add_firms_reconcilieri_estimate(conn)
    _migrate_seed_planuri_facturare(conn)
    _migrate_contracts_fara_pdf(conn)
    _migrate_add_contracts_esemneaza(conn)
    _migrate_add_contract_prestator_semnare(conn)
    _migrate_seed_cota_tva(conn)
    _migrate_seed_pachet_reconcilieri(conn)
    conn.commit()
    return conn
