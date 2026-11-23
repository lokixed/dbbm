import asyncio
import os
import shutil
import ssl
import tempfile
from pathlib import Path
from typing import Any

import pymysql

from app.drivers.base import (
    CONNECT_TIMEOUT,
    BaseDriver,
    ConnectionTestResult,
    DumpContext,
    DumpedArtifact,
    DumpError,
    RestoreContext,
    RestoreError,
    RestoreOptions,
)
from app.drivers.dumping import capture_output, run_dump, work_file
from app.drivers.restoring import run_restore, tool_version
from app.schemas.source import MySQLConfig
from app.services.naming import safe_component
from app.services.proc import clean_env
from app.services.redact import describe_error

SYSTEM_DATABASES = frozenset({"information_schema", "performance_schema", "sys"})
# Never dumped under "all"; a user can still select `mysql` explicitly.
EXCLUDED_FROM_ALL = SYSTEM_DATABASES | {"mysql"}
_CR_SSL_CONNECTION_ERROR = 2026
_SSL_MODES = {
    "disabled": "DISABLED",
    "preferred": "PREFERRED",
    "required": "REQUIRED",
    "verify_ca": "VERIFY_CA",
    "verify_identity": "VERIFY_IDENTITY",
}
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b"}


def _option_value(value: str) -> str:
    return '"' + "".join(_ESCAPES.get(c, c) for c in value) + '"'


def _ssl_context(mode: str) -> ssl.SSLContext | None:
    if mode == "disabled":
        return None
    context = ssl.create_default_context()
    if mode in ("preferred", "required"):
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif mode == "verify_ca":
        context.check_hostname = False
    return context


class MySQLDriver(BaseDriver):
    source_type = "mysql"

    def _connect(
        self,
        config: MySQLConfig,
        ssl_context: ssl.SSLContext | None,
        io_timeout: int = CONNECT_TIMEOUT,
    ) -> Any:
        return pymysql.connect(
            host=config.host,
            port=config.port,
            user=config.username,
            password=self._secrets.get("password", ""),
            connect_timeout=CONNECT_TIMEOUT,
            read_timeout=io_timeout,
            write_timeout=io_timeout,
            ssl=ssl_context,
            charset=config.extra_options.get("charset", "utf8mb4"),
        )

    def _open(self, config: MySQLConfig, io_timeout: int = CONNECT_TIMEOUT) -> Any:
        context = _ssl_context(config.tls_mode)
        try:
            return self._connect(config, context, io_timeout)
        except pymysql.err.OperationalError as exc:
            # "preferred" means: use TLS when the server offers it, otherwise plain.
            if (
                config.tls_mode == "preferred"
                and exc.args
                and exc.args[0] == _CR_SSL_CONNECTION_ERROR
            ):
                return self._connect(config, None, io_timeout)
            raise

    def _probe(self) -> ConnectionTestResult:
        config = MySQLConfig.model_validate(self._config)
        connection = self._open(config)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT VERSION()")
                version = str(cursor.fetchone()[0])
                cursor.execute("SHOW DATABASES")
                names = [str(row[0]) for row in cursor.fetchall()]
        finally:
            connection.close()
        databases = sorted(n for n in names if n.lower() not in SYSTEM_DATABASES)
        return ConnectionTestResult(
            ok=True,
            message=f"Connected to MySQL {version}.",
            server_version=version,
            databases=databases,
        )

    # ---------- dump ----------

    _help_text: str | None = None

    def select_databases(self, listed: list[str]) -> list[str]:
        config = MySQLConfig.model_validate(self._config)
        if config.databases == "all":
            return [n for n in listed if n.lower() not in EXCLUDED_FROM_ALL]
        return list(config.databases)

    async def tool_versions(self) -> dict[str, str]:
        env = clean_env({}, Path(tempfile.gettempdir()))
        version = (await capture_output(["mysqldump", "--version"], env)).strip()
        # Optional flags are used only when this client knows them (older clients differ).
        self._help_text = await capture_output(["mysqldump", "--help"], env)
        return {"mysqldump": version}

    def _supports(self, flag: str) -> bool:
        return flag in (self._help_text or "")

    def _write_options_file(self, config: MySQLConfig, parent: Path) -> Path:
        """A 0600 options file in a private 0700 directory; the caller removes the directory."""
        directory = Path(tempfile.mkdtemp(prefix=".cnf-", dir=parent))
        lines = [
            "[client]",
            f"user={_option_value(config.username)}",
            f"password={_option_value(self._secrets.get('password', ''))}",
            f"host={_option_value(config.host)}",
            f"port={config.port}",
            f"ssl-mode={_SSL_MODES[config.tls_mode]}",
        ]
        if charset := config.extra_options.get("charset"):
            lines.append(f"default-character-set={charset}")
        path = directory / "client.cnf"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        return path

    async def dump_database(self, database: str, ctx: DumpContext) -> DumpedArtifact:
        if database.startswith("-"):
            raise DumpError("Database names starting with '-' are not supported.")
        config = MySQLConfig.model_validate(self._config)
        if self._help_text is None:
            await self.tool_versions()
        extension = "sql.gz" if ctx.compress else "sql"
        final = work_file(ctx, safe_component(database), extension)
        try:
            options = self._write_options_file(config, ctx.work_dir)
        except OSError as exc:
            raise DumpError(f"Cannot prepare the dump: {exc.strerror}.") from None
        try:
            # The options file must be the first argument; it carries the credentials.
            argv = [
                "mysqldump",
                f"--defaults-extra-file={options}",
                "--single-transaction",
                "--routines",
                "--triggers",
                "--events",
            ]
            if self._supports("--set-gtid-purged"):
                argv.append("--set-gtid-purged=OFF")
            if self._supports("--no-tablespaces"):
                # Tablespaces are server-wide and dumping them needs the PROCESS privilege.
                argv.append("--no-tablespaces")
            argv.append(database)
            size, digest = await run_dump(
                tool="mysqldump",
                argv=argv,
                env=clean_env({}, ctx.work_dir),
                ctx=ctx,
                secrets=self.sensitive_values(),
                final_path=final,
                stream_stdout=True,
            )
        finally:
            shutil.rmtree(options.parent, ignore_errors=True)
        return DumpedArtifact(
            database=database,
            path=final,
            extension=extension,
            size=size,
            sha256=digest,
            compressed=ctx.compress,
            compression="gzip" if ctx.compress else "none",
        )

    # ---------- restore ----------

    async def restore_tool_versions(self) -> dict[str, str]:
        return {"mysql": await tool_version("mysql", Path(tempfile.gettempdir()))}

    def _prepare_target_sync(self, options: RestoreOptions, timeout: int) -> None:
        """Create the target database (dropping it first when asked)."""
        config = MySQLConfig.model_validate(self._config)
        quoted = "`" + options.target_database.replace("`", "``") + "`"
        connection = self._open(config, timeout)
        try:
            with connection.cursor() as cursor:
                if options.drop:
                    cursor.execute(f"DROP DATABASE IF EXISTS {quoted}")
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS {quoted}")
            connection.commit()
        finally:
            connection.close()

    async def restore_database(
        self, artifact: Path, options: RestoreOptions, ctx: RestoreContext
    ) -> None:
        config = MySQLConfig.model_validate(self._config)
        secrets = self.sensitive_values()
        try:
            await asyncio.to_thread(self._prepare_target_sync, options, ctx.timeout_seconds)
        except pymysql.err.Error as exc:
            raise RestoreError(
                f"Cannot prepare the target database: {describe_error(exc, secrets)}"
            ) from None
        try:
            defaults = self._write_options_file(config, ctx.work_dir)
        except OSError as exc:
            raise RestoreError(f"Cannot prepare the restore: {exc.strerror}.") from None
        try:
            # The options file must be the first argument; it carries the credentials.
            argv = ["mysql", f"--defaults-extra-file={defaults}", options.target_database]
            await run_restore(
                tool="mysql",
                argv=argv,
                env=clean_env({}, ctx.work_dir),
                ctx=ctx,
                secrets=secrets,
                stdin_path=artifact,
                gunzip=artifact.name.endswith(".gz"),
            )
        finally:
            shutil.rmtree(defaults.parent, ignore_errors=True)
