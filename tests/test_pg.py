import psycopg
import pytest

from etva import pg


def test_dsn_from_env_reads_variable(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")
    assert pg.dsn_from_env() == "postgresql://user:pass@host:5432/db"


def test_dsn_from_env_raises_if_missing(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(pg.PgError, match="DATABASE_URL"):
        pg.dsn_from_env()


def test_dsn_from_env_supports_custom_variable_name(monkeypatch):
    monkeypatch.setenv("DATABASE_URL_TESTARE", "postgresql://x/y")
    assert pg.dsn_from_env("DATABASE_URL_TESTARE") == "postgresql://x/y"


def test_connect_wraps_operational_error(monkeypatch):
    def _fake_connect(dsn, row_factory=None):
        raise psycopg.OperationalError("connection refused")
    monkeypatch.setattr(psycopg, "connect", _fake_connect)
    with pytest.raises(pg.PgError, match="connection refused"):
        pg.connect("postgresql://bad-dsn")


def test_connect_returns_connection_on_success(monkeypatch):
    sentinel = object()

    def _fake_connect(dsn, row_factory=None):
        assert dsn == "postgresql://ok"
        return sentinel
    monkeypatch.setattr(psycopg, "connect", _fake_connect)
    assert pg.connect("postgresql://ok") is sentinel


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, query):
        pass

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)


def _full_schema_rows():
    rows = []
    for tabel, coloane in pg.EXPECTED_SCHEMA.items():
        for coloana, tip in coloane.items():
            rows.append({"table_name": tabel, "column_name": coloana, "data_type": tip})
    return rows


def test_verify_schema_returns_empty_when_schema_matches():
    conn = _FakeConn(_full_schema_rows())
    assert pg.verify_schema(conn) == []


def test_verify_schema_detects_missing_table():
    rows = [r for r in _full_schema_rows() if r["table_name"] != "contracts"]
    conn = _FakeConn(rows)
    probleme = pg.verify_schema(conn)
    assert any("Lipseste tabelul 'contracts'" in p for p in probleme)


def test_verify_schema_detects_missing_column():
    rows = [r for r in _full_schema_rows()
           if not (r["table_name"] == "firms" and r["column_name"] == "arhivata_la")]
    conn = _FakeConn(rows)
    probleme = pg.verify_schema(conn)
    assert any("Lipseste coloana 'firms.arhivata_la'" in p for p in probleme)


def test_verify_schema_detects_wrong_type():
    rows = _full_schema_rows()
    for r in rows:
        if r["table_name"] == "invoices" and r["column_name"] == "valoare_neta":
            r["data_type"] = "text"
    conn = _FakeConn(rows)
    probleme = pg.verify_schema(conn)
    assert any(
        "Tip diferit la 'invoices.valoare_neta'" in p and "asteptat 'numeric'" in p
        and "gasit 'text'" in p
        for p in probleme)
