# DBBM - Database Backup & Management

Self-hosted backups for **MySQL, PostgreSQL, MongoDB and Firestore** to a local volume and Google
Drive, with a web UI. One container, one `docker compose up`.

- Schedules, retention, notifications (Slack, email, webhook) and live run logs.
- Backups browser with checksums, one-use download links and restore.
- Optional `age` encryption of every backup, so a leaked Drive account or disk exposes nothing.
- Stored credentials are encrypted with a master key that never touches the database.

Source code, full documentation and issues: <https://github.com/lokixed/dbbm>

## Tags

| Tag | What it is |
|---|---|
| `X.Y.Z` (for example `1.0.0-rc.1`) | One release. Pin one of these in production |
| `X.Y` | The newest patch of that minor version |
| `latest` | The newest release |
| `edge` | The latest commit on `main` that passed CI. May change at any time |
| `sha-<commit>` | One exact commit |

The image is built for `linux/amd64` only. It is also published to `ghcr.io/lokixed/dbbm`, and
signed with cosign (keyless).

## Quick start

Create a `docker-compose.yml`:

```yaml
services:
  dbbm:
    image: lokixed/dbbm:1.0.0-rc.1
    restart: unless-stopped
    # Match your host user so the bind mounts are readable and writable.
    user: "1000:1000"
    ports:
      - "127.0.0.1:8080:8080"
    environment:
      APP_SECRET_KEY_FILE: /run/secrets/app_key
      TZ: UTC
    volumes:
      - ./data:/data
      - ./backups:/backups
    read_only: true
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    secrets:
      - app_key

secrets:
  app_key:
    file: ./app_key.txt
```

Then:

```bash
# The master key encrypts stored credentials. Keep a backup of it.
(umask 077; openssl rand -base64 32 > app_key.txt)
mkdir -p data backups
docker compose up -d
```

Open <http://127.0.0.1:8080>, create the admin account, then add a source, a destination and a
job. Health check: `curl http://127.0.0.1:8080/healthz`.

## Good to know

- The port is bound to `127.0.0.1` only. Put Caddy or Traefik in front for HTTPS and set
  `COOKIE_SECURE=true`.
- **Losing `app_key.txt` means re-entering every stored credential.**
- Run exactly one container per `./data` folder. The container owns the scheduler.
- `/data` holds the metadata database and run logs; `/backups` holds the backup files.
- Back up your own databases from the same Docker network, or use `host.docker.internal` for
  databases on the host.

## Environment variables

| Variable | Purpose |
|---|---|
| `APP_SECRET_KEY` / `APP_SECRET_KEY_FILE` | Master key for encrypting stored credentials |
| `DATA_DIR` | Default `/data` |
| `BACKUP_DIR` | Default `/backups` |
| `TZ` | Container timezone |
| `MAX_CONCURRENT_RUNS` | Backup runs at the same time. Default 2 |
| `COOKIE_SECURE` | Set `true` behind HTTPS |
| `PUBLIC_BASE_URL` | Base URL used in notification links |
| `LOG_LEVEL` | Default `info` |

## What is inside

`mysqldump` (MySQL 8.4 client), `pg_dump` 18, `mongodump` and `mongorestore`, `rclone` (Google
Drive) and `age` (encryption). Firestore uses Google's REST APIs and needs no extra tool.

## License

MIT.
