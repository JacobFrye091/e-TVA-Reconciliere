"""SQLCipher-encrypted SQLite access + schema.

Identity (users, roles) now lives in the account portal; this schema keeps
only firm-local data. `client_assignments` and `audit_log` reference portal
usernames as plain strings.
"""


class DbError(Exception):
    pass


PERMISSIONS = {
    "clienti.creare": "Creare clienti",
    "clienti.editare": "Editare clienti",
    "clienti.stergere": "Stergere clienti",
    "reconciliere.creare": "Creare reconcilieri",
    "reconciliere.editare": "Editare reconcilieri",
    "reconciliere.stergere": "Stergere reconcilieri",
    "rapoarte.export": "Export rapoarte",
    "useri.gestionare": "Gestionare utilizatori si alocari",
    "audit.vizualizare": "Vizualizare jurnal de audit",
}

_ALL = list(PERMISSIONS)
DEFAULT_ROLES = {
    "Admin": _ALL,
    "Manager": [p for p in _ALL if p not in
                ("useri.gestionare", "clienti.stergere")],
    "Contabil": ["reconciliere.creare", "reconciliere.editare",
                 "rapoarte.export"],
    "Junior": ["reconciliere.creare", "reconciliere.editare"],
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients(
  id INTEGER PRIMARY KEY, cui TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  gdpr_confirmat INTEGER NOT NULL DEFAULT 0,
  gdpr_confirmat_de TEXT, gdpr_confirmat_la TEXT);
CREATE TABLE IF NOT EXISTS client_assignments(
  username TEXT NOT NULL, client_id INTEGER NOT NULL,
  PRIMARY KEY(username, client_id));
CREATE TABLE IF NOT EXISTS reconciliations(
  id INTEGER PRIMARY KEY, client_id INTEGER, period TEXT NOT NULL,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS invoices_company(
  id INTEGER PRIMARY KEY, reconciliation_id INTEGER NOT NULL,
  partner_cui TEXT, invoice_no TEXT, date TEXT,
  base REAL, vat REAL, category TEXT);
CREATE TABLE IF NOT EXISTS invoices_anaf(
  id INTEGER PRIMARY KEY, reconciliation_id INTEGER NOT NULL,
  partner_cui TEXT, invoice_no TEXT, date TEXT,
  base REAL, vat REAL, category TEXT);
CREATE TABLE IF NOT EXISTS differences(
  id INTEGER PRIMARY KEY, reconciliation_id INTEGER NOT NULL,
  diff_type TEXT NOT NULL, details TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY, user_id TEXT, action TEXT NOT NULL,
  entity TEXT, entity_id TEXT, ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cod_mappings(
  id INTEGER PRIMARY KEY, client_id INTEGER, direction TEXT NOT NULL,
  cod TEXT NOT NULL, line_no TEXT NOT NULL, updated_at TEXT NOT NULL,
  updated_by TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cod_mappings_client
  ON cod_mappings(client_id, direction, cod) WHERE client_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_cod_mappings_direct
  ON cod_mappings(direction, cod) WHERE client_id IS NULL;
CREATE TABLE IF NOT EXISTS risc_fiscal_perioade(
  id INTEGER PRIMARY KEY, client_id INTEGER, perioada TEXT NOT NULL,
  tip_raport TEXT NOT NULL, sursa_date TEXT NOT NULL,
  capitaluri_proprii REAL, datorii_totale REAL, cifra_afaceri REAL,
  rezultat_net REAL, declaratii_nedepuse_manual INTEGER,
  obligatii_restante_manual INTEGER, obligatii_crescute_manual INTEGER,
  flaguri_sectiune_b TEXT, scor_total_indicatori INTEGER,
  scor_max_posibil INTEGER, scor_afisat INTEGER, clasificare TEXT,
  scor_detaliu TEXT, creat_de TEXT NOT NULL, creat_la TEXT NOT NULL,
  saft_xml_original BLOB);
CREATE UNIQUE INDEX IF NOT EXISTS idx_risc_fiscal_perioade_client
  ON risc_fiscal_perioade(client_id, perioada) WHERE client_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_risc_fiscal_perioade_direct
  ON risc_fiscal_perioade(perioada) WHERE client_id IS NULL;
CREATE TABLE IF NOT EXISTS risc_fiscal_alerte(
  id INTEGER PRIMARY KEY, perioada_id INTEGER NOT NULL,
  semnatura TEXT NOT NULL, trimis_la TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS idx_risc_fiscal_alerte_semnatura
  ON risc_fiscal_alerte(perioada_id, semnatura);
"""


def open_db(path: str, key: bytes):
    # Import lazily (not at module level) so ETVA_DB=postgres deployments,
    # which import this module only for PERMISSIONS/DEFAULT_ROLES/DbError
    # and never call open_db(), don't pay to load the sqlcipher C extension
    # in every gunicorn worker (no --preload, see portal/wsgi.py).
    try:
        from sqlcipher3 import dbapi2 as sqlcipher
    except ImportError:  # sqlcipher3-binary exposes the same API
        from pysqlcipher3 import dbapi2 as sqlcipher

    # check_same_thread=False only lifts sqlite3's same-thread assertion -
    # concurrent statement execution on this connection from multiple
    # request threads is still unsafe. portal/app.py serializes all
    # requests (and therefore all use of this connection) around a single
    # lock, so callers must not bypass that.
    conn = sqlcipher.connect(path, check_same_thread=False)
    conn.row_factory = sqlcipher.Row
    conn.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
    try:
        conn.execute("SELECT count(*) FROM sqlite_master")
    except sqlcipher.DatabaseError:
        conn.close()
        raise DbError("Cheie gresita sau baza de date corupta.")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate_reconciliations_nullable_client(conn) -> None:
    """reconciliations.client_id was NOT NULL, forcing every reconciliation
    through a client row - even for a 'direct' firm (a single PFA/SRL
    reconciling itself), which used to get a fake client pointing at its
    own CUI just to satisfy this column. A direct firm now reconciles
    with client_id NULL (itself, no client involved), so older firm
    databases need the column relaxed to allow that."""
    cols = conn.execute("PRAGMA table_info(reconciliations)").fetchall()
    client_col = next((c for c in cols if c["name"] == "client_id"), None)
    if client_col is None or not client_col["notnull"]:
        return
    conn.executescript(
        "CREATE TABLE reconciliations_new("
        "  id INTEGER PRIMARY KEY, client_id INTEGER, period TEXT NOT NULL,"
        "  created_at TEXT NOT NULL, created_by TEXT NOT NULL);")
    conn.execute(
        "INSERT INTO reconciliations_new(id, client_id, period, created_at, created_by) "
        "SELECT id, client_id, period, created_at, created_by FROM reconciliations")
    conn.executescript(
        "DROP TABLE reconciliations; "
        "ALTER TABLE reconciliations_new RENAME TO reconciliations;")
    conn.commit()


def _migrate_add_clients_gdpr(conn) -> None:
    """Firm databases created before the GDPR confirmation columns - add
    them, defaulting existing clients to 0 (neconfirmat): firma de
    contabilitate nu a declarat nimic pentru ei la adaugare, deci starea
    reala e 'neconfirmat', nu una inventata. UI-ul o afiseaza ca atare si
    confirmarile noi se cer doar la clientii adaugati de acum inainte."""
    cols = {c["name"] for c in conn.execute("PRAGMA table_info(clients)")}
    if "gdpr_confirmat" in cols:
        return
    conn.executescript(
        "ALTER TABLE clients ADD COLUMN gdpr_confirmat INTEGER NOT NULL DEFAULT 0;"
        "ALTER TABLE clients ADD COLUMN gdpr_confirmat_de TEXT;"
        "ALTER TABLE clients ADD COLUMN gdpr_confirmat_la TEXT;")
    conn.commit()


def _migrate_add_risc_fiscal_saft_xml(conn) -> None:
    """Firm databases created before the SAF-T raw-upload option - adauga
    coloana care pastreaza fisierul XML D406 incarcat de contabil, nefolosit
    inca pentru extragere automata (vezi etva/risc_fiscal.py si planul
    modulului) - doar salvat pentru cand parserul va exista."""
    cols = {c["name"] for c in conn.execute("PRAGMA table_info(risc_fiscal_perioade)")}
    if "saft_xml_original" in cols:
        return
    conn.execute("ALTER TABLE risc_fiscal_perioade ADD COLUMN saft_xml_original BLOB;")
    conn.commit()


def init_schema(conn) -> None:
    conn.executescript(_SCHEMA)
    _migrate_reconciliations_nullable_client(conn)
    _migrate_add_clients_gdpr(conn)
    _migrate_add_risc_fiscal_saft_xml(conn)
    conn.commit()
