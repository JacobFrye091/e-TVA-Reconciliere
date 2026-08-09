"""Mapari cod TVA -> linie D300 confirmate o data per client (sau per
firma 'direct', client_id=None) prin picker-ul ghidat din web/index.html,
reaplicate automat la fiecare reconciliere ulterioara a acelui client -
vezi etva/d300.py::classify_legend si portal/app.py::new_reconciliation.

Deliberat NEpopulat din campul liber cod_mapping (JSON, textarea "Corectie
mapare coduri TVA firma"): acela a produs exact bug-ul care a motivat acest
modul (o mapare gresita, ghicita din memorie, fara nicio validare) -
persistenta se intampla doar prin save_mapping(), apelat din picker dupa
ce utilizatorul alege dintr-o lista deja restransa la liniile valide.
"""
from datetime import datetime, timezone

from etva import dbcompat
from etva.d300 import valid_lines_for_direction


class MappingError(Exception):
    pass


def save_mapping(conn, client_id: "int | None", direction: str, cod: str,
                 line_no: str, username: str) -> None:
    """Insereaza sau actualizeaza (upsert dupa cheia naturala) o mapare
    confirmata. Raises MappingError daca line_no nu apartine sectiunii
    corecte pentru direction (acelasi guardrail ca la clasificarea
    automata - un picker ghidat nu ar trebui sa poata produce vreodata
    aceasta eroare, dar validarea ramane si aici, nu doar in UI)."""
    if line_no not in valid_lines_for_direction(direction):
        raise MappingError(
            f"Linia '{line_no}' nu e valida pentru un jurnal de {direction}.")
    now = datetime.now(timezone.utc).isoformat()
    if client_id is None:
        # Doua firme 'directe' diferite pot alege acelasi (direction, cod) -
        # pe Postgres (tabela partajata) tinta ON CONFLICT trebuie scopata pe
        # firm_id (idx_cod_mappings_direct_firm, vezi pg_schema.sql), altfel
        # a doua firma lovea indexul unic al primeia. SQLite ramane pe
        # indexul vechi: fisier per firma, deci nu are coloana firm_id.
        conflict = ("(firm_id, direction, cod)" if dbcompat.backend() == "postgres"
                   else "(direction, cod)")
        conn.execute(
            "INSERT INTO cod_mappings(client_id, direction, cod, line_no, "
            "updated_at, updated_by) VALUES(NULL,?,?,?,?,?) "
            f"ON CONFLICT {conflict} WHERE client_id IS NULL "
            "DO UPDATE SET line_no=excluded.line_no, "
            "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
            (direction, cod, line_no, now, username))
    else:
        conn.execute(
            "INSERT INTO cod_mappings(client_id, direction, cod, line_no, "
            "updated_at, updated_by) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT (client_id, direction, cod) WHERE client_id IS NOT NULL "
            "DO UPDATE SET line_no=excluded.line_no, "
            "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
            (client_id, direction, cod, line_no, now, username))
    conn.commit()


def load_for_client(conn, client_id: "int | None") -> dict:
    """{(direction, cod): line_no} pentru acest client, sau pentru
    scope-ul 'direct' al firmei daca client_id e None."""
    if client_id is None:
        rows = conn.execute(
            "SELECT direction, cod, line_no FROM cod_mappings "
            "WHERE client_id IS NULL")
    else:
        rows = conn.execute(
            "SELECT direction, cod, line_no FROM cod_mappings WHERE client_id=?",
            (client_id,))
    return {(r["direction"], r["cod"]): r["line_no"] for r in rows}


def list_for_client(conn, client_id: "int | None") -> list:
    """Randuri complete (id, direction, cod, line_no, updated_at,
    updated_by) pentru sectiunea 'Mapari confirmate' din UI."""
    if client_id is None:
        rows = conn.execute(
            "SELECT * FROM cod_mappings WHERE client_id IS NULL "
            "ORDER BY direction, cod")
    else:
        rows = conn.execute(
            "SELECT * FROM cod_mappings WHERE client_id=? ORDER BY direction, cod",
            (client_id,))
    return [dict(r) for r in rows]


def delete_mapping(conn, mapping_id: int) -> None:
    conn.execute("DELETE FROM cod_mappings WHERE id=?", (mapping_id,))
    conn.commit()
