import pytest
from sqlmodel import Session

from app.core.db import get_engine
from app.core.migrations import run_migrations
from app.models import Destination, Source
from app.schemas.common import SecretsInvalid
from app.schemas.source import MongoSecretsIn, PasswordSecretsIn
from app.services.secrets import (
    SecretsUnavailable,
    check_required,
    load_secrets,
    merge_secrets,
    store_secrets,
)


def test_omitted_keeps_null_clears_value_replaces() -> None:
    existing = {"password": "old"}
    kept = merge_secrets(existing, PasswordSecretsIn(), ["password"])
    assert kept == {"password": "old"}
    cleared = merge_secrets(existing, PasswordSecretsIn(password=None), ["password"])
    assert cleared == {}
    replaced = merge_secrets(existing, PasswordSecretsIn(password="new"), ["password"])
    assert replaced == {"password": "new"}


def test_fields_not_allowed_for_mode_are_pruned_or_rejected() -> None:
    merged = merge_secrets({"uri": "mongodb://x"}, MongoSecretsIn(), ["password"])
    assert merged == {}
    with pytest.raises(SecretsInvalid):
        merge_secrets({}, MongoSecretsIn(uri="mongodb://x"), ["password"])


def test_required_secret_must_be_present() -> None:
    check_required({"uri": "x"}, ["uri"])
    with pytest.raises(SecretsInvalid):
        check_required({}, ["uri"])


def test_blob_round_trip_and_not_plaintext() -> None:
    blob = store_secrets({"password": "hunter2-very-secret"})
    assert blob is not None and b"hunter2" not in blob
    assert load_secrets(blob) == {"password": "hunter2-very-secret"}
    assert store_secrets({}) is None and load_secrets(None) == {}


def test_garbage_blob_raises_clear_error() -> None:
    with pytest.raises(SecretsUnavailable):
        load_secrets(b"not-a-fernet-token")


def test_round_trip_through_models() -> None:
    run_migrations()
    with Session(get_engine()) as db:
        source = Source(
            name="s",
            type="mysql",
            config={"host": "h"},
            secrets_enc=store_secrets({"password": "pw-1"}),
        )
        destination = Destination(name="d", type="local", config={"path": "x"})
        db.add(source)
        db.add(destination)
        db.commit()
        db.refresh(source)
        db.refresh(destination)
        assert source.secrets_enc is not None and b"pw-1" not in source.secrets_enc
        assert load_secrets(source.secrets_enc) == {"password": "pw-1"}
        assert destination.secrets_enc is None
        assert destination.retention == {"keep_last": None, "keep_days": None}
        assert source.created_at.tzinfo is not None
