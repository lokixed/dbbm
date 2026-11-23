"""The encryption service against the real `age` and `age-keygen` binaries.

Skipped without them; `make test-integration-docker` (or any run inside the application image)
has both. The unit tests use a fake `age`: this file checks the real tool behaves as the code
assumes (file arguments, the identity on stdin, and the wording of its errors).
"""

import asyncio
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import age

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not all(shutil.which(t) for t in ("age", "age-keygen")),
        reason="age and age-keygen must be installed",
    ),
]


def keygen() -> tuple[str, str]:
    out = subprocess.run(["age-keygen"], capture_output=True, text=True, check=True)  # noqa: S603, S607
    private = next(x for x in out.stdout.splitlines() if x.startswith("AGE-SECRET-KEY-"))
    public = next(x.split(": ")[1] for x in out.stderr.splitlines() if x.startswith("Public key:"))
    return public, private


@pytest.fixture
def plain(tmp_path: Path) -> Path:
    path = tmp_path / "dump.sql.gz"
    path.write_bytes(os.urandom(3_000_000) + b"END")
    return path


def test_real_keys_match_the_validators() -> None:
    public, private = keygen()
    assert age.valid_public_key(public)
    assert (
        age.extract_private_key(f"# created: now\n# public key: {public}\n{private}\n") == private
    )
    assert age.fingerprint(public) == age.fingerprint(public)


def test_round_trip_to_several_recipients(plain: Path, tmp_path: Path) -> None:
    (pub1, priv1), (pub2, priv2) = keygen(), keygen()
    target = tmp_path / "dump.sql.gz.age"
    size, digest = asyncio.run(age.encrypt_file(plain, target, [pub1, pub2], 60))

    assert target.stat().st_size == size and not (tmp_path / "dump.sql.gz.age.partial").exists()
    assert plain.read_bytes()[:64] not in target.read_bytes()  # it really is ciphertext
    assert digest == hashlib.sha256(target.read_bytes()).hexdigest()
    for number, key in enumerate((priv1, priv2)):
        out = tmp_path / f"out{number}"
        asyncio.run(age.decrypt_file(target, out, key, 60))  # the key goes in on stdin
        assert out.read_bytes() == plain.read_bytes()


def test_a_wrong_key_is_reported_and_leaves_no_file(plain: Path, tmp_path: Path) -> None:
    pub, _priv = keygen()
    _other_pub, other_priv = keygen()
    target = tmp_path / "x.age"
    asyncio.run(age.encrypt_file(plain, target, [pub], 60))
    with pytest.raises(age.AgeError, match="does not match any key"):
        asyncio.run(age.decrypt_file(target, tmp_path / "out", other_priv, 60))
    assert not (tmp_path / "out").exists() and not (tmp_path / "out.partial").exists()


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (lambda d: d[: len(d) // 2], "truncated, corrupted or has been tampered with"),
        (lambda d: d[:-16], "truncated, corrupted or has been tampered with"),
        (lambda d: d[:100], "truncated or incomplete"),
        (lambda d: d[:120] + bytes([d[120] ^ 255]) + d[121:], "damaged header"),
        (
            lambda d: d[: len(d) // 2] + bytes([d[len(d) // 2] ^ 255]) + d[len(d) // 2 + 1 :],
            "tampered",
        ),
        (lambda d: b"hello\n", "not an age-encrypted backup"),
        (lambda d: b"", "not an age-encrypted backup"),
    ],
)
def test_damaged_files_are_reported_clearly(
    plain: Path, tmp_path: Path, damage: object, message: str
) -> None:
    pub, priv = keygen()
    good = tmp_path / "good.age"
    asyncio.run(age.encrypt_file(plain, good, [pub], 60))
    broken = tmp_path / "broken.age"
    broken.write_bytes(damage(good.read_bytes()))  # type: ignore[operator]
    with pytest.raises(age.AgeError, match=message):
        asyncio.run(age.decrypt_file(broken, tmp_path / "out", priv, 60))
    assert not (tmp_path / "out").exists()


def test_encrypting_without_recipients_fails_closed(plain: Path, tmp_path: Path) -> None:
    with pytest.raises(age.AgeError, match="No recipient"):
        asyncio.run(age.encrypt_file(plain, tmp_path / "x.age", [], 60))
    assert not (tmp_path / "x.age").exists()
