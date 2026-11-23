import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from pydantic import TypeAdapter
from pymongo import MongoClient
from pymongo.errors import ConfigurationError, InvalidURI

from app.drivers.base import (
    CONNECT_TIMEOUT,
    BaseDriver,
    ConnectionTestResult,
    DumpContext,
    DumpedArtifact,
    DumpError,
)
from app.drivers.dumping import capture_output, run_dump, work_file
from app.schemas.source import MongoConfig, MongoFieldsConfig, MongoUriConfig
from app.services.naming import safe_component
from app.services.proc import clean_env

SYSTEM_DATABASES = frozenset({"local", "config"})
# Never dumped under "all"; a user can still select `admin` explicitly.
EXCLUDED_FROM_ALL = frozenset({"admin", "config", "local"})
FULL_INSTANCE = "full-instance"
_TIMEOUT_MS = CONNECT_TIMEOUT * 1000
_CONFIG: TypeAdapter[Any] = TypeAdapter(MongoConfig)
# mongodump logs progress to stderr; these lines are kept in the run log but not in the run error.
_PROGRESS_NOISE = re.compile(
    r"writing \S+ to archive|done dumping|dumping up to \d+ collections|\[[#.]*\]|archive prelude"
)


def uri_secrets(uri: str) -> list[str]:
    """The URI and the credentials embedded in it, for redaction."""
    values = [uri]
    authority = uri.partition("://")[2].split("/", 1)[0].split("?", 1)[0]
    if "@" in authority:
        userinfo = authority.rpartition("@")[0]
        password = userinfo.partition(":")[2]
        if password:
            values += [password, unquote(password)]
    return values


def build_uri(config: MongoFieldsConfig, password: str) -> str:
    """Connection URI for field mode; username and password are percent-encoded."""
    bare = ":" in config.host and not config.host.startswith("[")
    host = f"[{config.host}]" if bare else config.host
    credentials = ""
    if config.username:
        credentials = quote(config.username, safe="")
        if password:
            credentials += ":" + quote(password, safe="")
        credentials += "@"
    options: list[str] = []
    if config.username:
        options.append(f"authSource={quote(config.auth_database, safe='')}")
    if config.tls:
        options.append("tls=true")
    if config.replica_set:
        options.append(f"replicaSet={quote(config.replica_set, safe='')}")
    query = "?" + "&".join(options) if options else ""
    return f"mongodb://{credentials}{host}:{config.port}/{query}"


def strip_uri_database(uri: str) -> tuple[str, str | None]:
    """Remove the database from the URI path; return (uri, that database or None).

    mongodump treats a path database as --db, which conflicts with --oplog and with the database
    we choose. The path database is also the implicit auth database, so keep that meaning as an
    explicit authSource unless the URI already has one.
    """
    scheme, _, rest = uri.partition("://")
    head, mark, query = rest.partition("?")
    authority, _, path = head.partition("/")
    database = unquote(path)
    if not database:
        return uri, None
    if "authsource=" not in query.lower():
        pair = f"authSource={quote(database, safe='')}"
        query = f"{query}&{pair}" if query else pair
        mark = "?"
    return f"{scheme}://{authority}/{mark}{query}", database


def write_config_file(directory: Path, uri: str) -> Path:
    """A 0600 YAML file holding the URI; JSON string syntax is valid YAML, so quoting is exact."""
    path = directory / "mongodump.yml"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(f"uri: {json.dumps(uri)}\n")
    return path


class MongoDriver(BaseDriver):
    source_type = "mongo"
    _is_replica_set: bool | None = None
    _server_version: str | None = None

    def sensitive_values(self) -> list[str]:
        values = super().sensitive_values()
        if uri := self._secrets.get("uri"):
            values += uri_secrets(uri)
        return values

    def _client(self) -> MongoClient[dict[str, Any]]:
        config = _CONFIG.validate_python(self._config)
        common: dict[str, Any] = {
            "serverSelectionTimeoutMS": _TIMEOUT_MS,
            "connectTimeoutMS": _TIMEOUT_MS,
            "socketTimeoutMS": _TIMEOUT_MS,
            "appname": "dbbackup",
        }
        if isinstance(config, MongoUriConfig):
            return MongoClient(self._secrets["uri"], **common)
        options: dict[str, Any] = {"host": config.host, "port": config.port, "tls": config.tls}
        if config.replica_set:
            options["replicaSet"] = config.replica_set
        if config.username:
            options.update(
                username=config.username,
                password=self._secrets.get("password", ""),
                authSource=config.auth_database,
            )
        return MongoClient(**options, **common)

    def _probe(self) -> ConnectionTestResult:
        try:
            client = self._client()
        except (InvalidURI, ConfigurationError):
            return ConnectionTestResult(ok=False, message="Invalid connection URI or options.")
        try:
            version = str(client.server_info()["version"])
            self._server_version = version
            self._is_replica_set = "setName" in client.admin.command("hello")
            listing = client.admin.command("listDatabases", nameOnly=True, authorizedDatabases=True)
            names = [str(item["name"]) for item in listing["databases"]]
        finally:
            client.close()
        return ConnectionTestResult(
            ok=True,
            message=f"Connected to MongoDB {version}.",
            server_version=version,
            databases=sorted(n for n in names if n not in SYSTEM_DATABASES),
        )

    # ---------- dump ----------

    def _dump_mode(self) -> str:
        return str(_CONFIG.validate_python(self._config).dump_mode)

    def manifest_extra(self) -> dict[str, Any]:
        return {"dump_mode": self._dump_mode()}

    def select_databases(self, listed: list[str]) -> list[str]:
        config = _CONFIG.validate_python(self._config)
        if config.dump_mode == "full_oplog":
            return [FULL_INSTANCE]
        if config.databases == "all":
            return [n for n in listed if n not in EXCLUDED_FROM_ALL]
        return list(config.databases)

    async def tool_versions(self) -> dict[str, str]:
        env = clean_env({}, Path(tempfile.gettempdir()))
        output = await capture_output(["mongodump", "--version"], env)
        return {"mongodump": (output.strip().splitlines() or ["unknown"])[0]}

    def check_compatibility(self, server_version: str | None, tools: dict[str, str]) -> None:
        if self._dump_mode() == "full_oplog" and self._is_replica_set is False:
            raise DumpError(
                "Dump mode 'full instance with oplog' needs a replica set, but this server is "
                "standalone (or a mongos). Use 'per database' mode instead."
            )

    async def preflight(self, ctx: DumpContext) -> None:
        ctx.log.info(f"MongoDB server version: {self._server_version or 'unknown'}")
        if self._dump_mode() == "per_database" and self._is_replica_set:
            ctx.log.warning(
                "This server is a replica set. Per-database dumps are not point-in-time "
                "consistent across collections; use dump mode 'full instance with oplog' for "
                "a consistent backup."
            )

    def _connection_uri(self) -> str:
        config = _CONFIG.validate_python(self._config)
        if isinstance(config, MongoUriConfig):
            return self._secrets["uri"]
        return build_uri(config, self._secrets.get("password", ""))

    async def dump_database(self, database: str, ctx: DumpContext) -> DumpedArtifact:
        full = self._dump_mode() == "full_oplog"
        if full and database != FULL_INSTANCE:
            raise DumpError("Full-instance mode dumps the whole server, not a single database.")
        uri, uri_database = strip_uri_database(self._connection_uri())
        if uri_database:
            ctx.log.info(
                "The connection URI names a database; it is used only as the authentication "
                "database, not to choose what to dump."
            )
        final = work_file(ctx, safe_component(database), "archive.gz")
        try:
            directory = Path(tempfile.mkdtemp(prefix=".mongo-", dir=ctx.work_dir))
        except OSError as exc:
            raise DumpError(f"Cannot prepare the dump: {exc.strerror}.") from None
        try:
            try:
                config_file = write_config_file(directory, uri)
            except OSError as exc:
                raise DumpError(f"Cannot prepare the dump: {exc.strerror}.") from None
            argv = [
                "mongodump",
                f"--config={config_file}",
                f"--archive={final}.partial",
                "--gzip",
                "--oplog" if full else f"--db={database}",
            ]
            size, digest = await run_dump(
                tool="mongodump",
                argv=argv,
                env=clean_env({}, ctx.work_dir),
                ctx=ctx,
                secrets=self.sensitive_values(),
                final_path=final,
                stream_stdout=False,
                noise=_PROGRESS_NOISE,
            )
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        return DumpedArtifact(
            database=database,
            path=final,
            extension="archive.gz",
            size=size,
            sha256=digest,
            compressed=True,
            compression="gzip",
        )
