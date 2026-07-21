"""Portal database (plain SQLite): firms, users, wrapped data keys.

Roles: 'master' (platform owner, firm_id NULL), 'admin' (firm owner),
'manager', 'contabil', 'junior'. App permissions per role come from
etva.db so both sides stay in sync.
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
  id INTEGER PRIMARY KEY, firm_id INTEGER,
  username TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL,
  role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS firm_keys(
  firm_id INTEGER PRIMARY KEY, wrapped_key BLOB NOT NULL);
"""


def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn
