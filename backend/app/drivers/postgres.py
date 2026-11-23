import asyncio
import re
import tempfile
from pathlib import Path

import psycopg
from psycopg import sql

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
from app.drivers.dumping import capture_output, partial_of, run_dump, work_file
from app.drivers.restoring import run_restore, tool_version
from app.schemas.source import PostgresConfig
from app.services.naming import safe_component
from app.services.proc import clean_env
from app.services.redact import describe_error

_LIST_DATABASES = (
    "SELECT datname FROM pg_database "
    "WHERE NOT datistemplate AND datallowconn AND has_database_privilege(datname, 'CONNECT') "
    "ORDER BY datname"
)
# Never dumped under "all"; "postgres" can still be selected explicitly.
EXCLUDED_FROM_ALL = frozenset({"template0", "template1", "postgres"})
_CLIENT_MAJOR_RE = re.compile(r"\)\s*(\d+)")
_SERVER_MAJOR_RE = re.compile(r"\d+")
_CONNINFO_RE = re.compile(r"=|^postgres(ql)?://")


def client_major(version_line: str) -> int | None:
    match = _CLIENT_MAJOR_RE.search(version_line)
    return int(match.group(1)) if match else None


def server_major(version: str | None) -> int | None:
    match = _SERVER_MAJOR_RE.match(version or "")
    return int(match.group()) if match else None


# A pg_restore 17+ sets this parameter, which servers before 17 reject. Harmless.
_HARMLESS_RESTORE_ERRORS = re.compile(r'unrecognized configuration parameter "transaction_timeout"')
_TRANSACTION_TIMEOUT_SINCE = 17


class PostgresDriver(BaseDriver):
    source_type = "postgres"
    _server_version: str | None = None
    _restore_client: int | None = None

    def _probe(self) -> ConnectionTestResult:
        config = PostgresConfig.model_validate(self._config)
        with psycopg.connect(
            host=config.host,
            port=config.port,
            user=config.username,
            password=self._secrets.get("password") or None,
            dbname=config.connection_database,
            sslmode=config.tls_mode,
            connect_timeout=CONNECT_TIMEOUT,
            application_name=config.extra_options.get("application_name", "dbbm"),
            options=f"-c statement_timeout={CONNECT_TIMEOUT * 1000}",
            autocommit=True,
        ) as connection:
            version_row = connection.execute("SHOW server_version").fetchone()
            version = str(version_row[0]) if version_row else ""
            databases = [str(row[0]) for row in connection.execute(_LIST_DATABASES).fetchall()]
        self._server_version = version
        return ConnectionTestResult(
            ok=True,
            message=f"Connected to PostgreSQL {version}.",
            server_version=version,
            databases=databases,
        )

    # ---------- dump ----------

    def select_databases(self, listed: list[str]) -> list[str]:
        config = PostgresConfig.model_validate(self._config)
        if config.databases == "all":
            return [n for n in listed if n not in EXCLUDED_FROM_ALL]
        return list(config.databases)

    async def tool_versions(self) -> dict[str, str]:
        config = PostgresConfig.model_validate(self._config)
        env = clean_env({}, Path("/"))
        tools = {"pg_dump": (await capture_output(["pg_dump", "--version"], env)).strip()}
        if config.include_globals:
            tools["pg_dumpall"] = (await capture_output(["pg_dumpall", "--version"], env)).strip()
        return tools

    def check_compatibility(self, server_version: str | None, tools: dict[str, str]) -> None:
        client, server = client_major(tools.get("pg_dump", "")), server_major(server_version)
        if client is not None and server is not None and client < server:
            raise DumpError(
                f"pg_dump {client} is older than the PostgreSQL server ({server}) and cannot "
                f"back it up. Rebuild the image with PG_MAJOR={server} or newer."
            )

    def _env(self, config: PostgresConfig, work_dir: Path) -> dict[str, str]:
        extra = {
            "PGSSLMODE": config.tls_mode,
            "PGCONNECT_TIMEOUT": str(CONNECT_TIMEOUT),
            "PGAPPNAME": config.extra_options.get("application_name", "dbbm"),
        }
        if password := self._secrets.get("password"):
            extra["PGPASSWORD"] = password  # the child's environment only, never argv
        return clean_env(extra, work_dir)

    @staticmethod
    def _connection_args(config: PostgresConfig) -> list[str]:
        return ["--no-password", "-h", config.host, "-p", str(config.port), "-U", config.username]

    async def dump_database(self, database: str, ctx: DumpContext) -> DumpedArtifact:
        if _CONNINFO_RE.search(database):
            # libpq would read this as a connection string and could connect elsewhere.
            raise DumpError(
                "Database names containing '=' or starting with a URI scheme are not supported."
            )
        config = PostgresConfig.model_validate(self._config)
        final = work_file(ctx, safe_component(database), "dump")
        argv = [
            "pg_dump",
            "-Fc",
            *self._connection_args(config),
            "-d",
            database,
            "-f",
            str(partial_of(final)),
        ]
        size, digest = await run_dump(
            tool="pg_dump",
            argv=argv,
            env=self._env(config, ctx.work_dir),
            ctx=ctx,
            secrets=self.sensitive_values(),
            final_path=final,
            stream_stdout=False,
        )
        # Custom format is already compressed, so no extra gzip layer.
        return DumpedArtifact(
            database=database,
            path=final,
            extension="dump",
            size=size,
            sha256=digest,
            compressed=True,
            compression="pg_dump_custom",
        )

    async def dump_globals(self, ctx: DumpContext) -> DumpedArtifact | None:
        config = PostgresConfig.model_validate(self._config)
        if not config.include_globals:
            return None
        extension = "sql.gz" if ctx.compress else "sql"
        final = work_file(ctx, "globals", extension)
        argv = [
            "pg_dumpall",
            "--globals-only",
            *self._connection_args(config),
            "-l",
            config.connection_database,
        ]
        size, digest = await run_dump(
            tool="pg_dumpall",
            argv=argv,
            env=self._env(config, ctx.work_dir),
            ctx=ctx,
            secrets=self.sensitive_values(),
            final_path=final,
            stream_stdout=True,
        )
        return DumpedArtifact(
            database="globals",
            path=final,
            extension=extension,
            size=size,
            sha256=digest,
            compressed=ctx.compress,
            compression="gzip" if ctx.compress else "none",
        )

    # ---------- restore ----------

    async def restore_tool_versions(self) -> dict[str, str]:
        line = await tool_version("pg_restore", Path(tempfile.gettempdir()))
        self._restore_client = client_major(line)
        return {"pg_restore": line}

    def check_restore_compatibility(self, tools: dict[str, str]) -> list[str]:
        client, server = (
            client_major(tools.get("pg_restore", "")),
            server_major(self._server_version),
        )
        if client is not None and server is not None and client < server:
            raise RestoreError(
                f"pg_restore {client} is older than the PostgreSQL server ({server}) and cannot "
                f"restore into it. Rebuild the image with PG_MAJOR={server} or newer."
            )
        return []

    def _prepare_target_sync(self, options: RestoreOptions) -> None:
        """Create the target database if it does not exist yet."""
        config = PostgresConfig.model_validate(self._config)
        with psycopg.connect(
            host=config.host,
            port=config.port,
            user=config.username,
            password=self._secrets.get("password") or None,
            dbname=config.connection_database,
            sslmode=config.tls_mode,
            connect_timeout=CONNECT_TIMEOUT,
            application_name=config.extra_options.get("application_name", "dbbm"),
            autocommit=True,
        ) as connection:
            exists = connection.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (options.target_database,)
            ).fetchone()
            if exists is None:
                connection.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(options.target_database))
                )

    async def restore_database(
        self, artifact: Path, options: RestoreOptions, ctx: RestoreContext
    ) -> None:
        if options.database == "globals":
            raise RestoreError(
                "The roles file (globals) cannot be restored here. Download it and apply it "
                "with psql."
            )
        config = PostgresConfig.model_validate(self._config)
        try:
            await asyncio.to_thread(self._prepare_target_sync, options)
        except psycopg.Error as exc:
            raise RestoreError(
                "Cannot prepare the target database: "
                f"{describe_error(exc, self.sensitive_values())}"
            ) from None
        atomic = not self._client_newer_than_server()
        if not atomic:
            ctx.log.warning(
                f"pg_restore {self._restore_client} is newer than the server "
                f"({server_major(self._server_version)}). It cannot run in a single transaction, "
                "so a failed restore can leave the target partly restored."
            )
        # Owners and privileges are not restored, because the roles may not exist on the
        # target (see the globals file). Where possible it is one transaction, so a failed
        # restore leaves the target as it was.
        argv = [
            "pg_restore",
            *self._connection_args(config),
            "-d",
            options.target_database,
            "--no-owner",
            "--no-privileges",
        ]
        if atomic:
            argv.append("--single-transaction")
        if options.drop:
            argv += ["--clean", "--if-exists"]
        for table in options.only:
            argv += ["-t", table]
        argv.append(str(artifact))
        await run_restore(
            tool="pg_restore",
            argv=argv,
            env=self._env(config, ctx.work_dir),
            ctx=ctx,
            secrets=self.sensitive_values(),
            ignorable=None if atomic else _HARMLESS_RESTORE_ERRORS,
        )

    def _client_newer_than_server(self) -> bool:
        server = server_major(self._server_version)
        return (
            self._restore_client is not None
            and server is not None
            and self._restore_client >= _TRANSACTION_TIMEOUT_SINCE > server
        )
