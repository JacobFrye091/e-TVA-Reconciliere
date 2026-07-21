"""Password hashing (Argon2id) and data-key wrapping (Fernet).

The Fernet secret lives in a local file next to the portal DB and is
auto-generated on first run. It must NEVER be committed to git.
"""
import os
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(pw_hash: str, password: str) -> bool:
    try:
        _ph.verify(pw_hash, password)
        return True
    except VerifyMismatchError:
        return False


def load_secret(path: str) -> bytes:
    if not os.path.exists(path):
        key = Fernet.generate_key()
        with open(path, "wb") as f:
            f.write(key)
    with open(path, "rb") as f:
        return f.read()


def wrap_key(secret: bytes, data_key: bytes) -> bytes:
    return Fernet(secret).encrypt(data_key)


def unwrap_key(secret: bytes, wrapped: bytes) -> bytes:
    return Fernet(secret).decrypt(wrapped)
