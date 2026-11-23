BACKEND := backend
VENV := $(BACKEND)/.venv
BIN := $(VENV)/bin
STAMP := $(VENV)/.installed
UV := $(shell command -v uv 2>/dev/null)

DEV_DIR := $(CURDIR)/.dev
DEV_KEY ?= dev-only-master-key-change-me-0123456789

.PHONY: install-backend dev-backend dev-frontend test test-unit test-firestore-fake test-integration test-integration-docker lint build up down dev-dbs-up dev-dbs-down up-dev dev-echo-logs smoke-tz

install-backend: $(STAMP)

$(STAMP): $(BACKEND)/pyproject.toml
ifneq ($(UV),)
	$(UV) venv --python 3.12 --allow-existing $(VENV)
	$(UV) pip install --python $(BIN)/python -e "$(BACKEND)[dev]"
else
	python3 -m venv $(VENV)
	$(BIN)/pip install -e "$(BACKEND)[dev]"
endif
	touch $@

# API on :8080 with auto-reload; data lives in ./.dev (git-ignored).
dev-backend: $(STAMP)
	mkdir -p $(DEV_DIR)/data $(DEV_DIR)/backups
	cd $(BACKEND) && DATA_DIR=$(DEV_DIR)/data BACKUP_DIR=$(DEV_DIR)/backups \
		APP_SECRET_KEY=$(DEV_KEY) COOKIE_SECURE=false ENABLE_DOCS=true \
		.venv/bin/uvicorn app.main:create_app --factory --reload --port 8080

dev-frontend:
	cd frontend && yarn install && yarn dev

test: test-unit

test-unit: $(STAMP)
	cd $(BACKEND) && .venv/bin/pytest -m "not integration"

# Only the Firestore flow against a fake gcloud on PATH (no Google Cloud access); part of test-unit too.
test-firestore-fake: $(STAMP)
	cd $(BACKEND) && .venv/bin/pytest -m firestore_fake

# Real MySQL, PostgreSQL and MongoDB via Testcontainers; needs Docker. The MySQL/PostgreSQL backup
# tests also need mysqldump, mysql, pg_dump and pg_restore, and the MongoDB backup tests need
# mongodump and mongorestore, on PATH; they are skipped without them (see below).
test-integration: $(STAMP)
	cd $(BACKEND) && .venv/bin/pytest -m integration

# The same tests inside the application image, which ships the client tools. Needs the Docker socket.
test-integration-docker: build
	docker run --rm --user root --network host \
		-v /var/run/docker.sock:/var/run/docker.sock \
		-v $(CURDIR)/backend:/src:ro \
		--entrypoint sh dbbackup:local -c \
		'mkdir /tmp/backend && tar -C /src --exclude=.venv --exclude=__pycache__ -cf - . | tar -C /tmp/backend -xf - \
		&& cd /tmp/backend && pip install -q ".[dev]" && pytest -m integration'

# DEV ONLY sample databases on the "dbbackup-dev" network (see dev/README.md).
dev-dbs-up:
	docker compose -f dev/docker-compose.dev-dbs.yml up -d --wait

dev-dbs-down:
	docker compose -f dev/docker-compose.dev-dbs.yml down -v

lint: $(STAMP)
	cd $(BACKEND) && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy
	cd frontend && yarn lint && yarn typecheck

build:
	docker build -t dbbackup:local .

up:
	docker compose up --build -d

# The app attached to the dev network (databases, Mailpit, echo server); DEV ONLY.
up-dev:
	docker compose -f docker-compose.yml -f dev/docker-compose.attach-dev-dbs.yml up --build -d

# Requests received by the dev webhook/heartbeat echo server.
dev-echo-logs:
	docker compose -f dev/docker-compose.dev-dbs.yml logs --no-log-prefix dev-echo

# Time zone data inside the image (job schedules depend on it).
smoke-tz: build
	docker run --rm --entrypoint python dbbackup:local -c \
		"from zoneinfo import ZoneInfo; from datetime import datetime; print(datetime(2026, 3, 29, 12, tzinfo=ZoneInfo('Europe/Berlin')).isoformat())"

down:
	docker compose down
