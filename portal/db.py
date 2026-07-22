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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS firms(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, cui TEXT UNIQUE NOT NULL,
  active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL,
  is_master INTEGER NOT NULL DEFAULT 0,
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


def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _migrate_legacy_users(conn)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn
