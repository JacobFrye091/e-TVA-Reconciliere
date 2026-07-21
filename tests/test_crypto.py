import pytest
from etva import crypto

def test_create_and_unlock(tmp_path):
    ks = str(tmp_path / "keystore.json")
    phrase = crypto.create_keystore("Parola123!", ks)
    assert len(phrase.split()) == 24
    key = crypto.unlock_keystore("Parola123!", ks)
    assert isinstance(key, bytes) and len(key) == 32

def test_wrong_password_raises(tmp_path):
    ks = str(tmp_path / "keystore.json")
    crypto.create_keystore("Parola123!", ks)
    with pytest.raises(crypto.KeystoreError):
        crypto.unlock_keystore("gresit", ks)

def test_recover_with_phrase(tmp_path):
    ks = str(tmp_path / "keystore.json")
    phrase = crypto.create_keystore("Parola123!", ks)
    key1 = crypto.unlock_keystore("Parola123!", ks)
    key2 = crypto.recover_keystore(phrase, ks, "ParolaNoua456!")
    assert key1 == key2
    assert crypto.unlock_keystore("ParolaNoua456!", ks) == key1
    with pytest.raises(crypto.KeystoreError):
        crypto.unlock_keystore("Parola123!", ks)

def test_wrong_phrase_raises(tmp_path):
    ks = str(tmp_path / "keystore.json")
    crypto.create_keystore("Parola123!", ks)
    bad = " ".join(["abandon"] * 24)
    with pytest.raises(crypto.KeystoreError):
        crypto.recover_keystore(bad, ks, "x")
