import os, pytest
from etva import db, clients

ADMIN = {"username": "sef", "permissions": list(db.PERMISSIONS)}
JUNIOR = {"username": "junior1",
          "permissions": db.DEFAULT_ROLES["Junior"]}

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
    c1 = clients.create_client(conn, "RO1", "A")
    clients.create_client(conn, "RO2", "B")
    clients.assign(conn, "junior1", c1)
    vis = clients.visible_clients(conn, JUNIOR)
    assert [c["cui"] for c in vis] == ["RO1"]

def test_visibility_admin_sees_all(conn):
    clients.create_client(conn, "RO1", "A")
    clients.create_client(conn, "RO2", "B")
    assert len(clients.visible_clients(conn, ADMIN)) == 2

def test_delete(conn):
    cid = clients.create_client(conn, "RO1", "A")
    clients.assign(conn, "junior1", cid)
    clients.delete_client(conn, cid)
    assert clients.visible_clients(conn, ADMIN) == []
    assert clients.visible_clients(conn, JUNIOR) == []
