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
