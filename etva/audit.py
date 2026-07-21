"""Append-only audit log. No update/delete is exposed, by design."""
from datetime import datetime, timezone


def log(conn, user_id, action, entity=None, entity_id=None) -> None:
    conn.execute(
        "INSERT INTO audit_log(user_id, action, entity, entity_id, ts) "
        "VALUES(?,?,?,?,?)",
        (user_id, action, entity, entity_id,
         datetime.now(timezone.utc).isoformat()))
    conn.commit()


def entries(conn, limit: int = 200) -> list:
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]
