import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.core.logging import REDACTED, JsonRedactingFormatter, redact
from tests.conftest import MASTER_KEY, PASSWORD, login, setup_admin


def test_requests_do_not_log_secrets(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    setup_admin(client)
    login(client, password="wrong-password-123")
    login(client)

    assert PASSWORD not in caplog.text
    assert "wrong-password-123" not in caplog.text
    assert MASTER_KEY not in caplog.text
    session_cookie = client.cookies.get("dbbackup_session") or ""
    assert session_cookie and session_cookie not in caplog.text


def test_formatter_redacts_master_key_and_key_value_pairs() -> None:
    formatter = JsonRedactingFormatter(secrets=[MASTER_KEY])
    record = logging.LogRecord(
        "t",
        logging.INFO,
        __file__,
        1,
        "key=%s password=hunter2 uri: mongodb://u:p@h",
        (MASTER_KEY,),
        None,
    )
    output = json.loads(formatter.format(record))["msg"]
    assert MASTER_KEY not in output
    assert "hunter2" not in output
    assert "mongodb://u:p@h" not in output
    assert REDACTED in output


def test_formatter_redacts_exceptions() -> None:
    formatter = JsonRedactingFormatter(secrets=[MASTER_KEY])
    try:
        raise RuntimeError(f"failed with {MASTER_KEY}")
    except RuntimeError:
        import sys

        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "boom", (), sys.exc_info())
    assert MASTER_KEY not in formatter.format(record)


def test_redact_leaves_normal_text() -> None:
    assert redact("Login succeeded") == "Login succeeded"
