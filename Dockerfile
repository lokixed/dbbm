# syntax=docker/dockerfile:1

# ---- Stage 1: build the frontend ----
# The build output is plain files, so it runs on the build machine and is not compiled per target.
FROM --platform=$BUILDPLATFORM node:22-slim AS frontend
WORKDIR /frontend
RUN corepack enable
# Manifests first so the dependency layer is cached until they change.
COPY frontend/package.json frontend/yarn.lock frontend/.yarnrc.yml ./
RUN yarn install --immutable
COPY frontend/ ./
RUN yarn build

# ---- Stage 2: age, built with a current Go toolchain ----
# Runs on the build machine's own architecture and cross-compiles for the target, so the arm64 image
# does not compile under emulation.
FROM --platform=$BUILDPLATFORM golang:1.27.3-trixie AS age
ARG TARGETOS
ARG TARGETARCH
ARG AGE_VERSION=v1.3.2
WORKDIR /src
RUN go mod init dbbm-age \
    && go get "filippo.io/age/cmd/age@${AGE_VERSION}" "filippo.io/age/cmd/age-keygen@${AGE_VERSION}" \
    && for tool in age age-keygen; do \
        CGO_ENABLED=0 GOOS="${TARGETOS}" GOARCH="${TARGETARCH}" go build -trimpath \
            -o "/out/${tool}" "filippo.io/age/cmd/${tool}" || exit 1; \
    done

# ---- Stage 3: mongodump and mongorestore, built with patched dependencies ----
# MongoDB's prebuilt release binaries embed golang.org/x/net v0.61.0 (CVE-2026-78661). Build the
# same pinned release (the commit is checked) from source with the fixed x/net and a current Go
# toolchain.
FROM --platform=$BUILDPLATFORM golang:1.27.3-trixie AS mongotools
ARG TARGETOS
ARG TARGETARCH
ARG MONGO_TOOLS_VERSION=100.20.1
ARG XNET_VERSION=v0.62.0
ARG MONGO_TOOLS_COMMIT=99fd6bc7140e31e0287a9b4ef7e03194d834c7f0
RUN git clone --depth 1 --branch "${MONGO_TOOLS_VERSION}" https://github.com/mongodb/mongo-tools /src && test "$(git -C /src rev-parse HEAD)" = "${MONGO_TOOLS_COMMIT}"
WORKDIR /src
RUN go get "golang.org/x/net@${XNET_VERSION}" \
    && for tool in mongodump mongorestore; do \
        CGO_ENABLED=0 GOOS="${TARGETOS}" GOARCH="${TARGETARCH}" go build -mod=mod -trimpath \
            -ldflags "-s -w -X main.VersionStr=${MONGO_TOOLS_VERSION} -X main.GitCommit=source" \
            -o "/out/${tool}" "./${tool}/main" || exit 1; \
    done

# ---- Stage 4: runtime ----
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data \
    BACKUP_DIR=/backups \
    FRONTEND_DIST=/app/frontend/dist

# curl is used by the compose healthcheck and to download the repository signing keys below.
# tzdata provides the IANA zones that job schedules use (Python's zoneinfo reads them).
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends curl tzdata \
    && rm -rf /var/lib/apt/lists/*

# ---- Database client tools (milestone 3) ----
# MySQL: the official MySQL client (NOT MariaDB's default-mysql-client), from the MySQL APT repo.
# PostgreSQL: pg_dump / pg_restore from PGDG. pg_dump must be at least as new as every server it
# backs up (the runner refuses to dump otherwise), so PG_MAJOR must be >= your newest server.
#
# Each repository's signing key is downloaded over HTTPS and its fingerprint is compared with the
# value pinned here before it is trusted; a different key fails the build.
#
# Adding another PostgreSQL major later (e.g. to match an older server for a restore):
#   apt-get install -y postgresql-client-<N>   (adds /usr/lib/postgresql/<N>/bin/pg_dump)
# The `pg_dump` on PATH is a wrapper that picks the newest installed major.
# To move to a newer major, bump PG_MAJOR (docker build --build-arg PG_MAJOR=19 .).
ARG PG_MAJOR=18
ARG MYSQL_SERIES=8.4
ARG MYSQL_SIGNER_FPR=BCA43417C3B485DD128EC6D4B7B3B788A8D3785C
ARG PGDG_SIGNER_FPR=B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8
RUN set -eux; \
    export DEBIAN_FRONTEND=noninteractive; \
    apt-get update; \
    apt-get install -y --no-install-recommends gnupg; \
    . /etc/os-release; \
    trust_key() { \
        curl -fsSL "$1" -o /tmp/key.asc; \
        found="$(gpg --show-keys --with-colons /tmp/key.asc | awk -F: '$1 == "fpr" { print $10; exit }')"; \
        if [ "$found" != "$2" ]; then echo "Unexpected signing key $found for $1" >&2; exit 1; fi; \
        gpg --dearmor < /tmp/key.asc > "$3"; \
        rm -f /tmp/key.asc; \
    }; \
    trust_key https://repo.mysql.com/RPM-GPG-KEY-mysql-2025 "$MYSQL_SIGNER_FPR" /usr/share/keyrings/mysql.gpg; \
    trust_key https://www.postgresql.org/media/keys/ACCC4CF8.asc "$PGDG_SIGNER_FPR" /usr/share/keyrings/pgdg.gpg; \
    echo "deb [signed-by=/usr/share/keyrings/mysql.gpg] https://repo.mysql.com/apt/debian ${VERSION_CODENAME} mysql-${MYSQL_SERIES}-lts" > /etc/apt/sources.list.d/mysql.list; \
    echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        "mysql-community-client=${MYSQL_SERIES}.*" \
        "postgresql-client-${PG_MAJOR}"; \
    apt-get purge -y --auto-remove gnupg; \
    rm -rf /var/lib/apt/lists/* /root/.gnupg; \
    mysqldump --version; mysql --version; pg_dump --version; pg_restore --version

# ---- MongoDB tools (milestone 4) ----
# mongodump and mongorestore are built from source in the mongotools stage (no apt repository).
# Firestore needs no command-line tool: the driver calls Google's REST APIs directly.
COPY --from=mongotools /out/ /usr/local/bin/
RUN mongodump --version && mongorestore --version

# ---- rclone (milestone 5) ----
# Pinned release; the script checks the zip against a SHA-256 pinned in it and against SHA256SUMS.
COPY scripts/install-rclone.sh /tmp/install-rclone.sh
RUN sh /tmp/install-rclone.sh /usr/local/bin && rm -f /tmp/install-rclone.sh

# ---- age (milestone 8) ----
# Built from the pinned source release with a patched Go toolchain: the prebuilt release binaries
# embed an older Go standard library with known vulnerabilities. The Go module checksum database
# verifies the source.
COPY --from=age /out/ /usr/local/bin/
RUN age --version && age-keygen --version

RUN useradd --system --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin dbbm \
    && mkdir -p /data /backups \
    && chown dbbm:dbbm /data /backups

WORKDIR /app
COPY backend/ ./
# Install the dependencies only; the app itself runs from /app (it needs alembic/ beside it).
RUN pip install --root-user-action=ignore . \
    && pip uninstall -y dbbm \
    && rm -rf build ./*.egg-info \
    && python -c "from zoneinfo import ZoneInfo; ZoneInfo('Europe/Berlin'); ZoneInfo('Asia/Kolkata')"
# pip is not needed at run time and bundles libraries (msgpack, urllib3, pkg_resources) with known
# vulnerabilities, so it is removed together with the wheels ensurepip carries. setuptools stays
# current. To install packages in a running container, bootstrap pip again with get-pip.py.
RUN pip install --root-user-action=ignore --upgrade setuptools     && pip uninstall -y pip     && rm -rf /usr/local/lib/python3.12/ensurepip/_bundled
ENV PYTHONPATH=/app
COPY --from=frontend /frontend/dist ./frontend/dist

USER dbbm
EXPOSE 8080
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
