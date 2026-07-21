"""User accounts and login (Argon2id password hashing)."""
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_ph = PasswordHasher()


class AuthError(Exception):
    pass


def create_user(conn, username: str, password: str) -> int:
    if conn.execute("SELECT 1 FROM users WHERE username=?",
                    (username,)).fetchone():
        raise AuthError("Numele de utilizator exista deja.")
    cur = conn.execute(
        "INSERT INTO users(username, password_hash) VALUES(?,?)",
        (username, _ph.hash(password)))
    conn.commit()
    return cur.lastrowid


def verify_login(conn, username: str, password: str) -> int:
    row = conn.execute(
        "SELECT id, password_hash, active FROM users WHERE username=?",
        (username,)).fetchone()
    if row is None or not row["active"]:
        raise AuthError("Utilizator sau parola incorecta.")
    try:
        _ph.verify(row["password_hash"], password)
    except VerifyMismatchError:
        raise AuthError("Utilizator sau parola incorecta.")
    return row["id"]


def set_active(conn, user_id: int, active: bool) -> None:
    conn.execute("UPDATE users SET active=? WHERE id=?",
                 (1 if active else 0, user_id))
    conn.commit()
