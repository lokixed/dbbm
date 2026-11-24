# syntax=docker/dockerfile:1

# ---- Stage 1: build the frontend ----
FROM node:22-slim AS frontend
WORKDIR /frontend
RUN corepack enable
# Manifests first so the dependency layer is cached until they change.
COPY frontend/package.json frontend/yarn.lock frontend/.yarnrc.yml ./
RUN yarn install --immutable
COPY frontend/ ./
RUN yarn build

# ---- Stage 2: age, built with a current Go toolchain ----
FROM golang:1.27.3-trixie AS age
ARG AGE_VERSION=v1.3.2
RUN GOBIN=/out CGO_ENABLED=0 go install -trimpath     filippo.io/age/cmd/age@${AGE_VERSION} filippo.io/age/cmd/age-keygen@${AGE_VERSION}

# ---- Stage 3: mongodump and mongorestore, built with patched dependencies ----
# MongoDB's prebuilt release binaries embed golang.org/x/net v0.61.0 (CVE-2026-78661). Build the
# same pinned release from source with the fixed x/net and a current Go toolchain.
FROM golang:1.27.3-trixie AS mongotools
ARG MONGO_TOOLS_VERSION=100.20.1
ARG XNET_VERSION=v0.62.0
RUN git clone --depth 1 --branch "${MONGO_TOOLS_VERSION}" https://github.com/mongodb/mongo-tools /src
WORKDIR /src
RUN go get "golang.org/x/net@${XNET_VERSION}" \
    && for tool in mongodump mongorestore; do \
        CGO_ENABLED=0 go build -mod=mod -trimpath \
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

# ---- MongoDB tools and Google Cloud CLI (milestone 4) ----
# mongodb-database-tools (mongodump, mongorestore) from MongoDB's official APT repo, and the core
# google-cloud-cli (gcloud; no extra components) from Google's. Both signing keys are pinned by
# fingerprint exactly like the keys above: a key set that lacks the pinned fingerprint fails the build.
# Adds about 1.1 GB, almost all of it gcloud.
ARG MONGO_SERIES=8.0
ARG MONGO_SIGNER_FPR=4B0752C1BCA238C0B4EE14DC41DE058A4E7DCA05
ARG GCLOUD_SIGNER_FPR=35BAA0B33E9EB396F59CA838C0BA5CE6DC6315A3
RUN set -eux; \
    export DEBIAN_FRONTEND=noninteractive; \
    apt-get update; \
    apt-get install -y --no-install-recommends gnupg; \
    . /etc/os-release; \
    trust_key() { \
        curl -fsSL "$1" -o /tmp/key.asc; \
        if ! gpg --show-keys --with-colons /tmp/key.asc | awk -F: -v want="$2" '$1 == "fpr" && $10 == want { ok = 1 } END { exit !ok }'; then \
            echo "Pinned signing key $2 not found in $1" >&2; exit 1; \
        fi; \
        if grep -q 'BEGIN PGP' /tmp/key.asc; then gpg --dearmor < /tmp/key.asc > "$3"; else cp /tmp/key.asc "$3"; fi; \
        rm -f /tmp/key.asc; \
    }; \
    trust_key "https://pgp.mongodb.com/server-${MONGO_SERIES}.asc" "$MONGO_SIGNER_FPR" /usr/share/keyrings/mongodb.gpg; \
    trust_key https://packages.cloud.google.com/apt/doc/apt-key.gpg "$GCLOUD_SIGNER_FPR" /usr/share/keyrings/gcloud.gpg; \
    echo "deb [signed-by=/usr/share/keyrings/mongodb.gpg] https://repo.mongodb.org/apt/debian ${VERSION_CODENAME}/mongodb-org/${MONGO_SERIES} main" > /etc/apt/sources.list.d/mongodb.list; \
    echo "deb [signed-by=/usr/share/keyrings/gcloud.gpg] https://packages.cloud.google.com/apt cloud-sdk main" > /etc/apt/sources.list.d/gcloud.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends mongodb-database-tools google-cloud-cli; \
    rm -f /usr/bin/bsondump /usr/bin/mongoexport /usr/bin/mongofiles /usr/bin/mongoimport \
        /usr/bin/mongostat /usr/bin/mongotop /usr/lib/google-cloud-sdk/bin/gcloud-crc32c; \
    apt-get purge -y --auto-remove gnupg; \
    rm -rf /var/lib/apt/lists/* /root/.gnupg /root/.config; \
    gcloud --version
# Only mongodump and mongorestore are used; they replace the packaged builds.
COPY --from=mongotools /out/ /usr/bin/
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

# Current pip and setuptools (the base image's copies bundle libraries with known vulnerabilities).
RUN pip install --root-user-action=ignore --upgrade pip setuptools

WORKDIR /app
COPY backend/ ./
# Install the dependencies only; the app itself runs from /app (it needs alembic/ beside it).
RUN pip install --root-user-action=ignore . \
    && pip uninstall -y dbbm \
    && rm -rf build ./*.egg-info \
    && python -c "from zoneinfo import ZoneInfo; ZoneInfo('Europe/Berlin'); ZoneInfo('Asia/Kolkata')"
ENV PYTHONPATH=/app
COPY --from=frontend /frontend/dist ./frontend/dist

USER dbbm
EXPOSE 8080
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
