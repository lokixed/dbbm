import json
from collections.abc import Iterable, Mapping

from pydantic import SecretStr

from app.core.crypto import DecryptionError, decrypt, encrypt
from app.schemas.common import SecretsIn, SecretsInvalid

UNDECRYPTABLE_MESSAGE = (
    "Stored credentials cannot be decrypted with the configured master key. "
    "Re-enter the credentials, or restore the original APP_SECRET_KEY."
)


class SecretsUnavailable(Exception):
    """The stored secrets blob cannot be decrypted (wrong master key or corrupt data)."""


def load_secrets(blob: bytes | None) -> dict[str, str]:
    if not blob:
        return {}
    try:
        data = json.loads(decrypt(blob))
    except (DecryptionError, ValueError):
        raise SecretsUnavailable from None
    if not isinstance(data, dict):
        raise SecretsUnavailable
    return {str(k): str(v) for k, v in data.items()}


def store_secrets(secrets: Mapping[str, str]) -> bytes | None:
    return encrypt(json.dumps(dict(secrets))) if secrets else None


def merge_secrets(
    existing: Mapping[str, str], incoming: SecretsIn, allowed: Iterable[str]
) -> dict[str, str]:
    """Omitted keeps the stored value, an explicit null clears it, a value replaces it."""
    provided: dict[str, str | None] = {}
    for field in incoming.model_fields_set:
        value: SecretStr | None = getattr(incoming, field)
        provided[field] = None if value is None else value.get_secret_value()
    return merge_values(existing, provided, allowed)


def merge_values(
    existing: Mapping[str, str], provided: Mapping[str, str | None], allowed: Iterable[str]
) -> dict[str, str]:
    allowed = tuple(allowed)
    merged = {k: v for k, v in existing.items() if k in allowed}
    for field, value in provided.items():
        if field not in allowed:
            if value is not None:
                raise SecretsInvalid(
                    field, "This secret is not used by the selected configuration."
                )
            continue
        if value is None:
            merged.pop(field, None)
        else:
            merged[field] = value
    return merged


def check_required(merged: Mapping[str, str], required: Iterable[str]) -> None:
    for field in required:
        if not merged.get(field):
            raise SecretsInvalid(field, "This credential is required.")
