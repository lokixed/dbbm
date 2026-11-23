import json

import pytest

from app.core.logging import redact
from app.drivers.base import BaseDriver, ConnectionTestResult
from app.drivers.mongo import uri_secrets
from app.services.redact import MAX_MESSAGE_LENGTH, describe_error, sanitize, scrub

PASSWORD = "p@ss/w0rd:secret"


def test_known_secrets_and_variants_removed() -> None:
    text = f"auth failed for {PASSWORD} and p%40ss%2Fw0rd%3Asecret"
    out = sanitize(text, [PASSWORD])
    assert "w0rd" not in out and "secret" not in out


def test_uris_are_removed() -> None:
    out = sanitize("cannot connect to mongodb://user:hunter2@host:27017/db?x=1 now")
    assert "hunter2" not in out and "mongodb://" not in out
    assert "cannot connect" in out


def test_tokens_and_keys_are_removed() -> None:
    jwt = "eyJhbGciOiJSUzI1NiJ9.eyJpc3MiOiJ4In0.c2ln"
    pem = "-----BEGIN PRIVATE KEY-----\nMIIEvQ\n-----END PRIVATE KEY-----"
    out = sanitize(f"Bearer ya29.abc-DEF {jwt} {pem} token=abc123 password=hunter2")
    for leaked in ("ya29", "eyJ", "MIIEvQ", "abc123", "hunter2"):
        assert leaked not in out


def test_mysql_password_flag_stays_readable() -> None:
    out = sanitize("(1045, \"Access denied for user 'u'@'h' (using password: YES)\")")
    assert "using password: YES)" in out


def test_message_is_one_line_and_bounded() -> None:
    out = sanitize("a\n\n b\t" + "x" * 1000)
    assert "\n" not in out and len(out) <= MAX_MESSAGE_LENGTH


def test_describe_error_prefixes_type_and_sanitizes() -> None:
    out = describe_error(RuntimeError(f"bad {PASSWORD}"), [PASSWORD])
    assert out.startswith("RuntimeError:") and "w0rd" not in out


def test_mongo_uri_credentials_are_extracted() -> None:
    values = uri_secrets("mongodb://admin:s%40crt@h1:1,h2:2/db?replicaSet=rs")
    assert "s%40crt" in values and "s@crt" in values
    assert uri_secrets("mongodb://h:27017") == ["mongodb://h:27017"]


class _Leaky(BaseDriver):
    source_type = "leaky"

    def _probe(self) -> ConnectionTestResult:
        raise RuntimeError(f"boom mongodb://u:{self._secrets['password']}@h password={PASSWORD}")


def test_driver_failures_never_leak_secrets() -> None:
    result = _Leaky({}, {"password": PASSWORD}).test_connection()
    assert not result.ok
    assert PASSWORD not in result.message and "mongodb://" not in result.message


def test_scrub_keeps_line_structure_and_removes_secrets() -> None:
    text = "first line pw=hunter2\nsecond mysql://u:hunter2@h/db\nthird token: abc"
    result = scrub(text, ["hunter2"])
    assert result.count("\n") == 2
    assert "hunter2" not in result and "mysql://" not in result and "abc" not in result


# ---------- milestone 4: Mongo and Firestore secrets ----------

PEM = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq\nabc/def+ghi=\n-----END PRIVATE KEY-----\n"


@pytest.mark.parametrize("scheme", ["mongodb", "mongodb+srv"])
def test_mongo_uri_userinfo_is_masked_by_the_central_filter(scheme: str) -> None:
    out = redact(f"connecting to {scheme}://admin:hunter2@h1:27017,h2:27017/db?x=1 now")
    assert "hunter2" not in out and "admin" not in out
    assert out.startswith("connecting to ") and f"{scheme}://[REDACTED]@h1:27017" in out


@pytest.mark.parametrize("scheme", ["mongodb", "mongodb+srv"])
def test_mongo_uris_are_removed_from_messages_and_logs(scheme: str) -> None:
    out = scrub(f"error: {scheme}://admin:p%40ss@host/db?authSource=admin failed")
    assert "p%40ss" not in out and "admin" not in out and "host" not in out
    assert out == "error: [REDACTED] failed"


def test_pem_private_key_blocks_are_removed() -> None:
    for text in (f"key was:\n{PEM}\nend", f"truncated {PEM[:60]}"):
        for out in (redact(text), scrub(text), sanitize(text)):
            assert "MIIEvQ" not in out and "abc/def" not in out and "BEGIN" not in out


def test_json_private_key_fields_are_masked() -> None:
    blob = json.dumps(
        {
            "type": "service_account",
            "client_email": "bk@p.iam.gserviceaccount.com",
            "private_key": PEM,
            "private_key_id": "0123abcd",
            "project_id": "p",
        }
    )
    for out in (redact(blob), scrub(blob), sanitize(blob)):
        for leaked in ("MIIEvQ", "0123abcd", "bk@p.iam", "BEGIN"):
            assert leaked not in out
        assert "service_account" in out or out.count("REDACTED") >= 3


def test_json_fields_with_escaped_quotes_are_masked_whole() -> None:
    out = redact('{"private_key": "ab\\"cd\\nef", "client_email": "x@y", "keep": 1}')
    assert "ab" not in out and "cd" not in out and "ef" not in out and '"keep": 1' in out


def test_google_access_tokens_are_removed() -> None:
    for out in (redact("token ya29.a0AfH6SMB-xyz_123.abc used"), scrub("Authorization ya29.abc")):
        assert "ya29" not in out


def test_yaml_config_lines_are_masked_if_echoed() -> None:
    text = 'uri: "mongodb://u:pw with space@h/?a=\\"b"\npassword: \'sec ret\'\nother: ok'
    out = redact(text)
    assert "pw with" not in out and "sec ret" not in out and "other: ok" in out
    assert redact("password: plainpw").count("plainpw") == 0


def test_redaction_is_idempotent() -> None:
    once = scrub(f"{PEM} mongodb://u:p@h/ ya29.abc")
    assert scrub(once) == once


RCLONE_TOKEN = (
    '{"access_token":"ya29.a0AccessValue","token_type":"Bearer",'
    '"refresh_token":"1//0gRefreshValue-abcdefghij","expiry":"2026-11-22T10:00:00Z"}'
)


@pytest.mark.parametrize(
    "text",
    [
        f"token = {RCLONE_TOKEN}",
        f"[dbb]\ntype = drive\nclient_secret = GOCSPX-SecretValue\ntoken = {RCLONE_TOKEN}\n",
        RCLONE_TOKEN,
        '{"client_secret": "GOCSPX-SecretValue", "client_id": "cid"}',
        "Authorization: Bearer ya29.a0AccessValue",
        "authorization: Basic GOCSPX-SecretValue",
        "refresh failed for 1//0gRefreshValue-abcdefghij",
        json.dumps({"token": RCLONE_TOKEN}),
    ],
)
def test_rclone_credentials_are_redacted(text: str) -> None:
    for out in (redact(text), scrub(text), sanitize(text)):
        for leaked in ("a0AccessValue", "RefreshValue", "GOCSPX-SecretValue"):
            assert leaked not in out, out
    assert "type = drive" in redact(text) or "type = drive" not in text
