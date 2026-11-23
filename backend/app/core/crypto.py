import base64
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import get_settings

_HKDF_SALT = b"dbbackup/v1"


class DecryptionError(Exception):
    """Raised when a token is malformed or was encrypted with a different master key."""


@lru_cache(maxsize=16)
def derive_key(master_key: bytes, purpose: bytes) -> bytes:
    """Derive an independent 32-byte key for `purpose` from the master key."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=b"dbbackup:" + purpose,
    ).derive(master_key)


def _fernet(master_key: bytes) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(derive_key(master_key, b"fernet")))


def encrypt(plaintext: str) -> bytes:
    return _fernet(get_settings().master_key).encrypt(plaintext.encode("utf-8"))


def decrypt(token: bytes) -> str:
    try:
        return _fernet(get_settings().master_key).decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise DecryptionError("Cannot decrypt value with the configured master key.") from exc


def encrypt_with(master_key: bytes, plaintext: bytes) -> bytes:
    """Encrypt with an explicit master key (used by key rotation)."""
    return _fernet(master_key).encrypt(plaintext)


def decrypt_with(master_key: bytes, token: bytes) -> bytes:
    try:
        return _fernet(master_key).decrypt(token)
    except InvalidToken as exc:
        raise DecryptionError("Cannot decrypt value with the given master key.") from exc
