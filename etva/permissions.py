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
