"""Backup encryption with the `age` command-line tool.

Encryption needs only public keys (recipients). Decryption needs the private key (identity),
which is handed to `age` on standard input and never written to disk, a log or the database.
"""

import asyncio
import hashlib
import os
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path

from app.drivers.base import DumpError
from app.drivers.dumping import capture_output, summarize_stderr
from app.services.hashing import sha256_file
from app.services.proc import StderrTail, StepTimeoutError, clean_env, run_process

# bech32: the characters 1, b, i and o are not used.
_PUBLIC_KEY = re.compile(r"^age1[02-9ac-hj-np-z]{58}$")
_PRIVATE_KEY = re.compile(r"^AGE-SECRET-KEY-1[02-9AC-HJ-NP-Z]{58}$")

DECRYPT_TIMEOUT = 3600


class AgeError(DumpError):
    """Encryption or decryption failed. The message is safe to store and show."""


def valid_public_key(value: str) -> bool:
    return _PUBLIC_KEY.fullmatch(value) is not None


def fingerprint(public_key: str) -> str:
    """A short, stable label for a public key, shown next to backups made with it."""
    digest = hashlib.sha256(public_key.encode("ascii")).hexdigest()[:16]
    return "-".join(digest[i : i + 4] for i in range(0, 16, 4))


def extract_private_key(text: str) -> str:
    """The identity from pasted text or an `age-keygen` file (comment lines are ignored)."""
    lines = [line.strip() for line in text.splitlines()]
    keys = [line for line in lines if line and not line.startswith("#")]
    if len(keys) != 1 or not _PRIVATE_KEY.fullmatch(keys[0]):
        raise AgeError(
            "That is not an age private key. It starts with AGE-SECRET-KEY-1 and is a single line."
        )
    return keys[0]


async def version() -> str:
    env = clean_env({}, Path(tempfile.gettempdir()))
    try:
        output = await capture_output(["age", "--version"], env)
    except Exception:
        raise AgeError("age is not installed in this environment.") from None
    return (output.strip().splitlines() or ["unknown"])[0]


async def encrypt_file(
    source: Path, target: Path, recipients: Sequence[str], timeout: int
) -> tuple[int, str]:
    """Encrypt `source` to every recipient into `target`; returns (size, sha256) of the result.

    `age` reads and writes the files itself, so memory use is constant. The output appears
    under its final name only when complete.
    """
    if not recipients:
        raise AgeError("No recipient is configured, so the backup cannot be encrypted.")
    if not all(valid_public_key(r) for r in recipients):
        raise AgeError("A recipient is not a valid age public key.")
    partial = target.with_name(target.name + ".partial")
    argv = ["age", "--encrypt"]
    for recipient in recipients:
        argv += ["-r", recipient]
    argv += ["-o", str(partial), str(source)]
    tail = StderrTail()
    try:
        code = await run_process(argv, env=clean_env({}, source.parent), timeout=timeout, tail=tail)
        if code != 0:
            raise AgeError(f"Encryption failed: {summarize_stderr(tail.text(), [])}")
        digest, size = await asyncio.to_thread(sha256_file, partial)
        os.replace(partial, target)
        return size, digest
    except StepTimeoutError:
        raise AgeError(f"Encryption timed out after {timeout} seconds.") from None
    except FileNotFoundError:
        raise AgeError("age is not installed in this environment.") from None
    except OSError as exc:
        raise AgeError(f"Cannot write the encrypted file: {exc.strerror}.") from None
    finally:
        partial.unlink(missing_ok=True)


def explain_decrypt_failure(stderr: str) -> str:
    """A plain-language reason for a failed `age --decrypt`, from its error output.

    The texts are those of age 1.3: a wrong key, a damaged payload (age cannot tell a truncated
    payload from a corrupted or tampered one, they all fail authentication), and a bad header."""
    text = stderr.lower()
    if "no identity matched" in text:
        return "The private key does not match any key this backup was encrypted to."
    if "payload chunk" in text or "message authentication" in text:
        return (
            "The backup file is truncated, corrupted or has been tampered with "
            "(age cannot tell these apart)."
        )
    if "unexpected intro" in text or "file is empty" in text or "not an age" in text:
        return "The file is not an age-encrypted backup."
    if "failed to read header" in text and "eof" in text:
        return "The backup file is truncated or incomplete."
    if "header" in text:
        return "The backup file has a damaged header: it is corrupted or has been tampered with."
    return f"Decryption failed: {summarize_stderr(stderr, [])}"


async def decrypt_file(source: Path, target: Path, private_key: str, timeout: int) -> None:
    """Decrypt `source` into `target` with the private key, which is passed on stdin only."""
    partial = target.with_name(target.name + ".partial")
    argv = ["age", "--decrypt", "-i", "-", "-o", str(partial), str(source)]
    tail = StderrTail()

    async def send_key(writer: asyncio.StreamWriter) -> None:
        writer.write(private_key.encode("ascii") + b"\n")
        await writer.drain()

    try:
        code = await run_process(
            argv,
            env=clean_env({}, target.parent),
            timeout=timeout,
            tail=tail,
            stdin_source=send_key,
        )
        if code != 0:
            raise AgeError(explain_decrypt_failure(tail.text()))
        os.replace(partial, target)
    except StepTimeoutError:
        raise AgeError(f"Decryption timed out after {timeout} seconds.") from None
    except FileNotFoundError:
        raise AgeError("age is not installed in this environment.") from None
    except OSError as exc:
        raise AgeError(f"Cannot write the decrypted file: {exc.strerror}.") from None
    finally:
        partial.unlink(missing_ok=True)
