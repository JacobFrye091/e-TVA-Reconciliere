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
