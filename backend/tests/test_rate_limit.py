import pytest
from fastapi.testclient import TestClient

from app.core.security import LoginRateLimiter
from tests.conftest import login, setup_admin


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_limiter_locks_after_max_attempts_and_expires() -> None:
    clock = FakeClock()
    limiter = LoginRateLimiter(max_attempts=3, lockout_seconds=60, clock=clock)
    for _ in range(3):
        assert limiter.retry_after("1.1.1.1", "admin") == 0
        limiter.record_failure("1.1.1.1", "admin")
    assert limiter.retry_after("1.1.1.1", "admin") == 60
    assert limiter.retry_after("1.1.1.1", "other") == 0

    clock.now += 61
    assert limiter.retry_after("1.1.1.1", "admin") == 0


def test_limiter_success_resets_counter() -> None:
    limiter = LoginRateLimiter(max_attempts=3, lockout_seconds=60, clock=FakeClock())
    limiter.record_failure("ip", "admin")
    limiter.record_failure("ip", "admin")
    limiter.record_success("ip", "admin")
    limiter.record_failure("ip", "admin")
    assert limiter.retry_after("ip", "admin") == 0


def test_limiter_blocks_username_spraying_from_one_ip() -> None:
    limiter = LoginRateLimiter(max_attempts=2, lockout_seconds=60, clock=FakeClock())
    for i in range(8):
        limiter.record_failure("ip", f"user{i}")
    assert limiter.retry_after("ip", "brand-new") > 0
    assert limiter.retry_after("other-ip", "brand-new") == 0


def test_login_locked_out_after_repeated_failures(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings
    from app.core.security import get_rate_limiter

    monkeypatch.setenv("LOGIN_MAX_ATTEMPTS", "3")
    get_settings.cache_clear()
    get_rate_limiter.cache_clear()

    setup_admin(client)
    for _ in range(3):
        assert login(client, password="wrong").status_code == 401

    blocked = login(client)
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0
    assert client.get("/api/auth/me").status_code == 401
