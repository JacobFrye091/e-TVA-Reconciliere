"""Portal database (plain SQLite): users, firms, per-user firm memberships.

A user account (login identity) is independent of any single firm: the
same person can be linked to several firms (SRL/PFA) through user_firms,
each with its own role there ('admin' firm owner, 'manager', 'contabil',
'junior'). 'master' is not a role but a separate is_master flag on the
user, since the platform owner has no firm membership at all. App
permissions per role come from etva.db so both sides stay in sync.
"""
import sqlite3

from etva.db import PERMISSIONS, DEFAULT_ROLES

ROLE_PERMISSIONS = {
    "admin": list(PERMISSIONS),
    "manager": DEFAULT_ROLES["Manager"],
    "contabil": DEFAULT_ROLES["Contabil"],
    "junior": DEFAULT_ROLES["Junior"],
}

# A firm is either its own taxpayer (self-reconciling PFA/SRL - gets an
# auto-created client matching its own CUI, no separate client list to
# manage) or an accounting firm juggling several clients' reconciliations.
FIRM_TIP_DIRECT = "direct"
FIRM_TIP_CONTABILITATE = "contabilitate"
FIRM_TIPURI = (FIRM_TIP_DIRECT, FIRM_TIP_CONTABILITATE)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS firms(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, cui TEXT UNIQUE NOT NULL,
  tip TEXT NOT NULL DEFAULT 'contabilitate',
  active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL,
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
"""


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


def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _migrate_legacy_users(conn)
    _migrate_add_firm_tip(conn)
    _migrate_add_onboarding_flag(conn)
    conn.executescript(_SCHEMA)
    _migrate_firms_autoincrement(conn)
    conn.commit()
    return conn
