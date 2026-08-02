"""Adaptor dual-backend: semantica sqlite3 peste psycopg/PostgreSQL.

Tot codul aplicatiei a fost scris pe sqlite3 (portal.db) + sqlcipher
(fisierele per-firma). Migrarea pe Postgres (planning/migrare-postgres.md)
nu rescrie sutele de apeluri conn.execute(...) - in schimb, acest modul
imbraca o conexiune psycopg intr-un obiect cu exact contractul pe care il
asteapta codul existent:

- placeholder-ele ``?`` sunt traduse in ``%s`` (sarind literalele '...');
- randurile sunt dict-uri cu valorile normalizate la ce ar fi intors
  SQLite: datetime/date -> string ISO, bool -> 0/1, Decimal -> float,
  memoryview -> bytes;
- sesiunea ruleaza cu TIME ZONE 'UTC', ca redarea ISO a coloanelor
  timestamptz sa fie stabila indiferent de fusul serverului;
- ``insert_id()`` inlocuieste ``cur.lastrowid`` (pe Postgres nu exista -
  se obtine cu ``RETURNING id``); accesarea .lastrowid pe ramura PG
  esueaza zgomotos, ca un sit nemigrat sa nu intoarca tacut None;
- izolarea per-firma se face prin ``firm_scope()``: aceeasi conexiune
  fizica, dar cu variabila de sesiune ``app.firm_id`` setata inainte de
  fiecare comanda - politicile RLS din etva/pg_schema.sql fac restul.

Selectia backend-ului e o variabila de mediu (ETVA_DB=postgres +
DATABASE_URL), niciodata o schimbare de cod - SQLite ramane implicitul
pentru dev local si suita de teste.
"""
import functools
import os
from datetime import date, datetime
from decimal import Decimal


def backend() -> str:
    """'postgres' sau 'sqlite' - decizia globala de backend a procesului."""
    return "postgres" if os.environ.get("ETVA_DB") == "postgres" else "sqlite"


@functools.lru_cache(maxsize=4096)
def _tradu(sql: str) -> str:
    """Traduce placeholder-ele sqlite3 (?) in stil psycopg (%s), lasand
    neatins continutul literalelor intre apostrofuri ('' e escape-ul SQL
    standard si ramane in interiorul literalului). Orice '%' existent
    (ex. LIKE '%text%') se escapeaza in '%%', altfel psycopg l-ar citi ca
    inceput de placeholder. Se apeleaza DOAR cand exista parametri - fara
    parametri, SQL-ul pleaca spre psycopg neatins (vezi ConnCompat)."""
    bucati = []
    in_literal = False
    for c in sql:
        if c == "'":
            in_literal = not in_literal
            bucati.append(c)
        elif c == "%":
            bucati.append("%%")
        elif c == "?" and not in_literal:
            bucati.append("%s")
        else:
            bucati.append(c)
    return "".join(bucati)


def _normalizeaza(valoare):
    """Valorile psycopg -> exact ce ar fi intors sqlite3.Row pentru datele
    scrise de aceasta aplicatie (care stocheaza datetime ca .isoformat(),
    boolean ca 0/1 si numeric ca float)."""
    if isinstance(valoare, bool):
        return 1 if valoare else 0
    if isinstance(valoare, (datetime, date)):
        return valoare.isoformat()
    if isinstance(valoare, Decimal):
        return float(valoare)
    if isinstance(valoare, memoryview):
        return bytes(valoare)
    return valoare


def _rand(d: dict) -> dict:
    return {k: _normalizeaza(v) for k, v in d.items()}


class CursorCompat:
    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        r = self._cur.fetchone()
        return None if r is None else _rand(r)

    def fetchall(self):
        return [_rand(r) for r in self._cur.fetchall()]

    def __iter__(self):
        for r in self._cur:
            yield _rand(r)

    @property
    def lastrowid(self):
        raise NotImplementedError(
            "lastrowid nu exista pe Postgres - foloseste "
            "etva.dbcompat.insert_id(conn, sql, params) (RETURNING id).")


class ConnCompat:
    """Conexiunea portalului: aceeasi interfata ca sqlite3.Connection
    pentru subsetul folosit de aplicatie."""

    def __init__(self, pgconn):
        self.raw = pgconn          # pentru etva.pg.verify_schema etc.

    def execute(self, sql, params=()):
        if params:
            return CursorCompat(self.raw.execute(_tradu(sql), params))
        # Fara parametri psycopg nu proceseaza placeholdere deloc (params
        # None), deci literalele '%' raman valabile fara escapare.
        return CursorCompat(self.raw.execute(sql))

    def executemany(self, sql, seq_params):
        cur = self.raw.cursor()
        cur.executemany(_tradu(sql), list(seq_params))
        return CursorCompat(cur)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()

    def _seteaza_firma(self, firm_id: int) -> None:
        # Emis inaintea FIECAREI comenzi, fara cache: un SET (chiar
        # session-level) executat intr-o tranzactie care ajunge la rollback
        # e anulat de acel rollback - iar teardown_request face rollback
        # dupa fiecare cerere de citire. Un cache Python ar ramane atunci
        # desincronizat de valoarea reala din sesiune si urmatoarele cereri
        # ar rula cu scope gol ('' -> ''::int crapa in politica RLS) sau,
        # mai rau, cu firma anterioara - izolarea intre firme ar fi rupta.
        # Costul e un set_config local per comanda, neglijabil.
        self.raw.execute(
            "SELECT set_config('app.firm_id', %s, false)", (str(firm_id),))


class FirmScopedConnection:
    """Vederea unei singure firme peste aceeasi conexiune fizica: fiecare
    comanda se executa cu app.firm_id setat la firma acestui obiect, iar
    politicile RLS (izolare_firma) filtreaza/valideaza randurile. Sigur
    pentru ca `portal_conn` e fie conexiunea dedicata unui singur fir de
    executie (teste, scheduler-ele de fundal), fie proxy-ul per-cerere din
    portal/app.py, care rezolva la conexiunea din pool alocata exclusiv
    cererii curente - doua firme nu executa niciodata comenzi pe aceeasi
    conexiune fizica in acelasi timp."""

    def __init__(self, portal_conn: ConnCompat, firm_id: int):
        self._portal = portal_conn
        self._firm_id = firm_id

    def execute(self, sql, params=()):
        self._portal._seteaza_firma(self._firm_id)
        return self._portal.execute(sql, params)

    def executemany(self, sql, seq_params):
        self._portal._seteaza_firma(self._firm_id)
        return self._portal.executemany(sql, seq_params)

    def commit(self):
        self._portal.commit()

    def rollback(self):
        self._portal.rollback()

    def close(self):
        # Conexiunea fizica e a portalului si ramane deschisa - "inchiderea"
        # unei vederi per-firma nu inseamna nimic pe acest backend.
        pass


def connect(dsn: str) -> ConnCompat:
    import psycopg
    from psycopg.rows import dict_row
    conn = psycopg.connect(dsn, row_factory=dict_row)
    conn.execute("SET TIME ZONE 'UTC'")
    conn.commit()
    return ConnCompat(conn)


def firm_scope(portal_conn: ConnCompat, firm_id: int) -> FirmScopedConnection:
    return FirmScopedConnection(portal_conn, firm_id)


def make_pool(dsn: str, min_size: int = 2, max_size: int = 10):
    """Pool de conexiuni Postgres pentru gestionarea cererilor concurente -
    fiecare cerere primeste o conexiune fizica proprie in loc sa o
    partajeze cu tot procesul (vezi proxy-ul per-cerere din
    portal/app.py). `configure` reproduce initializarea din connect() de
    mai sus (fus orar UTC) pentru fiecare conexiune noua creata de pool."""
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    def _configure(conn):
        conn.execute("SET TIME ZONE 'UTC'")
        conn.commit()

    return ConnectionPool(
        dsn, min_size=min_size, max_size=max_size, open=True,
        kwargs={"row_factory": dict_row}, configure=_configure)


def insert_id(conn, sql: str, params=()) -> int:
    """INSERT care intoarce id-ul randului nou - inlocuitorul explicit al
    tiparului `cur = conn.execute(...); cur.lastrowid`, care nu exista pe
    Postgres. Pe sqlite3/sqlcipher pastreaza exact comportamentul vechi."""
    if isinstance(conn, (ConnCompat, FirmScopedConnection)):
        return conn.execute(sql + " RETURNING id", params).fetchone()["id"]
    return conn.execute(sql, params).lastrowid
