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
