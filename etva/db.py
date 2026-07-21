"""SQLCipher-encrypted SQLite access + schema."""
try:
    from sqlcipher3 import dbapi2 as sqlcipher
except ImportError:  # sqlcipher3-binary exposes the same API
    from pysqlcipher3 import dbapi2 as sqlcipher


class DbError(Exception):
    pass


PERMISSIONS = {
    "clienti.creare": "Creare clienti",
    "clienti.editare": "Editare clienti",
    "clienti.stergere": "Stergere clienti",
    "reconciliere.creare": "Creare reconcilieri",
    "reconciliere.editare": "Editare reconcilieri",
    "reconciliere.stergere": "Stergere reconcilieri",
    "rapoarte.export": "Export rapoarte",
    "useri.gestionare": "Gestionare utilizatori si roluri",
    "audit.vizualizare": "Vizualizare jurnal de audit",
}

_ALL = list(PERMISSIONS)
DEFAULT_ROLES = {
    "Admin": _ALL,
    "Manager": [p for p in _ALL if p not in
                ("useri.gestionare", "clienti.stergere")],
    "Contabil": ["reconciliere.creare", "reconciliere.editare",
                 "rapoarte.export"],
    "Junior": ["reconciliere.creare", "reconciliere.editare"],
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS roles(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS permissions(
  code TEXT PRIMARY KEY, description TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS role_permissions(
  role_id INTEGER NOT NULL, permission_code TEXT NOT NULL,
  PRIMARY KEY(role_id, permission_code));
CREATE TABLE IF NOT EXISTS user_roles(
  user_id INTEGER NOT NULL, role_id INTEGER NOT NULL,
  PRIMARY KEY(user_id, role_id));
CREATE TABLE IF NOT EXISTS clients(
  id INTEGER PRIMARY KEY, cui TEXT UNIQUE NOT NULL, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS client_assignments(
  user_id INTEGER NOT NULL, client_id INTEGER NOT NULL,
  PRIMARY KEY(user_id, client_id));
CREATE TABLE IF NOT EXISTS reconciliations(
  id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL, period TEXT NOT NULL,
  created_at TEXT NOT NULL, created_by INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS invoices_company(
  id INTEGER PRIMARY KEY, reconciliation_id INTEGER NOT NULL,
  partner_cui TEXT, invoice_no TEXT, date TEXT,
  base REAL, vat REAL, category TEXT);
CREATE TABLE IF NOT EXISTS invoices_anaf(
  id INTEGER PRIMARY KEY, reconciliation_id INTEGER NOT NULL,
  partner_cui TEXT, invoice_no TEXT, date TEXT,
  base REAL, vat REAL, category TEXT);
CREATE TABLE IF NOT EXISTS differences(
  id INTEGER PRIMARY KEY, reconciliation_id INTEGER NOT NULL,
  diff_type TEXT NOT NULL, details TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY, user_id INTEGER, action TEXT NOT NULL,
  entity TEXT, entity_id TEXT, ts TEXT NOT NULL);
"""


def open_db(path: str, key: bytes):
    conn = sqlcipher.connect(path)
    conn.row_factory = sqlcipher.Row
    conn.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
    try:
        conn.execute("SELECT count(*) FROM sqlite_master")
    except sqlcipher.DatabaseError:
        conn.close()
        raise DbError("Cheie gresita sau baza de date corupta.")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn) -> None:
    conn.executescript(_SCHEMA)
    for code, desc in PERMISSIONS.items():
        conn.execute(
            "INSERT OR IGNORE INTO permissions(code, description) VALUES(?,?)",
            (code, desc))
    for name, perms in DEFAULT_ROLES.items():
        conn.execute("INSERT OR IGNORE INTO roles(name) VALUES(?)", (name,))
        role_id = conn.execute(
            "SELECT id FROM roles WHERE name=?", (name,)).fetchone()["id"]
        for p in perms:
            conn.execute(
                "INSERT OR IGNORE INTO role_permissions VALUES(?,?)",
                (role_id, p))
    conn.commit()
