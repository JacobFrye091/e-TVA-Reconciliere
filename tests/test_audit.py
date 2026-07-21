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
