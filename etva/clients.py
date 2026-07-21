"""Clients CRUD and user-client assignments."""
from etva.permissions import user_permissions


class ClientError(Exception):
    pass


def create_client(conn, cui: str, name: str) -> int:
    if conn.execute("SELECT 1 FROM clients WHERE cui=?", (cui,)).fetchone():
        raise ClientError("Exista deja un client cu acest CUI.")
    cur = conn.execute("INSERT INTO clients(cui, name) VALUES(?,?)",
                       (cui, name))
    conn.commit()
    return cur.lastrowid


def assign(conn, user_id: int, client_id: int) -> None:
    conn.execute("INSERT OR IGNORE INTO client_assignments VALUES(?,?)",
                 (user_id, client_id))
    conn.commit()


def visible_clients(conn, user_id: int) -> list:
    perms = user_permissions(conn, user_id)
    if "clienti.creare" in perms or "useri.gestionare" in perms:
        rows = conn.execute("SELECT * FROM clients ORDER BY name")
    else:
        rows = conn.execute(
            "SELECT c.* FROM clients c JOIN client_assignments a "
            "ON a.client_id = c.id WHERE a.user_id=? ORDER BY c.name",
            (user_id,))
    return [dict(r) for r in rows]


def delete_client(conn, client_id: int) -> None:
    conn.execute("DELETE FROM client_assignments WHERE client_id=?",
                 (client_id,))
    conn.execute("DELETE FROM clients WHERE id=?", (client_id,))
    conn.commit()
