import pytest
from cryptography.fernet import Fernet

from app.core.config import get_settings
from app.core.crypto import DecryptionError, decrypt, derive_key, encrypt
from tests.conftest import MASTER_KEY


def test_round_trip() -> None:
    token = encrypt("s3cret value ✓")
    assert isinstance(token, bytes)
    assert b"s3cret" not in token
    assert decrypt(token) == "s3cret value ✓"


def test_encryption_is_randomised() -> None:
    assert encrypt("same") != encrypt("same")


def test_wrong_master_key_cannot_decrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    token = encrypt("value")
    monkeypatch.setenv("APP_SECRET_KEY", "a-completely-different-master-key-0000")
    get_settings.cache_clear()
    with pytest.raises(DecryptionError):
        decrypt(token)


def test_tampered_token_is_rejected() -> None:
    token = bytearray(encrypt("value"))
    token[-3] ^= 0x01
    with pytest.raises(DecryptionError):
        decrypt(bytes(token))


def test_garbage_is_rejected() -> None:
    with pytest.raises(DecryptionError):
        decrypt(b"not a fernet token")


def test_derived_keys_are_purpose_separated() -> None:
    master = MASTER_KEY.encode()
    keys = {derive_key(master, p) for p in (b"fernet", b"session", b"csrf")}
    assert len(keys) == 3
    assert all(len(k) == 32 for k in keys)
    assert master not in keys


def test_fernet_key_is_not_the_raw_master_key() -> None:
    # Fernet only accepts a valid 32-byte urlsafe key, so this also proves the KDF output is usable.
    import base64

    Fernet(base64.urlsafe_b64encode(derive_key(MASTER_KEY.encode(), b"fernet")))
