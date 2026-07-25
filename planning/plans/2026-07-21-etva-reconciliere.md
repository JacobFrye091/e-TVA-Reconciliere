# e-TVA-Reconciliere Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Desktop app for Romanian accounting firms to reconcile company VAT journals (D300/394 format) against ANAF e-TVA data, with multi-user auth, configurable roles, encrypted SQLite storage, audit log, and Excel reports with suggested D300 corrections.

**Architecture:** Python backend (Flask served locally, rendered in a native pywebview window). Layers: crypto/keystore → encrypted DB (SQLCipher) → auth/permissions/audit → importers (company journal + pluggable `AnafDataSource`) → reconciliation engine → correction advisor → Excel export → Flask API with permission guards → single-page HTML/JS frontend.

**Tech Stack:** Python 3.11+, Flask, pywebview, sqlcipher3-wheels, argon2-cffi, cryptography, mnemonic, pandas, openpyxl, pytest.

## Global Constraints

- Target OS: Windows (final packaging: single .exe via PyInstaller — packaging itself is out of scope for this plan, noted in Task 14).
- Python 3.11+.
- All user-facing UI text in Romanian; code identifiers/comments in English.
- DB is SQLCipher-encrypted; the raw DB key NEVER touches disk unencrypted — only wrapped copies in the keystore file.
- Recovery phrase: 24 words (BIP39 English wordlist via `mnemonic` package), shown exactly once at setup.
- Reconciliation tolerance default: 1.0 RON (configurable per call).
- Difference types (exact strings): `lipsa_in_anaf`, `lipsa_la_companie`, `suma_diferita`, `duplicat`.
- Permission codes (exact strings): `clienti.creare`, `clienti.editare`, `clienti.stergere`, `reconciliere.creare`, `reconciliere.editare`, `reconciliere.stergere`, `rapoarte.export`, `useri.gestionare`, `audit.vizualizare`.
- Canonical invoice row keys (exact): `partner_cui`, `invoice_no`, `date`, `base`, `vat`, `category`.
- Audit log is append-only: the code exposes NO update/delete path for it.
- Project root for all paths below: the repo root (`e-TVA-Reconciliere/`).

## File Structure

```
etva/
  __init__.py
  crypto.py          # KDF, keystore create/unlock/recover, recovery phrase
  db.py              # SQLCipher connection, schema, seed data
  auth.py            # users, argon2 hashing, login verification
  permissions.py     # roles CRUD, permission resolution, checks
  audit.py           # append-only audit logger
  clients.py         # clients CRUD + user-client assignments
  importer/
    __init__.py
    company.py       # company journal parser (Excel/CSV, fixed columns)
    anaf.py          # AnafDataSource interface + file-based impl with column mapping
  engine.py          # reconciliation engine
  advisor.py         # D300 correction suggestions
  export.py          # Excel report generation
  server.py          # Flask app factory, routes, permission guards
  main.py            # pywebview entry point + first-run setup flow
web/
  index.html         # single-page frontend (login, dashboard, admin, results)
tests/
  conftest.py
  test_crypto.py
  test_db.py
  test_auth.py
  test_permissions.py
  test_audit.py
  test_clients.py
  test_importer_company.py
  test_importer_anaf.py
  test_engine.py
  test_advisor.py
  test_export.py
  test_server.py
requirements.txt
```

---

### Task 1: Project scaffolding + crypto/keystore module

**Files:**
- Create: `requirements.txt`, `etva/__init__.py`, `etva/crypto.py`
- Test: `tests/test_crypto.py`, `tests/conftest.py`

**Interfaces:**
- Produces:
  - `crypto.create_keystore(master_password: str, path: str) -> str` — creates keystore JSON at `path`, returns 24-word recovery phrase.
  - `crypto.unlock_keystore(master_password: str, path: str) -> bytes` — returns 32-byte DB key. Raises `crypto.KeystoreError` on wrong password/missing file.
  - `crypto.recover_keystore(recovery_phrase: str, path: str, new_master_password: str) -> bytes` — re-wraps key under new password, returns DB key. Raises `KeystoreError` if phrase wrong.
  - `crypto.KeystoreError(Exception)`

- [ ] **Step 1: Write requirements.txt and empty package**

`requirements.txt`:
```
flask>=3.0
pywebview>=5.0
sqlcipher3-wheels>=0.5
argon2-cffi>=23.1
cryptography>=42.0
mnemonic>=0.21
pandas>=2.2
openpyxl>=3.1
pytest>=8.0
```

`etva/__init__.py`: empty file.

`tests/conftest.py`:
```python
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
```

Run: `pip install -r requirements.txt`
Expected: all packages install. If `sqlcipher3-wheels` fails on this platform, try `pip install sqlcipher3-binary`; if that also fails, STOP and report — do not silently fall back to plain sqlite3.

- [ ] **Step 2: Write failing tests**

`tests/test_crypto.py`:
```python
import pytest
from etva import crypto

def test_create_and_unlock(tmp_path):
    ks = str(tmp_path / "keystore.json")
    phrase = crypto.create_keystore("Parola123!", ks)
    assert len(phrase.split()) == 24
    key = crypto.unlock_keystore("Parola123!", ks)
    assert isinstance(key, bytes) and len(key) == 32

def test_wrong_password_raises(tmp_path):
    ks = str(tmp_path / "keystore.json")
    crypto.create_keystore("Parola123!", ks)
    with pytest.raises(crypto.KeystoreError):
        crypto.unlock_keystore("gresit", ks)

def test_recover_with_phrase(tmp_path):
    ks = str(tmp_path / "keystore.json")
    phrase = crypto.create_keystore("Parola123!", ks)
    key1 = crypto.unlock_keystore("Parola123!", ks)
    key2 = crypto.recover_keystore(phrase, ks, "ParolaNoua456!")
    assert key1 == key2
    assert crypto.unlock_keystore("ParolaNoua456!", ks) == key1
    with pytest.raises(crypto.KeystoreError):
        crypto.unlock_keystore("Parola123!", ks)

def test_wrong_phrase_raises(tmp_path):
    ks = str(tmp_path / "keystore.json")
    crypto.create_keystore("Parola123!", ks)
    bad = " ".join(["abandon"] * 24)
    with pytest.raises(crypto.KeystoreError):
        crypto.recover_keystore(bad, ks, "x")
```

Run: `python -m pytest tests/test_crypto.py -v` — Expected: FAIL (module has no members).

- [ ] **Step 3: Implement `etva/crypto.py`**

```python
"""Keystore: a random 32-byte DB key wrapped twice (master password + recovery phrase)."""
import json, os, base64
from argon2.low_level import hash_secret_raw, Type
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from mnemonic import Mnemonic


class KeystoreError(Exception):
    pass


def _kdf(secret: bytes, salt: bytes) -> bytes:
    return hash_secret_raw(secret, salt, time_cost=3, memory_cost=65536,
                           parallelism=4, hash_len=32, type=Type.ID)


def _wrap(wrapping_key: bytes, db_key: bytes) -> dict:
    nonce = os.urandom(12)
    ct = AESGCM(wrapping_key).encrypt(nonce, db_key, None)
    return {"nonce": base64.b64encode(nonce).decode(),
            "ct": base64.b64encode(ct).decode()}


def _unwrap(wrapping_key: bytes, blob: dict) -> bytes:
    try:
        return AESGCM(wrapping_key).decrypt(
            base64.b64decode(blob["nonce"]), base64.b64decode(blob["ct"]), None)
    except InvalidTag:
        raise KeystoreError("Parola sau fraza de recuperare este incorecta.")


def create_keystore(master_password: str, path: str) -> str:
    db_key = os.urandom(32)
    phrase = Mnemonic("english").generate(strength=256)  # 24 words
    pw_salt, ph_salt = os.urandom(16), os.urandom(16)
    data = {
        "pw_salt": base64.b64encode(pw_salt).decode(),
        "ph_salt": base64.b64encode(ph_salt).decode(),
        "pw_wrap": _wrap(_kdf(master_password.encode(), pw_salt), db_key),
        "ph_wrap": _wrap(_kdf(phrase.encode(), ph_salt), db_key),
    }
    with open(path, "w") as f:
        json.dump(data, f)
    return phrase


def _load(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        raise KeystoreError("Fisierul keystore lipseste sau este corupt.")


def unlock_keystore(master_password: str, path: str) -> bytes:
    data = _load(path)
    key = _kdf(master_password.encode(), base64.b64decode(data["pw_salt"]))
    return _unwrap(key, data["pw_wrap"])


def recover_keystore(recovery_phrase: str, path: str, new_master_password: str) -> bytes:
    data = _load(path)
    phrase = " ".join(recovery_phrase.split())
    key = _kdf(phrase.encode(), base64.b64decode(data["ph_salt"]))
    db_key = _unwrap(key, data["ph_wrap"])
    new_salt = os.urandom(16)
    data["pw_salt"] = base64.b64encode(new_salt).decode()
    data["pw_wrap"] = _wrap(_kdf(new_master_password.encode(), new_salt), db_key)
    with open(path, "w") as f:
        json.dump(data, f)
    return db_key
```

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_crypto.py -v` — Expected: 4 PASS.

- [ ] **Step 5: Commit** — `git add requirements.txt etva tests && git commit -m "feat: crypto keystore with master password + recovery phrase"`

---

### Task 2: Encrypted DB layer + schema + seed data

**Files:**
- Create: `etva/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing from other tasks (key comes in as bytes).
- Produces:
  - `db.open_db(path: str, key: bytes) -> Connection` — SQLCipher connection, `row_factory` set so rows behave like dicts (`sqlite3.Row` equivalent). Raises `db.DbError` if key is wrong / file corrupt.
  - `db.init_schema(conn) -> None` — creates all tables (idempotent) and seeds permissions + 4 default roles.
  - `db.DbError(Exception)`
  - Table names/columns exactly as in the schema below — all later tasks depend on them.
  - `db.PERMISSIONS: dict[str, str]` (code → Romanian description), `db.DEFAULT_ROLES: dict[str, list[str]]`.

- [ ] **Step 1: Write failing tests**

`tests/test_db.py`:
```python
import os, pytest
from etva import db

KEY = os.urandom(32)

def test_open_and_schema(tmp_path):
    path = str(tmp_path / "app.db")
    conn = db.open_db(path, KEY)
    db.init_schema(conn)
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "roles", "permissions", "role_permissions", "user_roles",
            "clients", "client_assignments", "reconciliations",
            "invoices_company", "invoices_anaf", "differences",
            "audit_log"} <= tables
    conn.close()

def test_wrong_key_raises(tmp_path):
    path = str(tmp_path / "app.db")
    conn = db.open_db(path, KEY)
    db.init_schema(conn)
    conn.close()
    with pytest.raises(db.DbError):
        db.open_db(path, os.urandom(32))

def test_seed_roles_and_permissions(tmp_path):
    conn = db.open_db(str(tmp_path / "app.db"), KEY)
    db.init_schema(conn)
    perms = {r["code"] for r in conn.execute("SELECT code FROM permissions")}
    assert "useri.gestionare" in perms and len(perms) == 9
    roles = {r["name"] for r in conn.execute("SELECT name FROM roles")}
    assert roles == {"Admin", "Manager", "Contabil", "Junior"}
    admin_perms = {r["permission_code"] for r in conn.execute(
        "SELECT permission_code FROM role_permissions rp "
        "JOIN roles ro ON ro.id=rp.role_id WHERE ro.name='Admin'")}
    assert len(admin_perms) == 9
    db.init_schema(conn)  # idempotent
```

Run: `python -m pytest tests/test_db.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement `etva/db.py`**

```python
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
```

- [ ] **Step 3: Run tests** — `python -m pytest tests/test_db.py -v` — Expected: 3 PASS.

- [ ] **Step 4: Commit** — `git add etva/db.py tests/test_db.py && git commit -m "feat: encrypted DB layer with schema and seed roles"`

---

### Task 3: Auth (users + login)

**Files:**
- Create: `etva/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `db.open_db`, `db.init_schema` (tests build an in-memory-style tmp DB).
- Produces:
  - `auth.create_user(conn, username: str, password: str) -> int` (user id). Raises `auth.AuthError` if username taken.
  - `auth.verify_login(conn, username: str, password: str) -> int` (user id). Raises `auth.AuthError` on bad credentials or inactive user.
  - `auth.set_active(conn, user_id: int, active: bool) -> None` (soft delete).
  - `auth.AuthError(Exception)`

- [ ] **Step 1: Write failing tests**

`tests/test_auth.py`:
```python
import os, pytest
from etva import db, auth

@pytest.fixture
def conn(tmp_path):
    c = db.open_db(str(tmp_path / "a.db"), os.urandom(32))
    db.init_schema(c)
    yield c
    c.close()

def test_create_and_login(conn):
    uid = auth.create_user(conn, "ana", "Secret123!")
    assert auth.verify_login(conn, "ana", "Secret123!") == uid

def test_wrong_password(conn):
    auth.create_user(conn, "ana", "Secret123!")
    with pytest.raises(auth.AuthError):
        auth.verify_login(conn, "ana", "gresit")

def test_duplicate_username(conn):
    auth.create_user(conn, "ana", "x")
    with pytest.raises(auth.AuthError):
        auth.create_user(conn, "ana", "y")

def test_inactive_user_cannot_login(conn):
    uid = auth.create_user(conn, "ana", "Secret123!")
    auth.set_active(conn, uid, False)
    with pytest.raises(auth.AuthError):
        auth.verify_login(conn, "ana", "Secret123!")
```

Run: `python -m pytest tests/test_auth.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement `etva/auth.py`**

```python
"""User accounts and login (Argon2id password hashing)."""
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_ph = PasswordHasher()


class AuthError(Exception):
    pass


def create_user(conn, username: str, password: str) -> int:
    if conn.execute("SELECT 1 FROM users WHERE username=?",
                    (username,)).fetchone():
        raise AuthError("Numele de utilizator exista deja.")
    cur = conn.execute(
        "INSERT INTO users(username, password_hash) VALUES(?,?)",
        (username, _ph.hash(password)))
    conn.commit()
    return cur.lastrowid


def verify_login(conn, username: str, password: str) -> int:
    row = conn.execute(
        "SELECT id, password_hash, active FROM users WHERE username=?",
        (username,)).fetchone()
    if row is None or not row["active"]:
        raise AuthError("Utilizator sau parola incorecta.")
    try:
        _ph.verify(row["password_hash"], password)
    except VerifyMismatchError:
        raise AuthError("Utilizator sau parola incorecta.")
    return row["id"]


def set_active(conn, user_id: int, active: bool) -> None:
    conn.execute("UPDATE users SET active=? WHERE id=?",
                 (1 if active else 0, user_id))
    conn.commit()
```

- [ ] **Step 3: Run tests** — `python -m pytest tests/test_auth.py -v` — Expected: 4 PASS.

- [ ] **Step 4: Commit** — `git add etva/auth.py tests/test_auth.py && git commit -m "feat: user auth with argon2 and soft-delete"`

---

### Task 4: Roles & permissions

**Files:**
- Create: `etva/permissions.py`
- Test: `tests/test_permissions.py`

**Interfaces:**
- Consumes: `db` seed data, `auth.create_user`.
- Produces:
  - `permissions.assign_role(conn, user_id: int, role_name: str) -> None` — raises `PermError` if role unknown.
  - `permissions.create_role(conn, name: str, perm_codes: list[str]) -> int` — raises `PermError` on unknown code or duplicate name.
  - `permissions.update_role(conn, role_name: str, perm_codes: list[str]) -> None`
  - `permissions.user_permissions(conn, user_id: int) -> set[str]` — union across the user's roles.
  - `permissions.has_permission(conn, user_id: int, code: str) -> bool`
  - `permissions.PermError(Exception)`

- [ ] **Step 1: Write failing tests**

`tests/test_permissions.py`:
```python
import os, pytest
from etva import db, auth, permissions as pm

@pytest.fixture
def conn(tmp_path):
    c = db.open_db(str(tmp_path / "a.db"), os.urandom(32))
    db.init_schema(c)
    yield c
    c.close()

def test_assign_default_role(conn):
    uid = auth.create_user(conn, "ana", "x")
    pm.assign_role(conn, uid, "Junior")
    perms = pm.user_permissions(conn, uid)
    assert perms == {"reconciliere.creare", "reconciliere.editare"}
    assert not pm.has_permission(conn, uid, "rapoarte.export")

def test_union_of_roles(conn):
    uid = auth.create_user(conn, "ana", "x")
    pm.assign_role(conn, uid, "Junior")
    pm.assign_role(conn, uid, "Contabil")
    assert pm.has_permission(conn, uid, "rapoarte.export")

def test_custom_role(conn):
    uid = auth.create_user(conn, "ana", "x")
    pm.create_role(conn, "Auditor", ["audit.vizualizare"])
    pm.assign_role(conn, uid, "Auditor")
    assert pm.user_permissions(conn, uid) == {"audit.vizualizare"}

def test_unknown_permission_code(conn):
    with pytest.raises(pm.PermError):
        pm.create_role(conn, "Rau", ["nu.exista"])

def test_update_role(conn):
    uid = auth.create_user(conn, "ana", "x")
    pm.assign_role(conn, uid, "Junior")
    pm.update_role(conn, "Junior", ["rapoarte.export"])
    assert pm.user_permissions(conn, uid) == {"rapoarte.export"}
```

Run: `python -m pytest tests/test_permissions.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement `etva/permissions.py`**

```python
"""Configurable roles and permission resolution."""
from etva.db import PERMISSIONS


class PermError(Exception):
    pass


def _role_id(conn, role_name: str) -> int:
    row = conn.execute("SELECT id FROM roles WHERE name=?",
                       (role_name,)).fetchone()
    if row is None:
        raise PermError(f"Rol necunoscut: {role_name}")
    return row["id"]


def _check_codes(perm_codes):
    unknown = [c for c in perm_codes if c not in PERMISSIONS]
    if unknown:
        raise PermError(f"Permisiuni necunoscute: {unknown}")


def create_role(conn, name: str, perm_codes: list) -> int:
    _check_codes(perm_codes)
    if conn.execute("SELECT 1 FROM roles WHERE name=?", (name,)).fetchone():
        raise PermError("Rolul exista deja.")
    cur = conn.execute("INSERT INTO roles(name) VALUES(?)", (name,))
    for c in perm_codes:
        conn.execute("INSERT INTO role_permissions VALUES(?,?)",
                     (cur.lastrowid, c))
    conn.commit()
    return cur.lastrowid


def update_role(conn, role_name: str, perm_codes: list) -> None:
    _check_codes(perm_codes)
    rid = _role_id(conn, role_name)
    conn.execute("DELETE FROM role_permissions WHERE role_id=?", (rid,))
    for c in perm_codes:
        conn.execute("INSERT INTO role_permissions VALUES(?,?)", (rid, c))
    conn.commit()


def assign_role(conn, user_id: int, role_name: str) -> None:
    rid = _role_id(conn, role_name)
    conn.execute("INSERT OR IGNORE INTO user_roles VALUES(?,?)",
                 (user_id, rid))
    conn.commit()


def user_permissions(conn, user_id: int) -> set:
    rows = conn.execute(
        "SELECT DISTINCT rp.permission_code FROM role_permissions rp "
        "JOIN user_roles ur ON ur.role_id = rp.role_id WHERE ur.user_id=?",
        (user_id,))
    return {r["permission_code"] for r in rows}


def has_permission(conn, user_id: int, code: str) -> bool:
    return code in user_permissions(conn, user_id)
```

- [ ] **Step 3: Run tests** — `python -m pytest tests/test_permissions.py -v` — Expected: 5 PASS.

- [ ] **Step 4: Commit** — `git add etva/permissions.py tests/test_permissions.py && git commit -m "feat: configurable roles and permission resolution"`

---

### Task 5: Audit log (append-only)

**Files:**
- Create: `etva/audit.py`
- Test: `tests/test_audit.py`

**Interfaces:**
- Consumes: DB connection.
- Produces:
  - `audit.log(conn, user_id: int | None, action: str, entity: str | None = None, entity_id: str | None = None) -> None` — UTC ISO timestamp.
  - `audit.entries(conn, limit: int = 200) -> list[dict]` — newest first.
  - Module intentionally exposes NO update/delete function.

- [ ] **Step 1: Write failing tests**

`tests/test_audit.py`:
```python
import os, pytest
from etva import db, audit

@pytest.fixture
def conn(tmp_path):
    c = db.open_db(str(tmp_path / "a.db"), os.urandom(32))
    db.init_schema(c)
    yield c
    c.close()

def test_log_and_read(conn):
    audit.log(conn, 1, "login")
    audit.log(conn, 1, "client.creare", "client", "42")
    rows = audit.entries(conn)
    assert len(rows) == 2
    assert rows[0]["action"] == "client.creare"  # newest first
    assert rows[0]["entity_id"] == "42"
    assert rows[1]["ts"]  # timestamp present

def test_no_mutation_api():
    assert not hasattr(audit, "delete")
    assert not hasattr(audit, "update")
```

Run: `python -m pytest tests/test_audit.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement `etva/audit.py`**

```python
"""Append-only audit log. No update/delete is exposed, by design."""
from datetime import datetime, timezone


def log(conn, user_id, action, entity=None, entity_id=None) -> None:
    conn.execute(
        "INSERT INTO audit_log(user_id, action, entity, entity_id, ts) "
        "VALUES(?,?,?,?,?)",
        (user_id, action, entity, entity_id,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()


def entries(conn, limit: int = 200) -> list:
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]
```

- [ ] **Step 3: Run tests** — `python -m pytest tests/test_audit.py -v` — Expected: 2 PASS.

- [ ] **Step 4: Commit** — `git add etva/audit.py tests/test_audit.py && git commit -m "feat: append-only audit log"`

---

### Task 6: Clients + assignments

**Files:**
- Create: `etva/clients.py`
- Test: `tests/test_clients.py`

**Interfaces:**
- Consumes: DB connection, `permissions.user_permissions` (for visibility rule).
- Produces:
  - `clients.create_client(conn, cui: str, name: str) -> int` — raises `ClientError` on duplicate CUI.
  - `clients.assign(conn, user_id: int, client_id: int) -> None`
  - `clients.visible_clients(conn, user_id: int) -> list[dict]` — ALL clients if the user has `useri.gestionare` (Admin) or `clienti.stergere`-less Manager rule: users with `clienti.creare` see all; otherwise only assigned ones. Exact rule: if user has `clienti.creare` OR `useri.gestionare` → all clients; else only those in `client_assignments`.
  - `clients.delete_client(conn, client_id: int) -> None`
  - `clients.ClientError(Exception)`

- [ ] **Step 1: Write failing tests**

`tests/test_clients.py`:
```python
import os, pytest
from etva import db, auth, permissions as pm, clients

@pytest.fixture
def conn(tmp_path):
    c = db.open_db(str(tmp_path / "a.db"), os.urandom(32))
    db.init_schema(c)
    yield c
    c.close()

def test_create_and_duplicate(conn):
    clients.create_client(conn, "RO123", "Firma SRL")
    with pytest.raises(clients.ClientError):
        clients.create_client(conn, "RO123", "Alta")

def test_visibility_assigned_only(conn):
    uid = auth.create_user(conn, "ana", "x")
    pm.assign_role(conn, uid, "Contabil")
    c1 = clients.create_client(conn, "RO1", "A")
    clients.create_client(conn, "RO2", "B")
    clients.assign(conn, uid, c1)
    vis = clients.visible_clients(conn, uid)
    assert [c["cui"] for c in vis] == ["RO1"]

def test_visibility_manager_sees_all(conn):
    uid = auth.create_user(conn, "boss", "x")
    pm.assign_role(conn, uid, "Manager")
    clients.create_client(conn, "RO1", "A")
    clients.create_client(conn, "RO2", "B")
    assert len(clients.visible_clients(conn, uid)) == 2

def test_delete(conn):
    cid = clients.create_client(conn, "RO1", "A")
    clients.delete_client(conn, cid)
    assert clients.visible_clients(conn, 999) == []
```

Run: `python -m pytest tests/test_clients.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement `etva/clients.py`**

```python
"""Clients CRUD and user-client assignments."""
from etva.permissions import user_permissions


class ClientError(Exception):
    pass


def create_client(conn, cui: str, name: str) -> int:
    if conn.execute("SELECT 1 FROM clients WHERE cui=?", (cui,)).fetchone():
        raise ClientError("Exista deja un client cu acest CUI.")
    cur = conn.execute("INSERT INTO clients(cui, name) VALUES(?,?)",
                       (cui, name))
    conn.commit()
    return cur.lastrowid


def assign(conn, user_id: int, client_id: int) -> None:
    conn.execute("INSERT OR IGNORE INTO client_assignments VALUES(?,?)",
                 (user_id, client_id))
    conn.commit()


def visible_clients(conn, user_id: int) -> list:
    perms = user_permissions(conn, user_id)
    if "clienti.creare" in perms or "useri.gestionare" in perms:
        rows = conn.execute("SELECT * FROM clients ORDER BY name")
    else:
        rows = conn.execute(
            "SELECT c.* FROM clients c JOIN client_assignments a "
            "ON a.client_id = c.id WHERE a.user_id=? ORDER BY c.name",
            (user_id,))
    return [dict(r) for r in rows]


def delete_client(conn, client_id: int) -> None:
    conn.execute("DELETE FROM client_assignments WHERE client_id=?",
                 (client_id,))
    conn.execute("DELETE FROM clients WHERE id=?", (client_id,))
    conn.commit()
```

- [ ] **Step 3: Run tests** — `python -m pytest tests/test_clients.py -v` — Expected: 4 PASS.

- [ ] **Step 4: Commit** — `git add etva/clients.py tests/test_clients.py && git commit -m "feat: clients CRUD with per-user visibility"`

---

### Task 7: Company journal importer

**Files:**
- Create: `etva/importer/__init__.py` (empty), `etva/importer/company.py`
- Test: `tests/test_importer_company.py`

**Interfaces:**
- Produces:
  - `company.parse_company_journal(path: str) -> list[dict]` — reads `.xlsx`/`.csv`. Required source columns (Romanian, exact): `cui_partener`, `nr_factura`, `data`, `baza`, `tva`, `categorie`. Returns canonical rows: `{"partner_cui", "invoice_no", "date", "base": float, "vat": float, "category"}`. Raises `company.ImportError_` listing every bad row/column — all-or-nothing, never partial.
  - `company.ImportError_(Exception)` with attribute `errors: list[str]`.

- [ ] **Step 1: Write failing tests**

`tests/test_importer_company.py`:
```python
import pandas as pd, pytest
from etva.importer import company

GOOD = pd.DataFrame({
    "cui_partener": ["RO111", "RO222"],
    "nr_factura": ["F1", "F2"],
    "data": ["2026-01-10", "2026-01-15"],
    "baza": [100.0, 200.0],
    "tva": [19.0, 38.0],
    "categorie": ["livrari_interne", "achizitii_interne"],
})

def test_parse_xlsx(tmp_path):
    p = str(tmp_path / "j.xlsx")
    GOOD.to_excel(p, index=False)
    rows = company.parse_company_journal(p)
    assert rows[0] == {"partner_cui": "RO111", "invoice_no": "F1",
                       "date": "2026-01-10", "base": 100.0, "vat": 19.0,
                       "category": "livrari_interne"}

def test_parse_csv(tmp_path):
    p = str(tmp_path / "j.csv")
    GOOD.to_csv(p, index=False)
    assert len(company.parse_company_journal(p)) == 2

def test_missing_column_rejected(tmp_path):
    p = str(tmp_path / "j.csv")
    GOOD.drop(columns=["tva"]).to_csv(p, index=False)
    with pytest.raises(company.ImportError_) as e:
        company.parse_company_journal(p)
    assert "tva" in str(e.value)

def test_bad_number_rejected_entirely(tmp_path):
    bad = GOOD.copy()
    bad.loc[1, "baza"] = "abc"
    p = str(tmp_path / "j.csv")
    bad.to_csv(p, index=False)
    with pytest.raises(company.ImportError_) as e:
        company.parse_company_journal(p)
    assert "baza" in str(e.value) and "3" in str(e.value)  # file row number
```

Run: `python -m pytest tests/test_importer_company.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement `etva/importer/company.py`**

```python
"""Company sales/purchases journal parser (D300/394-style columns)."""
import pandas as pd

REQUIRED = ["cui_partener", "nr_factura", "data", "baza", "tva", "categorie"]
_CANON = {"cui_partener": "partner_cui", "nr_factura": "invoice_no",
          "data": "date", "baza": "base", "tva": "vat",
          "categorie": "category"}


class ImportError_(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(errors))


def _read(path: str) -> pd.DataFrame:
    if path.lower().endswith(".csv"):
        return pd.read_csv(path, dtype=str)
    return pd.read_excel(path, dtype=str)


def rows_from_dataframe(df: pd.DataFrame, required=REQUIRED,
                        canon=_CANON) -> list:
    errors = []
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ImportError_([f"Coloane lipsa: {', '.join(missing)}"])
    rows = []
    for idx, rec in df.iterrows():
        file_row = idx + 2  # 1-based + header row
        row = {}
        for src, dst in canon.items():
            val = rec[src]
            if pd.isna(val) or str(val).strip() == "":
                errors.append(f"Rand {file_row}: '{src}' este gol")
                continue
            if dst in ("base", "vat"):
                try:
                    row[dst] = float(str(val).replace(",", "."))
                except ValueError:
                    errors.append(
                        f"Rand {file_row}: '{src}' nu este numeric ({val})")
            else:
                row[dst] = str(val).strip()
        rows.append(row)
    if errors:
        raise ImportError_(errors)
    return rows


def parse_company_journal(path: str) -> list:
    return rows_from_dataframe(_read(path))
```

- [ ] **Step 3: Run tests** — `python -m pytest tests/test_importer_company.py -v` — Expected: 4 PASS.

- [ ] **Step 4: Commit** — `git add etva/importer tests/test_importer_company.py && git commit -m "feat: company journal importer with strict validation"`

---

### Task 8: ANAF data source (interface + file implementation with column mapping)

**Files:**
- Create: `etva/importer/anaf.py`
- Test: `tests/test_importer_anaf.py`

**Interfaces:**
- Consumes: `company.rows_from_dataframe`, `company.ImportError_`.
- Produces:
  - `anaf.AnafDataSource` — ABC with abstract `get_etva_data(self, cui: str, period: str) -> list[dict]` (canonical rows, same shape as Task 7).
  - `anaf.FileAnafDataSource(path: str, column_mapping: dict[str, str])` — `column_mapping` maps canonical Romanian names (`cui_partener`, `nr_factura`, `data`, `baza`, `tva`, `categorie`) to whatever the ANAF file actually calls them. Supports `.xlsx`/`.csv`. `cui`/`period` args are accepted and ignored by this implementation (the file IS the data for that client+period).
  - `anaf.DEFAULT_MAPPING: dict` — identity mapping.
  - Future live-API connector implements the same ABC; nothing else changes.

- [ ] **Step 1: Write failing tests**

`tests/test_importer_anaf.py`:
```python
import pandas as pd, pytest
from etva.importer import anaf, company

def test_file_source_with_mapping(tmp_path):
    df = pd.DataFrame({
        "CIF": ["RO111"], "Numar": ["F1"], "Data doc": ["2026-01-10"],
        "Baza impozabila": ["100"], "TVA": ["19"], "Tip": ["livrari_interne"],
    })
    p = str(tmp_path / "anaf.csv")
    df.to_csv(p, index=False)
    mapping = {"cui_partener": "CIF", "nr_factura": "Numar",
               "data": "Data doc", "baza": "Baza impozabila",
               "tva": "TVA", "categorie": "Tip"}
    src = anaf.FileAnafDataSource(p, mapping)
    rows = src.get_etva_data("RO999", "2026-01")
    assert rows == [{"partner_cui": "RO111", "invoice_no": "F1",
                     "date": "2026-01-10", "base": 100.0, "vat": 19.0,
                     "category": "livrari_interne"}]

def test_is_abstract():
    with pytest.raises(TypeError):
        anaf.AnafDataSource()

def test_bad_mapping_rejected(tmp_path):
    df = pd.DataFrame({"X": ["1"]})
    p = str(tmp_path / "anaf.csv")
    df.to_csv(p, index=False)
    src = anaf.FileAnafDataSource(p, anaf.DEFAULT_MAPPING)
    with pytest.raises(company.ImportError_):
        src.get_etva_data("RO999", "2026-01")
```

Run: `python -m pytest tests/test_importer_anaf.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement `etva/importer/anaf.py`**

```python
"""ANAF e-TVA data source. The official format is not yet published, so the
file-based implementation uses a configurable column mapping. A future live
API connector implements the same interface."""
from abc import ABC, abstractmethod
import pandas as pd
from etva.importer.company import rows_from_dataframe, REQUIRED

DEFAULT_MAPPING = {c: c for c in REQUIRED}


class AnafDataSource(ABC):
    @abstractmethod
    def get_etva_data(self, cui: str, period: str) -> list:
        ...


class FileAnafDataSource(AnafDataSource):
    def __init__(self, path: str, column_mapping: dict = None):
        self.path = path
        self.mapping = column_mapping or DEFAULT_MAPPING

    def get_etva_data(self, cui: str, period: str) -> list:
        if self.path.lower().endswith(".csv"):
            df = pd.read_csv(self.path, dtype=str)
        else:
            df = pd.read_excel(self.path, dtype=str)
        # Rename actual file columns to canonical Romanian names first.
        rename = {actual: canon for canon, actual in self.mapping.items()}
        df = df.rename(columns=rename)
        return rows_from_dataframe(df)
```

- [ ] **Step 3: Run tests** — `python -m pytest tests/test_importer_anaf.py -v` — Expected: 3 PASS.

- [ ] **Step 4: Commit** — `git add etva/importer/anaf.py tests/test_importer_anaf.py && git commit -m "feat: pluggable AnafDataSource with file-based implementation"`

---

### Task 9: Reconciliation engine

**Files:**
- Create: `etva/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: canonical rows from Tasks 7-8.
- Produces:
  - `engine.reconcile(company_rows: list[dict], anaf_rows: list[dict], tolerance: float = 1.0) -> ReconcileResult`
  - `engine.ReconcileResult` dataclass:
    - `totals_company: dict[str, dict]` — category → `{"base": float, "vat": float}`
    - `totals_anaf: dict[str, dict]` — same shape
    - `differences: list[dict]` — each: `{"diff_type": str, "partner_cui": str, "invoice_no": str, "category": str, "company": {"base","vat"}|None, "anaf": {"base","vat"}|None, "delta_base": float, "delta_vat": float}`
  - Matching key: `(partner_cui, invoice_no)`. Per-key sums compared with tolerance. Duplicates (key appears >1 time in one source) produce a `duplicat` diff for that source AND still participate (summed) in matching.

- [ ] **Step 1: Write failing tests**

`tests/test_engine.py`:
```python
from etva.engine import reconcile

def row(cui="RO1", no="F1", base=100.0, vat=19.0, cat="livrari_interne"):
    return {"partner_cui": cui, "invoice_no": no, "date": "2026-01-10",
            "base": base, "vat": vat, "category": cat}

def test_perfect_match_no_diffs():
    r = reconcile([row()], [row()])
    assert r.differences == []
    assert r.totals_company["livrari_interne"] == {"base": 100.0, "vat": 19.0}

def test_tolerance_swallows_rounding():
    r = reconcile([row(base=100.0)], [row(base=100.9)], tolerance=1.0)
    assert r.differences == []

def test_amount_difference():
    r = reconcile([row(base=100.0)], [row(base=150.0)])
    d = r.differences[0]
    assert d["diff_type"] == "suma_diferita"
    assert d["delta_base"] == -50.0

def test_missing_in_anaf():
    r = reconcile([row()], [])
    assert r.differences[0]["diff_type"] == "lipsa_in_anaf"
    assert r.differences[0]["anaf"] is None

def test_missing_at_company():
    r = reconcile([], [row()])
    assert r.differences[0]["diff_type"] == "lipsa_la_companie"
    assert r.differences[0]["company"] is None

def test_duplicate_flagged_and_summed():
    r = reconcile([row(base=50.0), row(base=50.0)], [row(base=100.0)])
    types = sorted(d["diff_type"] for d in r.differences)
    assert types == ["duplicat"]  # sums match, only the duplicate flag remains

def test_totals_by_category():
    r = reconcile([row(cat="livrari_interne"),
                   row(no="F2", cat="achizitii_interne", base=200.0, vat=38.0)],
                  [])
    assert r.totals_company["achizitii_interne"]["base"] == 200.0
```

Run: `python -m pytest tests/test_engine.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement `etva/engine.py`**

```python
"""Reconciliation engine: invoice-level matching + category totals."""
from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class ReconcileResult:
    totals_company: dict = field(default_factory=dict)
    totals_anaf: dict = field(default_factory=dict)
    differences: list = field(default_factory=list)


def _totals(rows) -> dict:
    out = defaultdict(lambda: {"base": 0.0, "vat": 0.0})
    for r in rows:
        out[r["category"]]["base"] += r["base"]
        out[r["category"]]["vat"] += r["vat"]
    return {k: {"base": round(v["base"], 2), "vat": round(v["vat"], 2)}
            for k, v in out.items()}


def _group(rows) -> dict:
    grouped = {}
    for r in rows:
        key = (r["partner_cui"], r["invoice_no"])
        g = grouped.setdefault(key, {"base": 0.0, "vat": 0.0, "count": 0,
                                     "category": r["category"]})
        g["base"] += r["base"]
        g["vat"] += r["vat"]
        g["count"] += 1
    return grouped


def reconcile(company_rows, anaf_rows, tolerance: float = 1.0) -> ReconcileResult:
    result = ReconcileResult(totals_company=_totals(company_rows),
                             totals_anaf=_totals(anaf_rows))
    comp, anaf = _group(company_rows), _group(anaf_rows)

    def diff(dtype, key, c, a):
        result.differences.append({
            "diff_type": dtype, "partner_cui": key[0], "invoice_no": key[1],
            "category": (c or a)["category"],
            "company": {"base": c["base"], "vat": c["vat"]} if c else None,
            "anaf": {"base": a["base"], "vat": a["vat"]} if a else None,
            "delta_base": round((c["base"] if c else 0) - (a["base"] if a else 0), 2),
            "delta_vat": round((c["vat"] if c else 0) - (a["vat"] if a else 0), 2),
        })

    for key, g in comp.items():
        if g["count"] > 1:
            diff("duplicat", key, g, anaf.get(key))
    for key, g in anaf.items():
        if g["count"] > 1 and comp.get(key, {}).get("count", 0) <= 1:
            diff("duplicat", key, comp.get(key), g)

    for key, c in comp.items():
        a = anaf.get(key)
        if a is None:
            diff("lipsa_in_anaf", key, c, None)
        elif (abs(c["base"] - a["base"]) > tolerance
              or abs(c["vat"] - a["vat"]) > tolerance):
            diff("suma_diferita", key, c, a)
    for key, a in anaf.items():
        if key not in comp:
            diff("lipsa_la_companie", key, None, a)
    return result
```

- [ ] **Step 3: Run tests** — `python -m pytest tests/test_engine.py -v` — Expected: 7 PASS.

- [ ] **Step 4: Commit** — `git add etva/engine.py tests/test_engine.py && git commit -m "feat: reconciliation engine with tolerance and duplicate detection"`

---

### Task 10: Correction advisor

**Files:**
- Create: `etva/advisor.py`
- Test: `tests/test_advisor.py`

**Interfaces:**
- Consumes: `engine.ReconcileResult`.
- Produces:
  - `advisor.suggest_d300(result: ReconcileResult) -> list[dict]` — one entry per category present on either side: `{"category": str, "company_base", "company_vat", "anaf_base", "anaf_vat", "suggested_base", "suggested_vat", "status": "ok"|"de_verificat"}`. Suggested value = ANAF value when the category has any difference (informative starting point, user decides), company value otherwise. `status="de_verificat"` iff any difference touches that category.

- [ ] **Step 1: Write failing tests**

`tests/test_advisor.py`:
```python
from etva.engine import reconcile
from etva.advisor import suggest_d300

def row(cui="RO1", no="F1", base=100.0, vat=19.0, cat="livrari_interne"):
    return {"partner_cui": cui, "invoice_no": no, "date": "2026-01-10",
            "base": base, "vat": vat, "category": cat}

def test_clean_category_keeps_company_values():
    r = reconcile([row()], [row()])
    s = suggest_d300(r)
    assert s == [{"category": "livrari_interne", "company_base": 100.0,
                  "company_vat": 19.0, "anaf_base": 100.0, "anaf_vat": 19.0,
                  "suggested_base": 100.0, "suggested_vat": 19.0,
                  "status": "ok"}]

def test_diff_category_suggests_anaf_values():
    r = reconcile([row(base=100.0, vat=19.0)], [row(base=150.0, vat=28.5)])
    s = suggest_d300(r)[0]
    assert s["status"] == "de_verificat"
    assert s["suggested_base"] == 150.0 and s["suggested_vat"] == 28.5

def test_category_only_at_anaf():
    r = reconcile([], [row()])
    s = suggest_d300(r)[0]
    assert s["company_base"] == 0.0 and s["suggested_base"] == 100.0
    assert s["status"] == "de_verificat"
```

Run: `python -m pytest tests/test_advisor.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement `etva/advisor.py`**

```python
"""Suggested corrected D300 values per category. Informative only."""


def suggest_d300(result) -> list:
    flagged = {d["category"] for d in result.differences}
    cats = sorted(set(result.totals_company) | set(result.totals_anaf))
    out = []
    for cat in cats:
        c = result.totals_company.get(cat, {"base": 0.0, "vat": 0.0})
        a = result.totals_anaf.get(cat, {"base": 0.0, "vat": 0.0})
        dirty = cat in flagged
        src = a if dirty else c
        out.append({"category": cat,
                    "company_base": c["base"], "company_vat": c["vat"],
                    "anaf_base": a["base"], "anaf_vat": a["vat"],
                    "suggested_base": src["base"],
                    "suggested_vat": src["vat"],
                    "status": "de_verificat" if dirty else "ok"})
    return out
```

- [ ] **Step 3: Run tests** — `python -m pytest tests/test_advisor.py -v` — Expected: 3 PASS.

- [ ] **Step 4: Commit** — `git add etva/advisor.py tests/test_advisor.py && git commit -m "feat: D300 correction advisor"`

---

### Task 11: Excel export

**Files:**
- Create: `etva/export.py`
- Test: `tests/test_export.py`

**Interfaces:**
- Consumes: `ReconcileResult`, `suggest_d300` output.
- Produces:
  - `export.write_report(result, suggestions: list[dict], path: str, client_name: str, period: str) -> None` — workbook with sheets `Sumar` (per-category totals + suggestions, `de_verificat` rows red-filled) and `Diferente` (one row per difference).

- [ ] **Step 1: Write failing tests**

`tests/test_export.py`:
```python
from openpyxl import load_workbook
from etva.engine import reconcile
from etva.advisor import suggest_d300
from etva import export

def row(cui="RO1", no="F1", base=100.0, vat=19.0, cat="livrari_interne"):
    return {"partner_cui": cui, "invoice_no": no, "date": "2026-01-10",
            "base": base, "vat": vat, "category": cat}

def test_report_structure(tmp_path):
    r = reconcile([row()], [row(base=200.0)])
    p = str(tmp_path / "raport.xlsx")
    export.write_report(r, suggest_d300(r), p, "Firma SRL", "2026-01")
    wb = load_workbook(p)
    assert wb.sheetnames == ["Sumar", "Diferente"]
    sumar = wb["Sumar"]
    assert sumar["A1"].value == "Client: Firma SRL"
    assert sumar["A2"].value == "Perioada: 2026-01"
    diffs = wb["Diferente"]
    assert diffs["A1"].value == "Tip diferenta"
    assert diffs["A2"].value == "suma_diferita"

def test_flagged_row_is_red(tmp_path):
    r = reconcile([row()], [row(base=200.0)])
    p = str(tmp_path / "raport.xlsx")
    export.write_report(r, suggest_d300(r), p, "F", "2026-01")
    sumar = load_workbook(p)["Sumar"]
    # data starts at row 5 (title, period, blank, header)
    assert sumar.cell(row=5, column=1).fill.start_color.rgb == "00FFC7CE"
```

Run: `python -m pytest tests/test_export.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement `etva/export.py`**

```python
"""Excel report: summary with suggestions + detailed differences."""
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

_RED = PatternFill("solid", start_color="FFC7CE")
_BOLD = Font(bold=True)

_SUMAR_HEADER = ["Categorie", "Baza firma", "TVA firma", "Baza ANAF",
                 "TVA ANAF", "Baza sugerata", "TVA sugerata", "Status"]
_DIFF_HEADER = ["Tip diferenta", "CUI partener", "Nr factura", "Categorie",
                "Baza firma", "TVA firma", "Baza ANAF", "TVA ANAF",
                "Delta baza", "Delta TVA"]


def write_report(result, suggestions, path, client_name, period) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sumar"
    ws["A1"] = f"Client: {client_name}"
    ws["A2"] = f"Perioada: {period}"
    ws["A1"].font = ws["A2"].font = _BOLD
    ws.append([])
    ws.append(_SUMAR_HEADER)
    for cell in ws[4]:
        cell.font = _BOLD
    for s in suggestions:
        ws.append([s["category"], s["company_base"], s["company_vat"],
                   s["anaf_base"], s["anaf_vat"], s["suggested_base"],
                   s["suggested_vat"], s["status"]])
        if s["status"] == "de_verificat":
            for cell in ws[ws.max_row]:
                cell.fill = _RED

    wd = wb.create_sheet("Diferente")
    wd.append(_DIFF_HEADER)
    for cell in wd[1]:
        cell.font = _BOLD
    for d in result.differences:
        c, a = d["company"], d["anaf"]
        wd.append([d["diff_type"], d["partner_cui"], d["invoice_no"],
                   d["category"],
                   c["base"] if c else "", c["vat"] if c else "",
                   a["base"] if a else "", a["vat"] if a else "",
                   d["delta_base"], d["delta_vat"]])
    wb.save(path)
```

- [ ] **Step 3: Run tests** — `python -m pytest tests/test_export.py -v` — Expected: 2 PASS.

- [ ] **Step 4: Commit** — `git add etva/export.py tests/test_export.py && git commit -m "feat: Excel report export with highlighted differences"`

---

### Task 12: Flask API with permission guards + persistence of reconciliations

**Files:**
- Create: `etva/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `server.create_app(conn, upload_dir: str) -> Flask`. Session = signed cookie holding `user_id` (Flask session, random secret per run). Routes (all JSON unless noted):
  - `POST /api/login` `{username, password}` → `{user_id, permissions: [...]}`; audits `login`. 401 on failure.
  - `POST /api/logout` → 200.
  - `GET /api/me` → `{user_id, permissions}` or 401.
  - `GET /api/clients` → visible clients for current user.
  - `POST /api/clients` `{cui, name}` — needs `clienti.creare`; audits.
  - `DELETE /api/clients/<id>` — needs `clienti.stergere`; audits.
  - `POST /api/reconciliations` — multipart form: `client_id`, `period`, files `company_file`, `anaf_file`, optional `anaf_mapping` (JSON string) → runs importers + engine + advisor, persists invoices and differences, returns `{id, totals_company, totals_anaf, differences, suggestions}`. Needs `reconciliere.creare`; audits. 400 with `{errors: [...]}` on ImportError_.
  - `GET /api/reconciliations/<id>` → stored result (recomputed from stored invoices).
  - `GET /api/reconciliations/<id>/export` — needs `rapoarte.export`; audits; returns the .xlsx file (Flask `send_file`).
  - `GET /api/audit` — needs `audit.vizualizare`.
  - Admin: `GET/POST /api/admin/users`, `POST /api/admin/users/<id>/deactivate`, `POST /api/admin/roles`, `PUT /api/admin/roles/<name>`, `POST /api/admin/assign` `{user_id, client_id}` — all need `useri.gestionare`; all audited.
  - 403 JSON `{"error": "Acces interzis"}` when permission missing.

- [ ] **Step 1: Write failing tests**

`tests/test_server.py`:
```python
import io, os, json, pytest, pandas as pd
from etva import db, auth, permissions as pm, clients
from etva.server import create_app

@pytest.fixture
def app(tmp_path):
    conn = db.open_db(str(tmp_path / "a.db"), os.urandom(32))
    db.init_schema(conn)
    uid = auth.create_user(conn, "admin", "Parola123!")
    pm.assign_role(conn, uid, "Admin")
    jid = auth.create_user(conn, "junior", "Parola123!")
    pm.assign_role(conn, jid, "Junior")
    application = create_app(conn, str(tmp_path))
    application.config["TESTING"] = True
    return application

def login(client, user="admin"):
    r = client.post("/api/login",
                    json={"username": user, "password": "Parola123!"})
    assert r.status_code == 200
    return r

def _csv(df):
    return io.BytesIO(df.to_csv(index=False).encode())

def _journal():
    return pd.DataFrame({"cui_partener": ["RO1"], "nr_factura": ["F1"],
                         "data": ["2026-01-10"], "baza": ["100"],
                         "tva": ["19"], "categorie": ["livrari_interne"]})

def test_login_bad_password(app):
    c = app.test_client()
    r = c.post("/api/login", json={"username": "admin", "password": "x"})
    assert r.status_code == 401

def test_full_reconciliation_flow(app):
    c = app.test_client()
    login(c)
    r = c.post("/api/clients", json={"cui": "RO9", "name": "Firma"})
    cid = r.get_json()["id"]
    anaf = _journal(); anaf.loc[0, "baza"] = "150"
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-01",
        "company_file": (_csv(_journal()), "j.csv"),
        "anaf_file": (_csv(anaf), "a.csv"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    assert body["differences"][0]["diff_type"] == "suma_diferita"
    assert body["suggestions"][0]["status"] == "de_verificat"
    rid = body["id"]
    r = c.get(f"/api/reconciliations/{rid}")
    assert r.get_json()["differences"][0]["diff_type"] == "suma_diferita"
    r = c.get(f"/api/reconciliations/{rid}/export")
    assert r.status_code == 200
    assert r.data[:2] == b"PK"  # xlsx zip magic

def test_junior_cannot_export(app):
    c = app.test_client()
    login(c, "junior")
    r = c.get("/api/reconciliations/1/export")
    assert r.status_code == 403

def test_junior_cannot_manage_users(app):
    c = app.test_client()
    login(c, "junior")
    r = c.post("/api/admin/users",
               json={"username": "x", "password": "y", "role": "Junior"})
    assert r.status_code == 403

def test_import_errors_returned(app):
    c = app.test_client()
    login(c)
    r = c.post("/api/clients", json={"cui": "RO9", "name": "Firma"})
    cid = r.get_json()["id"]
    bad = _journal().drop(columns=["tva"])
    r = c.post("/api/reconciliations", data={
        "client_id": str(cid), "period": "2026-01",
        "company_file": (_csv(bad), "j.csv"),
        "anaf_file": (_csv(_journal()), "a.csv"),
    }, content_type="multipart/form-data")
    assert r.status_code == 400
    assert "tva" in r.get_json()["errors"][0]

def test_audit_written(app):
    c = app.test_client()
    login(c)
    r = c.get("/api/audit")
    assert r.status_code == 200
    assert r.get_json()[0]["action"] == "login"
```

Run: `python -m pytest tests/test_server.py -v` — Expected: FAIL.

- [ ] **Step 2: Implement `etva/server.py`**

```python
"""Local Flask API. Runs only on 127.0.0.1, rendered inside pywebview."""
import io, json, os, secrets
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, request, session, jsonify, send_file
from etva import auth, audit, clients, permissions as pm
from etva.importer.company import parse_company_journal, ImportError_, rows_from_dataframe
from etva.importer.anaf import FileAnafDataSource
from etva.engine import reconcile, ReconcileResult
from etva.advisor import suggest_d300
from etva import export as export_mod


def create_app(conn, upload_dir: str) -> Flask:
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(32)

    def current_user():
        return session.get("user_id")

    def require(perm=None):
        def deco(fn):
            @wraps(fn)
            def wrapper(*a, **kw):
                uid = current_user()
                if uid is None:
                    return jsonify({"error": "Neautentificat"}), 401
                if perm and not pm.has_permission(conn, uid, perm):
                    return jsonify({"error": "Acces interzis"}), 403
                return fn(uid, *a, **kw)
            return wrapper
        return deco

    @app.post("/api/login")
    def login():
        data = request.get_json(force=True)
        try:
            uid = auth.verify_login(conn, data["username"], data["password"])
        except auth.AuthError as e:
            return jsonify({"error": str(e)}), 401
        session["user_id"] = uid
        audit.log(conn, uid, "login")
        return jsonify({"user_id": uid,
                        "permissions": sorted(pm.user_permissions(conn, uid))})

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify({"ok": True})

    @app.get("/api/me")
    @require()
    def me(uid):
        return jsonify({"user_id": uid,
                        "permissions": sorted(pm.user_permissions(conn, uid))})

    @app.get("/api/clients")
    @require()
    def list_clients(uid):
        return jsonify(clients.visible_clients(conn, uid))

    @app.post("/api/clients")
    @require("clienti.creare")
    def add_client(uid):
        data = request.get_json(force=True)
        try:
            cid = clients.create_client(conn, data["cui"], data["name"])
        except clients.ClientError as e:
            return jsonify({"error": str(e)}), 400
        audit.log(conn, uid, "client.creare", "client", str(cid))
        return jsonify({"id": cid})

    @app.delete("/api/clients/<int:cid>")
    @require("clienti.stergere")
    def del_client(uid, cid):
        clients.delete_client(conn, cid)
        audit.log(conn, uid, "client.stergere", "client", str(cid))
        return jsonify({"ok": True})

    def _save_upload(f):
        path = os.path.join(upload_dir, secrets.token_hex(8) + "_" + f.filename)
        f.save(path)
        return path

    def _persist(uid, client_id, period, comp_rows, anaf_rows):
        cur = conn.execute(
            "INSERT INTO reconciliations(client_id, period, created_at, "
            "created_by) VALUES(?,?,?,?)",
            (client_id, period,
             datetime.now(timezone.utc).isoformat(), uid))
        rid = cur.lastrowid
        for table, rows in (("invoices_company", comp_rows),
                            ("invoices_anaf", anaf_rows)):
            conn.executemany(
                f"INSERT INTO {table}(reconciliation_id, partner_cui, "
                "invoice_no, date, base, vat, category) VALUES(?,?,?,?,?,?,?)",
                [(rid, r["partner_cui"], r["invoice_no"], r["date"],
                  r["base"], r["vat"], r["category"]) for r in rows])
        conn.commit()
        return rid

    def _result_payload(rid, comp_rows, anaf_rows):
        result = reconcile(comp_rows, anaf_rows)
        conn.execute("DELETE FROM differences WHERE reconciliation_id=?", (rid,))
        conn.executemany(
            "INSERT INTO differences(reconciliation_id, diff_type, details) "
            "VALUES(?,?,?)",
            [(rid, d["diff_type"], json.dumps(d)) for d in result.differences])
        conn.commit()
        return {"id": rid,
                "totals_company": result.totals_company,
                "totals_anaf": result.totals_anaf,
                "differences": result.differences,
                "suggestions": suggest_d300(result)}

    @app.post("/api/reconciliations")
    @require("reconciliere.creare")
    def new_reconciliation(uid):
        client_id = int(request.form["client_id"])
        period = request.form["period"]
        mapping = None
        if request.form.get("anaf_mapping"):
            mapping = json.loads(request.form["anaf_mapping"])
        try:
            comp_rows = parse_company_journal(
                _save_upload(request.files["company_file"]))
            anaf_rows = FileAnafDataSource(
                _save_upload(request.files["anaf_file"]),
                mapping).get_etva_data("", period)
        except ImportError_ as e:
            return jsonify({"errors": e.errors}), 400
        rid = _persist(uid, client_id, period, comp_rows, anaf_rows)
        audit.log(conn, uid, "reconciliere.creare", "reconciliation", str(rid))
        return jsonify(_result_payload(rid, comp_rows, anaf_rows))

    def _load_rows(rid, table):
        rows = conn.execute(
            f"SELECT partner_cui, invoice_no, date, base, vat, category "
            f"FROM {table} WHERE reconciliation_id=?", (rid,))
        return [dict(r) for r in rows]

    @app.get("/api/reconciliations/<int:rid>")
    @require()
    def get_reconciliation(uid, rid):
        comp = _load_rows(rid, "invoices_company")
        anaf = _load_rows(rid, "invoices_anaf")
        return jsonify(_result_payload(rid, comp, anaf))

    @app.get("/api/reconciliations/<int:rid>/export")
    @require("rapoarte.export")
    def export_report(uid, rid):
        row = conn.execute(
            "SELECT r.period, c.name FROM reconciliations r "
            "JOIN clients c ON c.id = r.client_id WHERE r.id=?",
            (rid,)).fetchone()
        if row is None:
            return jsonify({"error": "Reconciliere inexistenta"}), 404
        comp = _load_rows(rid, "invoices_company")
        anaf = _load_rows(rid, "invoices_anaf")
        result = reconcile(comp, anaf)
        path = os.path.join(upload_dir, f"raport_{rid}.xlsx")
        export_mod.write_report(result, suggest_d300(result), path,
                                row["name"], row["period"])
        audit.log(conn, uid, "raport.export", "reconciliation", str(rid))
        return send_file(path, as_attachment=True,
                         download_name=f"raport_{rid}.xlsx")

    @app.get("/api/audit")
    @require("audit.vizualizare")
    def audit_view(uid):
        return jsonify(audit.entries(conn))

    @app.get("/api/admin/users")
    @require("useri.gestionare")
    def list_users(uid):
        rows = conn.execute(
            "SELECT u.id, u.username, u.active, "
            "GROUP_CONCAT(r.name) AS roles FROM users u "
            "LEFT JOIN user_roles ur ON ur.user_id=u.id "
            "LEFT JOIN roles r ON r.id=ur.role_id GROUP BY u.id")
        return jsonify([dict(r) for r in rows])

    @app.post("/api/admin/users")
    @require("useri.gestionare")
    def add_user(uid):
        data = request.get_json(force=True)
        try:
            new_id = auth.create_user(conn, data["username"], data["password"])
        except auth.AuthError as e:
            return jsonify({"error": str(e)}), 400
        if data.get("role"):
            pm.assign_role(conn, new_id, data["role"])
        audit.log(conn, uid, "user.creare", "user", str(new_id))
        return jsonify({"id": new_id})

    @app.post("/api/admin/users/<int:target>/deactivate")
    @require("useri.gestionare")
    def deactivate_user(uid, target):
        auth.set_active(conn, target, False)
        audit.log(conn, uid, "user.dezactivare", "user", str(target))
        return jsonify({"ok": True})

    @app.post("/api/admin/roles")
    @require("useri.gestionare")
    def add_role(uid):
        data = request.get_json(force=True)
        try:
            rid = pm.create_role(conn, data["name"], data["permissions"])
        except pm.PermError as e:
            return jsonify({"error": str(e)}), 400
        audit.log(conn, uid, "rol.creare", "role", str(rid))
        return jsonify({"id": rid})

    @app.put("/api/admin/roles/<name>")
    @require("useri.gestionare")
    def edit_role(uid, name):
        data = request.get_json(force=True)
        try:
            pm.update_role(conn, name, data["permissions"])
        except pm.PermError as e:
            return jsonify({"error": str(e)}), 400
        audit.log(conn, uid, "rol.editare", "role", name)
        return jsonify({"ok": True})

    @app.post("/api/admin/assign")
    @require("useri.gestionare")
    def assign_client(uid):
        data = request.get_json(force=True)
        clients.assign(conn, data["user_id"], data["client_id"])
        audit.log(conn, uid, "client.alocare", "client",
                  str(data["client_id"]))
        return jsonify({"ok": True})

    return app
```

- [ ] **Step 3: Run tests** — `python -m pytest tests/test_server.py -v` — Expected: 6 PASS. Then run the FULL suite: `python -m pytest tests -v` — Expected: all PASS.

- [ ] **Step 4: Commit** — `git add etva/server.py tests/test_server.py && git commit -m "feat: Flask API with permission guards, persistence and export"`

---

### Task 13: Frontend (single-page UI)

**Files:**
- Create: `web/index.html`
- Modify: `etva/server.py` — add static route serving `web/` at `/`.

**Interfaces:**
- Consumes: every `/api/*` route from Task 12, exactly as specified there.
- Produces: a functional Romanian-language UI. No build step, no frameworks — plain HTML/CSS/JS with `fetch`. Views (shown/hidden by JS): Login → Dashboard (client list + new reconciliation form with file inputs + optional ANAF mapping JSON textarea) → Results (two tables: Sumar per category with red rows for `de_verificat`; Diferente per invoice) → Admin (users/roles/assignments, visible only with `useri.gestionare`) → Audit (visible only with `audit.vizualizare`). Export button downloads the xlsx.

- [ ] **Step 1: Add static serving to `server.py`**

In `create_app`, change the Flask constructor and add an index route:

```python
    import pathlib
    web_dir = str(pathlib.Path(__file__).resolve().parents[1] / "web")
    app = Flask(__name__, static_folder=web_dir, static_url_path="/static")
    app.secret_key = secrets.token_hex(32)

    @app.get("/")
    def index():
        return app.send_static_file("index.html")
```

- [ ] **Step 2: Write `web/index.html`**

One file, three sections: `<style>` (simple clean layout, red row class `.rosu { background:#ffc7ce }`), `<body>` with five `<div class="view">` blocks, `<script>` with:

```html
<!DOCTYPE html>
<html lang="ro">
<head>
<meta charset="utf-8">
<title>e-TVA Reconciliere</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; background: #f5f6fa; }
  header { background: #1e3a5f; color: #fff; padding: 12px 20px;
           display: flex; justify-content: space-between; align-items: center; }
  main { padding: 20px; max-width: 1100px; margin: auto; }
  .view { display: none; } .view.activ { display: block; }
  table { border-collapse: collapse; width: 100%; background: #fff;
          margin: 12px 0; }
  th, td { border: 1px solid #d0d4dc; padding: 6px 10px; text-align: left; }
  th { background: #e8ebf2; }
  tr.rosu td { background: #ffc7ce; }
  button { background: #1e3a5f; color: #fff; border: 0; padding: 8px 14px;
           border-radius: 4px; cursor: pointer; }
  input, select, textarea { padding: 6px; margin: 4px 0; width: 100%;
                            box-sizing: border-box; }
  .card { background: #fff; border: 1px solid #d0d4dc; border-radius: 6px;
          padding: 16px; margin: 12px 0; }
  .eroare { color: #b00020; white-space: pre-wrap; }
  nav button { margin-right: 8px; background: #35507a; }
</style>
</head>
<body>
<header>
  <strong>e-TVA Reconciliere</strong>
  <nav id="meniu" style="display:none">
    <button onclick="arata('dashboard')">Clienti</button>
    <button id="btnAdmin" onclick="arata('admin')" style="display:none">Administrare</button>
    <button id="btnAudit" onclick="arata('audit'); incarcaAudit()" style="display:none">Audit</button>
    <button onclick="logout()">Iesire</button>
  </nav>
</header>
<main>

<div id="login" class="view activ">
  <div class="card" style="max-width:360px;margin:60px auto">
    <h2>Autentificare</h2>
    <input id="loginUser" placeholder="Utilizator">
    <input id="loginPass" type="password" placeholder="Parola">
    <button onclick="login()">Intra</button>
    <p id="loginErr" class="eroare"></p>
  </div>
</div>

<div id="dashboard" class="view">
  <h2>Clienti</h2>
  <div id="listaClienti"></div>
  <div class="card" id="cardClientNou" style="display:none">
    <h3>Client nou</h3>
    <input id="cuiNou" placeholder="CUI">
    <input id="numeNou" placeholder="Denumire">
    <button onclick="adaugaClient()">Adauga</button>
  </div>
  <div class="card">
    <h3>Reconciliere noua</h3>
    <select id="selClient"></select>
    <input id="perioada" placeholder="Perioada (ex: 2026-01)">
    <label>Jurnal firma (xlsx/csv)</label>
    <input id="fisierFirma" type="file">
    <label>Fisier ANAF e-TVA (xlsx/csv)</label>
    <input id="fisierAnaf" type="file">
    <label>Mapare coloane ANAF (JSON, optional)</label>
    <textarea id="mapareAnaf" rows="3"
      placeholder='{"cui_partener":"CIF","nr_factura":"Numar",...}'></textarea>
    <button onclick="ruleazaReconciliere()">Ruleaza reconcilierea</button>
    <p id="reconErr" class="eroare"></p>
  </div>
</div>

<div id="rezultate" class="view">
  <h2>Rezultate reconciliere</h2>
  <button id="btnExport" onclick="exporta()">Export raport Excel</button>
  <h3>Sumar pe categorii</h3>
  <table id="tabelSumar"></table>
  <h3>Diferente pe facturi</h3>
  <table id="tabelDiferente"></table>
</div>

<div id="admin" class="view">
  <h2>Administrare</h2>
  <div class="card">
    <h3>Utilizator nou</h3>
    <input id="adminUser" placeholder="Utilizator">
    <input id="adminPass" type="password" placeholder="Parola">
    <select id="adminRol"></select>
    <button onclick="adaugaUser()">Creeaza</button>
  </div>
  <div class="card"><h3>Utilizatori</h3><div id="listaUseri"></div></div>
  <div class="card">
    <h3>Alocare client la utilizator</h3>
    <select id="alocUser"></select>
    <select id="alocClient"></select>
    <button onclick="aloca()">Aloca</button>
  </div>
</div>

<div id="audit" class="view">
  <h2>Jurnal de audit</h2>
  <table id="tabelAudit"></table>
</div>

</main>
<script>
let permisiuni = [], reconCurent = null;

function arata(id) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('activ'));
  document.getElementById(id).classList.add('activ');
}

async function api(url, opts = {}) {
  const r = await fetch(url, opts);
  if (r.status === 401) { arata('login'); throw new Error('neautentificat'); }
  return r;
}

async function login() {
  const r = await fetch('/api/login', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username: loginUser.value, password: loginPass.value})});
  if (!r.ok) { loginErr.textContent = (await r.json()).error; return; }
  permisiuni = (await r.json()).permissions;
  meniu.style.display = '';
  btnAdmin.style.display = permisiuni.includes('useri.gestionare') ? '' : 'none';
  btnAudit.style.display = permisiuni.includes('audit.vizualizare') ? '' : 'none';
  cardClientNou.style.display = permisiuni.includes('clienti.creare') ? '' : 'none';
  btnExport.style.display = permisiuni.includes('rapoarte.export') ? '' : 'none';
  await incarcaClienti();
  if (permisiuni.includes('useri.gestionare')) await incarcaAdmin();
  arata('dashboard');
}

async function logout() {
  await fetch('/api/logout', {method: 'POST'});
  meniu.style.display = 'none';
  arata('login');
}

async function incarcaClienti() {
  const cl = await (await api('/api/clients')).json();
  listaClienti.innerHTML = '<table><tr><th>CUI</th><th>Denumire</th></tr>' +
    cl.map(c => `<tr><td>${c.cui}</td><td>${c.name}</td></tr>`).join('') +
    '</table>';
  selClient.innerHTML = alocClient.innerHTML =
    cl.map(c => `<option value="${c.id}">${c.name} (${c.cui})</option>`).join('');
}

async function adaugaClient() {
  const r = await api('/api/clients', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({cui: cuiNou.value, name: numeNou.value})});
  if (r.ok) await incarcaClienti();
}

async function ruleazaReconciliere() {
  reconErr.textContent = '';
  const fd = new FormData();
  fd.append('client_id', selClient.value);
  fd.append('period', perioada.value);
  fd.append('company_file', fisierFirma.files[0]);
  fd.append('anaf_file', fisierAnaf.files[0]);
  if (mapareAnaf.value.trim()) fd.append('anaf_mapping', mapareAnaf.value);
  const r = await api('/api/reconciliations', {method: 'POST', body: fd});
  const body = await r.json();
  if (!r.ok) {
    reconErr.textContent = (body.errors || [body.error]).join('\n');
    return;
  }
  reconCurent = body.id;
  afiseazaRezultate(body);
  arata('rezultate');
}

function afiseazaRezultate(b) {
  tabelSumar.innerHTML =
    '<tr><th>Categorie</th><th>Baza firma</th><th>TVA firma</th>' +
    '<th>Baza ANAF</th><th>TVA ANAF</th><th>Baza sugerata</th>' +
    '<th>TVA sugerata</th><th>Status</th></tr>' +
    b.suggestions.map(s =>
      `<tr class="${s.status === 'de_verificat' ? 'rosu' : ''}">` +
      `<td>${s.category}</td><td>${s.company_base}</td><td>${s.company_vat}</td>` +
      `<td>${s.anaf_base}</td><td>${s.anaf_vat}</td><td>${s.suggested_base}</td>` +
      `<td>${s.suggested_vat}</td><td>${s.status}</td></tr>`).join('');
  tabelDiferente.innerHTML =
    '<tr><th>Tip</th><th>CUI</th><th>Factura</th><th>Categorie</th>' +
    '<th>Delta baza</th><th>Delta TVA</th></tr>' +
    b.differences.map(d =>
      `<tr class="rosu"><td>${d.diff_type}</td><td>${d.partner_cui}</td>` +
      `<td>${d.invoice_no}</td><td>${d.category}</td>` +
      `<td>${d.delta_base}</td><td>${d.delta_vat}</td></tr>`).join('') ||
    '<tr><td colspan="6">Nicio diferenta — totul corespunde.</td></tr>';
}

function exporta() {
  window.location = `/api/reconciliations/${reconCurent}/export`;
}

async function incarcaAdmin() {
  const useri = await (await api('/api/admin/users')).json();
  listaUseri.innerHTML =
    '<table><tr><th>Utilizator</th><th>Roluri</th><th>Activ</th><th></th></tr>' +
    useri.map(u => `<tr><td>${u.username}</td><td>${u.roles || ''}</td>` +
      `<td>${u.active ? 'Da' : 'Nu'}</td>` +
      `<td><button onclick="dezactiveaza(${u.id})">Dezactiveaza</button></td></tr>`
    ).join('') + '</table>';
  alocUser.innerHTML = useri.map(u =>
    `<option value="${u.id}">${u.username}</option>`).join('');
  adminRol.innerHTML = ['Admin', 'Manager', 'Contabil', 'Junior'].map(r =>
    `<option>${r}</option>`).join('');
}

async function adaugaUser() {
  await api('/api/admin/users', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username: adminUser.value, password: adminPass.value,
                          role: adminRol.value})});
  await incarcaAdmin();
}

async function dezactiveaza(id) {
  await api(`/api/admin/users/${id}/deactivate`, {method: 'POST'});
  await incarcaAdmin();
}

async function aloca() {
  await api('/api/admin/assign', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({user_id: +alocUser.value,
                          client_id: +alocClient.value})});
}

async function incarcaAudit() {
  const rows = await (await api('/api/audit')).json();
  tabelAudit.innerHTML =
    '<tr><th>Data</th><th>User</th><th>Actiune</th><th>Entitate</th></tr>' +
    rows.map(r => `<tr><td>${r.ts}</td><td>${r.user_id}</td>` +
      `<td>${r.action}</td><td>${r.entity || ''} ${r.entity_id || ''}</td></tr>`
    ).join('');
}
</script>
</body>
</html>
```

- [ ] **Step 3: Verify with test client**

Add to `tests/test_server.py`:
```python
def test_index_served(app):
    c = app.test_client()
    r = c.get("/")
    assert r.status_code == 200
    assert b"e-TVA Reconciliere" in r.data
```

Run: `python -m pytest tests/test_server.py -v` — Expected: all PASS.

- [ ] **Step 4: Commit** — `git add web etva/server.py tests/test_server.py && git commit -m "feat: single-page Romanian UI"`

---

### Task 14: pywebview entry point + first-run setup wizard

**Files:**
- Create: `etva/main.py`
- Modify: `etva/server.py` — add setup routes.
- Test: `tests/test_server.py` (setup routes only; the pywebview window itself is verified manually).

**Interfaces:**
- Consumes: `crypto`, `db`, `auth`, `permissions`, `server.create_app`.
- Produces:
  - `server.create_setup_app(app_dir: str, on_ready) -> Flask` — routes served when no keystore exists:
    - `GET /api/setup/status` → `{"initialized": bool}` (keystore file exists?)
    - `POST /api/setup` `{master_password, admin_username, admin_password}` → creates keystore + DB + schema + admin user with Admin role; returns `{"recovery_phrase": "..."}` — shown ONCE in the UI with a mandatory "Am salvat fraza" confirmation.
    - `POST /api/setup/unlock` `{master_password}` → opens DB, calls `on_ready(conn)`, returns `{"ok": true}`; 401 with Romanian message on wrong password.
    - `POST /api/setup/recover` `{recovery_phrase, new_master_password}` → re-wraps key, opens DB, calls `on_ready(conn)`.
  - `main.py` — runs Flask on `127.0.0.1:<random free port>`, opens pywebview window `"e-TVA Reconciliere"` pointed at it. App data dir: `%APPDATA%/eTVA-Reconciliere/` (keystore.json, app.db, uploads/).

- [ ] **Step 1: Write failing tests for setup routes**

Add to `tests/test_server.py`:
```python
from etva.server import create_setup_app

def test_setup_flow(tmp_path):
    holder = {}
    app2 = create_setup_app(str(tmp_path), lambda conn: holder.update(conn=conn))
    app2.config["TESTING"] = True
    c = app2.test_client()
    assert c.get("/api/setup/status").get_json() == {"initialized": False}
    r = c.post("/api/setup", json={"master_password": "Master123!",
                                   "admin_username": "admin",
                                   "admin_password": "Admin123!"})
    phrase = r.get_json()["recovery_phrase"]
    assert len(phrase.split()) == 24
    assert c.get("/api/setup/status").get_json() == {"initialized": True}
    r = c.post("/api/setup/unlock", json={"master_password": "gresit"})
    assert r.status_code == 401
    r = c.post("/api/setup/unlock", json={"master_password": "Master123!"})
    assert r.status_code == 200 and "conn" in holder

def test_setup_recover(tmp_path):
    holder = {}
    app2 = create_setup_app(str(tmp_path), lambda conn: holder.update(conn=conn))
    app2.config["TESTING"] = True
    c = app2.test_client()
    phrase = c.post("/api/setup", json={
        "master_password": "Master123!", "admin_username": "admin",
        "admin_password": "x"}).get_json()["recovery_phrase"]
    r = c.post("/api/setup/recover", json={
        "recovery_phrase": phrase, "new_master_password": "Nou123!"})
    assert r.status_code == 200 and "conn" in holder
```

Run: `python -m pytest tests/test_server.py -v` — Expected: new tests FAIL.

- [ ] **Step 2: Add `create_setup_app` to `etva/server.py`**

```python
def create_setup_app(app_dir: str, on_ready) -> Flask:
    """Pre-unlock app: setup wizard, unlock, recovery."""
    from etva import crypto, db as db_mod
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(32)
    ks_path = os.path.join(app_dir, "keystore.json")
    db_path = os.path.join(app_dir, "app.db")

    def _open_and_ready(key):
        conn = db_mod.open_db(db_path, key)
        db_mod.init_schema(conn)
        on_ready(conn)

    @app.get("/api/setup/status")
    def status():
        return jsonify({"initialized": os.path.exists(ks_path)})

    @app.post("/api/setup")
    def setup():
        data = request.get_json(force=True)
        if os.path.exists(ks_path):
            return jsonify({"error": "Aplicatia este deja initializata."}), 400
        phrase = crypto.create_keystore(data["master_password"], ks_path)
        key = crypto.unlock_keystore(data["master_password"], ks_path)
        conn = db_mod.open_db(db_path, key)
        db_mod.init_schema(conn)
        uid = auth.create_user(conn, data["admin_username"],
                               data["admin_password"])
        pm.assign_role(conn, uid, "Admin")
        audit.log(conn, uid, "setup.initializare")
        on_ready(conn)
        return jsonify({"recovery_phrase": phrase})

    @app.post("/api/setup/unlock")
    def unlock():
        data = request.get_json(force=True)
        from etva.crypto import KeystoreError
        try:
            key = crypto.unlock_keystore(data["master_password"], ks_path)
        except KeystoreError as e:
            return jsonify({"error": str(e)}), 401
        _open_and_ready(key)
        return jsonify({"ok": True})

    @app.post("/api/setup/recover")
    def recover():
        data = request.get_json(force=True)
        from etva.crypto import KeystoreError
        try:
            key = crypto.recover_keystore(data["recovery_phrase"], ks_path,
                                          data["new_master_password"])
        except KeystoreError as e:
            return jsonify({"error": str(e)}), 401
        _open_and_ready(key)
        return jsonify({"ok": True})

    return app
```

- [ ] **Step 3: Run tests** — `python -m pytest tests/test_server.py -v` — Expected: all PASS. Full suite: `python -m pytest tests -v` — Expected: all PASS.

- [ ] **Step 4: Write `etva/main.py`**

```python
"""Desktop entry point: local Flask in a background thread + pywebview window.

Flow: create_setup_app serves the unlock/setup wizard; once the DB is open,
the main app's routes are registered on the same server via a swap.
"""
import os, socket, threading
import webview
from werkzeug.serving import make_server
from etva.server import create_app, create_setup_app


def _app_dir() -> str:
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    d = os.path.join(base, "eTVA-Reconciliere")
    os.makedirs(os.path.join(d, "uploads"), exist_ok=True)
    return d


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class AppHolder:
    """Dispatches WSGI calls to setup app until unlocked, then to main app."""
    def __init__(self, app_dir):
        self.main_app = None
        self.setup_app = create_setup_app(app_dir, self._on_ready)
        self.app_dir = app_dir

    def _on_ready(self, conn):
        self.main_app = create_app(conn, os.path.join(self.app_dir, "uploads"))

    def __call__(self, environ, start_response):
        if self.main_app is not None and not environ["PATH_INFO"].startswith("/api/setup"):
            return self.main_app(environ, start_response)
        return self.setup_app(environ, start_response)


def main():
    app_dir = _app_dir()
    holder = AppHolder(app_dir)
    port = _free_port()
    server = make_server("127.0.0.1", port, holder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    webview.create_window("e-TVA Reconciliere",
                          f"http://127.0.0.1:{port}/",
                          width=1200, height=800)
    webview.start()
    server.shutdown()


if __name__ == "__main__":
    main()
```

Note: `create_setup_app`'s Flask app has no `/` route; the setup wizard UI must be reachable. Add to `create_setup_app` the same static serving block used in `create_app` (index + static folder), and extend `web/index.html` with a setup/unlock view: on page load, JS calls `/api/setup/status`; if `initialized:false` show a setup form (master password + admin credentials → POST `/api/setup`, then display the recovery phrase full-screen with a confirmation checkbox "Am salvat fraza de recuperare intr-un loc sigur" and a continue button); if `initialized:true` show an unlock form (master password → POST `/api/setup/unlock`, on success show the normal login view; a "Am uitat parola" link reveals the recovery form → POST `/api/setup/recover`). After unlock succeeds, proceed to the existing login flow unchanged.

- [ ] **Step 5: Manual smoke test**

Run: `python -m etva.main`
Expected: native window opens → setup wizard (first run) → recovery phrase shown once → login as admin → create client → upload two CSVs → results with red-highlighted rows → export downloads xlsx. Close and rerun: unlock screen appears, wrong password shows a Romanian error message.

- [ ] **Step 6: Commit** — `git add etva/main.py etva/server.py web/index.html tests/test_server.py && git commit -m "feat: desktop entry with setup wizard, unlock and recovery"`

**Packaging note (out of scope for this plan):** final distribution uses PyInstaller: `pyinstaller --onefile --windowed --add-data "web;web" etva/main.py`. sqlcipher3/pywebview hooks may need `--hidden-import` flags; handled in a future task once the app is stable.

---

## Verification checklist (end of plan)

- `python -m pytest tests -v` — full suite green.
- Manual smoke test from Task 14 Step 5 passes.
- `git log --oneline` shows one commit per task minimum.
