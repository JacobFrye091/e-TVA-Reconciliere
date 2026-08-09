"""Persistenta scorurilor de risc fiscal (etva.risc_fiscal.calculeaza_scor)
in tabela per-firma `risc_fiscal_perioade` - upsert pe cheia naturala
(client_id, perioada), acelasi tipar ca etva/cod_mappings.py::save_mapping
(inclusiv ramura separata pentru client_id NULL - firma 'direct' evaluandu-
se pe sine, fara client). Calculul (etva/risc_fiscal.py) ramane pur, fara
I/O - acest modul e strict persistenta, apelat DUPA ce scorul e deja calculat.
"""
import json
from datetime import datetime, timezone

from etva import dbcompat

_COLOANE_UPSERT = (
    "tip_raport", "sursa_date", "capitaluri_proprii", "datorii_totale",
    "cifra_afaceri", "rezultat_net", "declaratii_nedepuse_manual",
    "obligatii_restante_manual", "obligatii_crescute_manual",
    "flaguri_sectiune_b", "scor_total_indicatori", "scor_max_posibil",
    "scor_afisat", "clasificare", "scor_detaliu", "creat_de", "creat_la",
)


def salveaza_perioada(conn, client_id: "int | None", perioada: str,
                      sursa_date: str, date_financiare: dict, scor, *,
                      declaratii_nedepuse: "int | None" = None,
                      obligatii_restante: "bool | None" = None,
                      obligatii_crescute: "bool | None" = None,
                      flaguri_sectiune_b: "dict | None" = None,
                      username: str) -> int:
    """Insereaza sau actualizeaza perioada evaluata. `scor` e un
    etva.risc_fiscal.ScorRiscFiscal deja calculat de apelant."""
    now = datetime.now(timezone.utc).isoformat()
    valori = (
        scor.nivel, sursa_date, date_financiare.get("capitaluri_proprii"),
        date_financiare.get("datorii_totale"), date_financiare.get("cifra_afaceri"),
        date_financiare.get("rezultat_net"), declaratii_nedepuse,
        obligatii_restante, obligatii_crescute,
        json.dumps(flaguri_sectiune_b or {}), scor.scor_total_indicatori,
        scor.scor_max_posibil, scor.scor_afisat, scor.clasificare,
        json.dumps(scor.detaliu), username, now)
    set_clause = ", ".join(f"{c}=excluded.{c}" for c in _COLOANE_UPSERT)
    coloane_sql = ", ".join(_COLOANE_UPSERT)
    if client_id is None:
        # Doua firme 'directe' diferite pot evalua exact aceeasi `perioada`
        # (ex. "2026-T3" - toate raporteaza pe acelasi calendar fiscal) - pe
        # Postgres (tabela partajata) tinta ON CONFLICT trebuie scopata pe
        # firm_id (idx_risc_fiscal_perioade_direct_firm, vezi pg_schema.sql),
        # altfel a doua firma lovea indexul unic al primeia. SQLite ramane pe
        # indexul vechi: fisier per firma, deci nu are coloana firm_id.
        conflict = ("(firm_id, perioada)" if dbcompat.backend() == "postgres"
                   else "(perioada)")
        placeholders = ", ".join(["?"] * (1 + len(valori)))  # perioada + valori
        conn.execute(
            f"INSERT INTO risc_fiscal_perioade(client_id, perioada, {coloane_sql}) "
            f"VALUES(NULL, {placeholders}) "
            f"ON CONFLICT {conflict} WHERE client_id IS NULL "
            "DO UPDATE SET " + set_clause,
            (perioada, *valori))
    else:
        placeholders = ", ".join(["?"] * (2 + len(valori)))  # client_id + perioada + valori
        conn.execute(
            f"INSERT INTO risc_fiscal_perioade(client_id, perioada, {coloane_sql}) "
            f"VALUES({placeholders}) "
            "ON CONFLICT (client_id, perioada) WHERE client_id IS NOT NULL "
            "DO UPDATE SET " + set_clause,
            (client_id, perioada, *valori))
    conn.commit()
    if client_id is None:
        row = conn.execute(
            "SELECT id FROM risc_fiscal_perioade WHERE client_id IS NULL "
            "AND perioada=?", (perioada,)).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM risc_fiscal_perioade WHERE client_id=? AND perioada=?",
            (client_id, perioada)).fetchone()
    return row["id"]


def _decodeaza(row) -> dict:
    d = dict(row)
    d["flaguri_sectiune_b"] = json.loads(d["flaguri_sectiune_b"] or "{}")
    d["scor_detaliu"] = json.loads(d["scor_detaliu"] or "[]")
    return d


def lista_perioade(conn, client_id: "int | None") -> list:
    """Istoricul perioadelor evaluate pentru un client (sau pentru scope-ul
    'direct' al firmei daca client_id e None), cea mai recenta perioada
    prima - folosit pentru evolutia scorului in timp."""
    if client_id is None:
        rows = conn.execute(
            "SELECT * FROM risc_fiscal_perioade WHERE client_id IS NULL "
            "ORDER BY perioada DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM risc_fiscal_perioade WHERE client_id=? "
            "ORDER BY perioada DESC", (client_id,)).fetchall()
    return [_decodeaza(r) for r in rows]


def perioade_cu_risc_ridicat(conn) -> list:
    """Toate perioadele (indiferent de client) clasificate 'ridicat' -
    folosit de portal/risk_alerts.py, care scaneaza fiecare firma o data
    (nu client cu client, ca lista_perioade). Include numele/CUI-ul
    clientului (NULL pentru scope-ul 'direct' al firmei) - alerta are
    nevoie de el ca sa arate cui se refera evaluarea."""
    rows = conn.execute(
        "SELECT p.*, c.name AS client_name, c.cui AS client_cui "
        "FROM risc_fiscal_perioade p LEFT JOIN clients c ON c.id = p.client_id "
        "WHERE p.clasificare='ridicat'").fetchall()
    return [_decodeaza(r) for r in rows]


def obtine_perioada(conn, client_id: "int | None", perioada: str) -> "dict | None":
    if client_id is None:
        row = conn.execute(
            "SELECT * FROM risc_fiscal_perioade WHERE client_id IS NULL "
            "AND perioada=?", (perioada,)).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM risc_fiscal_perioade WHERE client_id=? AND perioada=?",
            (client_id, perioada)).fetchone()
    return _decodeaza(row) if row else None
