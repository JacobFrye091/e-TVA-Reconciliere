"""Keystore: a random 32-byte DB key wrapped twice (master password + recovery phrase)."""
import json, os, base64
from argon2.low_level import hash_secret_raw, Type
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from mnemonic import Mnemonic


class KeystoreError(Exception):
    pass


def _kdf(secret: bytes, salt: bytes) -> bytes:
    return hash_secret_raw(secret, salt, time_cost=3, memory_cost=65536,
                           parallelism=4, hash_len=32, type=Type.ID)


def _wrap(wrapping_key: bytes, db_key: bytes) -> dict:
    nonce = os.urandom(12)
    ct = AESGCM(wrapping_key).encrypt(nonce, db_key, None)
    return {"nonce": base64.b64encode(nonce).decode(),
            "ct": base64.b64encode(ct).decode()}


def _unwrap(wrapping_key: bytes, blob: dict) -> bytes:
    try:
        return AESGCM(wrapping_key).decrypt(
            base64.b64decode(blob["nonce"]), base64.b64decode(blob["ct"]), None)
    except InvalidTag:
        raise KeystoreError("Parola sau fraza de recuperare este incorecta.")


def create_keystore(master_password: str, path: str) -> str:
    db_key = os.urandom(32)
    phrase = Mnemonic("english").generate(strength=256)  # 24 words
    pw_salt, ph_salt = os.urandom(16), os.urandom(16)
    data = {
        "pw_salt": base64.b64encode(pw_salt).decode(),
        "ph_salt": base64.b64encode(ph_salt).decode(),
        "pw_wrap": _wrap(_kdf(master_password.encode(), pw_salt), db_key),
        "ph_wrap": _wrap(_kdf(phrase.encode(), ph_salt), db_key),
    }
    with open(path, "w") as f:
        json.dump(data, f)
    return phrase


def _load(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        raise KeystoreError("Fisierul keystore lipseste sau este corupt.")


def unlock_keystore(master_password: str, path: str) -> bytes:
    data = _load(path)
    key = _kdf(master_password.encode(), base64.b64decode(data["pw_salt"]))
    return _unwrap(key, data["pw_wrap"])


def recover_keystore(recovery_phrase: str, path: str, new_master_password: str) -> bytes:
    data = _load(path)
    phrase = " ".join(recovery_phrase.split())
    key = _kdf(phrase.encode(), base64.b64decode(data["ph_salt"]))
    db_key = _unwrap(key, data["ph_wrap"])
    new_salt = os.urandom(16)
    data["pw_salt"] = base64.b64encode(new_salt).decode()
    data["pw_wrap"] = _wrap(_kdf(new_master_password.encode(), new_salt), db_key)
    with open(path, "w") as f:
        json.dump(data, f)
    return db_key
