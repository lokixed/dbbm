# Operations guide

For people running DBBM. Design and internals are in [DESIGN.md](DESIGN.md). Threat model:
[SECURITY.md](SECURITY.md).

## Install and upgrade

First install: follow the Quick start in the [README](../README.md).

Upgrade:

```bash
# 1. Copy the tool's own data first (see "Back up the tool itself").
docker compose exec dbbm python -m app.cli backup-db
# 2. Pull or build the new version and restart.
docker compose pull   # or: docker compose build
docker compose up -d
# 3. Check the version and the scheduler.
curl -s http://127.0.0.1:8080/healthz
```

Database migrations run automatically at start. **Downgrade:** migrations only move forward. To go
back, stop the container, restore the `app.db` copy made in step 1, and start the older image.

Versions follow [semantic versioning](https://semver.org). Changes are listed in
[CHANGELOG.md](../CHANGELOG.md). The version is shown on every page of the web UI (the sidebar footer, and the sign-in, setup and Docs pages) and in `/healthz`.

## Orchestrators (compose, Portainer)

- Health check: `GET /healthz` returns `200` when the database is reachable and this instance owns
  the scheduler, `503` otherwise. The compose file already uses it (`start_period: 15s`).
- Start up: migrations, then recovery of runs left active by a crash (marked `interrupted`), then
  the scheduler. Allow up to 15 seconds before the first health result.
- Stop: send `SIGTERM` and allow `stop_grace_period: 30s`. Running backups are marked
  `interrupted`, child processes are stopped and temporary files removed.
- Run **one** container per `./data`. A second one reports `not_owner` and is unhealthy.

## HTTPS behind a reverse proxy

Keep the published port on `127.0.0.1` and put a proxy in front. Set `COOKIE_SECURE=true` and the
**Public base URL** in Settings. The app sends HSTS when `COOKIE_SECURE=true`.

Caddy:

```
backup.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Traefik (labels on the `dbbm` service, with Traefik on the same Docker network):

```yaml
labels:
  - traefik.enable=true
  - traefik.http.routers.dbbm.rule=Host(`backup.example.com`)
  - traefik.http.routers.dbbm.entrypoints=websecure
  - traefik.http.routers.dbbm.tls.certresolver=letsencrypt
  - traefik.http.services.dbbm.loadbalancer.server.port=8080
```

Large downloads and restores do not hold a request open: downloads use a one-use link and restores
are background runs. Do not set short proxy timeouts on `/api/backups/download/`.

## Back up the tool itself

Everything the tool knows is in `/data` (`./data`): the SQLite database `app.db`, run logs and the
scheduler lock. The **master key** (`app_key.txt`) is stored outside it on purpose.

Losing `app.db` means re-creating sources, destinations, jobs and notification channels. Losing the
master key means re-entering every stored credential. Back up both, in **different places**.

```bash
# A consistent copy of the database; safe while the app runs.
docker compose exec dbbm python -m app.cli backup-db --out /data/app.db.copy
# A list of sources, destinations, jobs and channels without any secret, to rebuild from.
docker compose exec dbbm python -m app.cli export-config --out /data/config-export.json
```

Do not copy `app.db` with `cp` while the app runs: the write-ahead log (`app.db-wal`) holds recent
changes. Use the command above, or stop the container first.

Back up `app_key.txt` and the age private keys (if you use encryption) separately from the server.
The config export contains hostnames and usernames: treat it as confidential.

### Disaster recovery drill

Practise this at least twice a year, on a scratch machine:

1. Install Docker and the compose file on a new server.
2. Restore `app_key.txt` and the `data/` directory (the `app.db` copy, renamed to `app.db`).
3. `docker compose up -d`. Open the UI and sign in.
4. Check `/healthz` shows the scheduler `running`. Open **Destinations** and run **Test connection** on each
   one. Google Drive tokens may need re-authorizing.
5. Open **Backups**, pick a recent backup and restore it into a scratch database (README, Restoring).
6. Without the config export or `app.db`, rebuild sources, destinations and jobs by hand from the
   export; credentials are entered again.

Record the date and what you had to fix.

## Rotate the master key

Rotation re-encrypts every stored secret (source, destination and channel credentials, heartbeat
URLs). Sessions are signed out because they are also derived from the key. Backups encrypted with
age are not affected.

```bash
# 1. Stop the app so nothing writes during the change.
docker compose stop
# 2. Write the new key (32+ characters) next to the old one.
(umask 077; openssl rand -base64 32 > app_key.new)
# 3. Dry run: decrypts everything, changes nothing.
docker compose run --rm --no-deps -v "$PWD/app_key.new:/run/secrets/app_key_new:ro" dbbm \
  python -m app.cli rotate-key --new-key-file /run/secrets/app_key_new --dry-run
# 4. Real run. It first copies app.db to app.db.pre-rotate-<time> (still readable with the old key).
docker compose run --rm --no-deps -v "$PWD/app_key.new:/run/secrets/app_key_new:ro" dbbm \
  python -m app.cli rotate-key --new-key-file /run/secrets/app_key_new
# 5. Swap the key file and start.
mv app_key.txt app_key.old && mv app_key.new app_key.txt
docker compose up -d
```

The command is all or nothing: if any stored secret cannot be decrypted with the current key, nothing
changes. **Roll back:** stop the app, copy `data/app.db.pre-rotate-<time>` over `data/app.db`,
restore the old key file, start. Delete the old key and the pre-rotate copy once you are sure.

## Lost admin password

Sign in and use **Settings > Change password** while you can. If you cannot sign in, reset it from
the host (needs access to the container, which already means access to the data):

```bash
docker compose exec dbbm python -m app.cli reset-password admin
```

It asks for the new password twice, signs every session out and writes an audit entry.

## Monitoring and alerts

- Add a notification channel and select it on every job; use **Send test**.
- Give important jobs a heartbeat URL so a stopped service alerts you.
- The **Audit log** page shows sign-ins, changes, downloads and restores. Entries are kept for the
  period in Settings (default 365 days).
- `GET /healthz` for uptime monitors. It includes the version and the scheduler state.
- Watch the **Dashboard** for failed and overdue jobs and for low disk space.

## Retention and disk planning

Per destination, keep the number of runs you need to restore from (*keep last*) and a time limit
(*keep days*). Estimate disk as: average backup size x runs kept x copies. Leave headroom for one
extra dump and for temporary files in `/data/work` (about the size of the largest database).
The low-disk warning triggers below the threshold set in Settings.

Run a restore drill (README, Restoring) after any change to a source and at least once a quarter.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Container unhealthy, `not_owner` in `/healthz` | Another container uses the same `./data`. Stop it. |
| "Stored credentials cannot be decrypted" | The master key differs from the one used to save them. Restore the original key, or re-enter the credentials. |
| Source test fails with a TLS or auth error | Check the TLS mode and the user's privileges (README, Backup user privileges). |
| `pg_dump` older than the server | Rebuild the image with `--build-arg PG_MAJOR=<server major>` or newer. |
| Drive run fails with `auth_expired` | The OAuth consent screen is in *Testing* (tokens expire after 7 days) or access was revoked. Publish it, then re-authorize the destination. |
| Drive run fails with `quota_exceeded` | Daily upload quota or a full Drive. Retry later or free space. |
| Run is `interrupted` | The container stopped during the run. Check the schedule and the stop grace period. |
| Backups show "Missing" | The file was removed outside the app. **Rescan destination**, or delete the entry. |
| Restore refuses: checksum mismatch | The stored file is damaged. Restore from another copy. |
| Download link is "invalid or expired" | Links work once for 60 seconds. Click Download again. |
| Locked out after failed logins | Wait for the lockout (default 15 minutes) or restart the container. |
| `permission denied` on `./data` or `./backups` | Set `DBBM_UID` and `DBBM_GID` in `.env` to your user (README, Quick start). |
