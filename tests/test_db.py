import os, pytest
from etva import db

KEY = os.urandom(32)

def test_open_and_schema(tmp_path):
    path = str(tmp_path / "app.db")
    conn = db.open_db(path, KEY)
    db.init_schema(conn)
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"clients", "client_assignments", "reconciliations",
            "invoices_company", "invoices_anaf", "differences",
            "audit_log"} <= tables
    assert "users" not in tables  # identity lives in the portal now
    db.init_schema(conn)  # idempotent
    conn.close()

def test_wrong_key_raises(tmp_path):
    path = str(tmp_path / "app.db")
    conn = db.open_db(path, KEY)
    db.init_schema(conn)
    conn.close()
    with pytest.raises(db.DbError):
        db.open_db(path, os.urandom(32))

def test_permission_catalog_stable():
    assert len(db.PERMISSIONS) == 9
    assert "rapoarte.export" in db.DEFAULT_ROLES["Contabil"]
    assert "rapoarte.export" not in db.DEFAULT_ROLES["Junior"]
