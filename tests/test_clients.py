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
