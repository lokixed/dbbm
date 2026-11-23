# Development databases

Sample MySQL 8, PostgreSQL 16 and MongoDB 7 servers for trying out sources.

> **DEV ONLY.** Every credential here is public and committed to the repo. Never reuse them for real data, and never expose these containers beyond localhost.

```bash
make dev-dbs-up     # start, wait until healthy
make dev-dbs-down   # stop and delete the data (volumes)
```

Each server has a seeded `shop` database (customers and orders) and a dedicated read-only `backup` user.

| Server | Host (from the app container) | Port | Username | Password | Host port on your machine |
|---|---|---|---|---|---|
| MySQL 8 | `dev-mysql` | 3306 | `backup` | `dev-only-backup-pw` | 3307 |
| PostgreSQL 16 | `dev-postgres` | 5432 | `backup` | `dev-only-backup-pw` | 5433 |
| MongoDB 7 | `dev-mongo` | 27017 | `backup` | `dev-only-backup-pw` | 27018 |

- PostgreSQL: leave the connection database as `postgres`.
- MongoDB: auth database `admin`.
- Admin accounts (`root`, password `dev-only-root-pw`) exist for debugging; don't enter them in the UI.

## Attach the app container

The servers live on the Docker network `dbbm-dev`. Start the app attached to it:

```bash
docker compose -f docker-compose.yml -f dev/docker-compose.attach-dev-dbs.yml up --build -d
```

Or connect an already running container: `docker network connect dbbm-dev dbbm-dbbm-1`.

In the UI use the hostnames in the table (`dev-mysql`, ...). If you run the backend on the host instead (`make dev-backend`), use `127.0.0.1` and the host ports.

## First test run

With the app attached to the dev network (above) and `make dev-dbs-up` running, create these in the UI. MySQL, PostgreSQL and MongoDB can all be tried with the dev servers; Firestore needs a real Google Cloud project (last section).

**Sources** (Add source, then Save and test)

| Field | MySQL | PostgreSQL |
|---|---|---|
| Name | `dev-mysql` | `dev-postgres` |
| Host / Port | `dev-mysql` / `3306` | `dev-postgres` / `5432` |
| Username / Password | `backup` / `dev-only-backup-pw` | `backup` / `dev-only-backup-pw` |
| Connection database | n/a | `postgres` |
| Databases | All databases | All databases |

**Destination** (Add destination)

- Name `local-dev`, path `dev` (stored under the backup directory, `./backups/dev` with the default compose file).

**Job** (Jobs, Add job)

- Name `dev-nightly`, source `dev-mysql`, destination `local-dev`, compress on, timeout `3600`. Create a second job `dev-pg` with source `dev-postgres` the same way. Leave the schedule empty; schedules are not active yet.

Click **Run now** on the job. You land on the run page and the log streams live. When it finishes you should see:

- `success`, with one artifact per database (the dev servers expose `shop` only), listed with size and SHA-256.
- Files such as `backups/dev/dev-nightly/2026/11/dev-nightly_shop_20261118T120000Z.sql.gz` (PostgreSQL: `.dump`), each with a `.manifest.json` next to it, mode `0600`.
- `sha256sum` of the file equals the SHA-256 shown in the UI.

Run it again after a second: new files appear and the first ones are untouched.

Restoring by hand (the application has no restore feature yet). `D` below is the dev compose file:

```bash
D="docker compose -f dev/docker-compose.dev-dbs.yml"

# MySQL
$D exec -T dev-mysql mysql -uroot -pdev-only-root-pw -e 'CREATE DATABASE scratch'
gunzip -c backups/dev/dev-nightly/*/*/dev-nightly_shop_*.sql.gz | $D exec -T dev-mysql mysql -uroot -pdev-only-root-pw scratch
$D exec -T dev-mysql mysql -uroot -pdev-only-root-pw -e 'SELECT COUNT(*) FROM scratch.customers'

# PostgreSQL: restore with the app image's pg_restore (a dump from a newer pg_dump needs a
# pg_restore at least as new; the dev server is 16, the image has 18).
$D exec -T dev-postgres createdb -U postgres scratch
docker run --rm --network dbbm-dev -e PGPASSWORD=dev-only-root-pw -v "$PWD/backups:/backups:ro" --entrypoint sh dbbm:local \
  -c 'pg_restore --no-owner -h dev-postgres -U postgres -d scratch /backups/dev/dev-pg/*/*/dev-pg_shop_*.dump'
$D exec -T dev-postgres psql -U postgres -d scratch -c 'SELECT COUNT(*) FROM customers'
```

Restoring a PostgreSQL 18 dump into the 16 server prints one harmless error about `transaction_timeout` (an option 16 does not know); the data is restored.

Clean up with `DROP DATABASE scratch`.

## MongoDB source and job

Using the same `local-dev` destination:

1. **Sources**, Add source, type MongoDB, then fill in:

   | Field | Value |
   |---|---|
   | Name | `dev-mongo` |
   | Host / Port | `dev-mongo` / `27017` |
   | Username / Password | `backup` / `dev-only-backup-pw` |
   | Auth database | `admin` |
   | Databases | All databases |
   | Dump mode | Per database |

   Click **Save and test**; it should list `shop`.
2. **Jobs**, Add job: name `dev-mongo-nightly`, source `dev-mongo`, destination `local-dev`, timeout `3600`. Click **Run now**.
3. Expect `success` and one `.archive.gz` per database (`admin`, `config` and `local` are skipped), each with a `.manifest.json`, for example `backups/dev/dev-mongo-nightly/2026/11/dev-mongo-nightly_shop_20261122T120000Z.archive.gz`. The manifest records the `mongodump` version and `dump_mode`.

To get more than one archive, add a database first:

```bash
docker exec dbbm-dev-dbs-dev-mongo-1 mongosh -u root -p dev-only-root-pw --authenticationDatabase admin \
  --eval 'db.getSiblingDB("inventory").items.insertMany([{sku:"a"},{sku:"b"}])'
```

**Dump mode "Full instance with oplog"** needs a replica set. The dev MongoDB is a standalone server, so a run in that mode fails during preflight with "needs a replica set"; that is the expected result here. The replica-set case is covered by the integration test (`make test-integration-docker`).

**Wrong password**: change the source password and run again; the run fails with `Authentication failed` and leaves no artifact.

Restore by hand into a scratch server and compare counts (run as your own user, the archives are `0600`):

```bash
docker run -d --name scratch-mongo --network dbbm-dev mongo:7
for f in backups/dev/dev-mongo-nightly/*/*/*.archive.gz; do
  docker run --rm --user "$(id -u)" --network dbbm-dev -v "$PWD/backups:/backups:ro" --entrypoint mongorestore dbbm:local \
    --host scratch-mongo --archive="/$f" --gzip --drop
done
docker exec scratch-mongo mongosh --quiet --eval 'db.getSiblingDB("shop").customers.countDocuments()'   # 3
docker rm -f scratch-mongo
```

## Firestore real-project test

Firestore has no local stand-in for this flow (the emulator has no managed export), so it is tested against a real project. The unit tests cover the logic with a fake `gcloud`; CI never contacts Google Cloud.

Prerequisites in the Google Cloud project:

- Billing is enabled (exports are a paid feature, and read charges apply).
- A Cloud Storage staging bucket in the same location as the Firestore database.
- A service account with `roles/datastore.importExportAdmin`, plus read and write on the staging bucket. For the app to delete the staging export afterwards it also needs `storage.objects.delete` on that bucket (for example `roles/storage.objectAdmin`).
- Recommended: a bucket lifecycle rule (delete after 1 to 7 days), which also cleans up exports from failed or cancelled runs.

Steps:

1. **Sources**, Add source, type Firestore: project ID, database ID (`(default)` unless you use a named database), staging bucket `gs://<bucket>`, optional collection IDs, and paste the service-account JSON. Leave **Delete staging export after download** on. **Save and test** checks authentication, the database and the bucket.
2. **Jobs**, Add job with that source and a local destination. Use a generous timeout (it applies to each step); small databases finish in a few minutes.
3. **Run now**. The run page shows the sub-steps export, poll, download, pack and cleanup-staging with timings. Expect a `.tar.gz` under `backups/` that contains an `*.overall_export_metadata` file (`tar tzf <file> | grep overall_export_metadata`), and the staging prefix gone from the bucket.
4. Warning case: remove delete permission from the service account and run again. The run still succeeds, with a warning that the staging export could not be deleted.
5. Cancel case: start a run on a larger database and click **Cancel run** during the export. Polling stops, the app tries to cancel the export operation (see the log), and the work directory is removed.


## Google Drive test run

Needs a Google account; nothing here runs in CI (CI uses the real rclone against a local folder and a fake rclone for Drive errors).

1. Create a Desktop OAuth client and publish the consent screen (see the README, "Google Drive destination").
2. Install rclone on your own machine (any recent version), open **Destinations > Add destination > Google Drive**, set the folder to `dbbm-dev`, enter the client ID and secret, and run the command shown in the form. Paste the output into **Token**.
3. **Save and test**: all three checks pass and free space is shown. In Drive, the folder `dbbm-dev` exists and is empty (the probe file was deleted, not trashed).
4. Create a job with the dev MySQL source and both the local destination and the Drive destination, then **Run now**.

### Manual end-to-end checklist

- [ ] Run succeeds; the run page shows both destinations `ok` with upload and verify timings; each Drive artifact has an MD5.
- [ ] Drive contains `dbbm-dev/<job>/<YYYY>/<MM>/<file>` and its `.manifest.json`, named exactly like the local copy.
- [ ] Run the job again: new files with a new timestamp; nothing overwritten, no duplicate names.
- [ ] Network blocked (for example `docker network disconnect bridge dbbm-dbbm-1` while a run uploads, or block `www.googleapis.com`): the run ends `partial`, the local copy exists, Drive shows `network`.
- [ ] Reconnect the network, click **Retry failed destinations**: the retry run uploads from the local copy and verifies; the original run becomes `success` and links to the retry.
- [ ] Revoke access at <https://myaccount.google.com/permissions> and run again: `partial`, Drive shows `auth_expired`, the destination shows **Reconnect needed** and the banner appears. The next run skips Drive in preflight without calling it.
- [ ] **Reconnect**, paste a new token, retry the partial run: success; the banner disappears.
- [ ] Token refresh: after a run more than an hour after the token was created, the stored token's expiry is newer (the run log says "Google Drive token refreshed"). Start two jobs using the same Drive destination at once: both succeed and the destination still tests OK afterwards.
- [ ] No secrets anywhere: `grep -rE "ya29\.|1//0|GOCSPX" data/logs/` finds nothing; the API (`/api/destinations`, `/api/runs/<id>`) shows only `secrets_set` flags; `docker exec dbbm-dbbm-1 ls /tmp` shows no `dbbm-rclone-*` directories after runs; `ps` during an upload shows no token in rclone's arguments.

## Notifications, heartbeat and scheduling (milestone 6)

`make dev-dbs-up` also starts **Mailpit** (SMTP on `dev-mailpit:1025`, web UI at
http://localhost:8025) and an **HTTP echo server** (`dev-echo:8080`, it logs every request).
Both have private Docker addresses, which the app refuses by default, so
`dev/docker-compose.attach-dev-dbs.yml` sets `ALLOW_PRIVATE_NOTIFICATION_TARGETS=true`
(**development only**). Start the app with `make up-dev`.

Channels to add under **Notifications**:

| Type | Field | Value |
|---|---|---|
| Email | SMTP host / Port | `dev-mailpit` / `1025` |
| | Security | `None` (the UI warns; fine for Mailpit) |
| | Username / Password | leave empty |
| | From / To | `dbbm@example.test` / `ops@example.test` |
| Webhook | Webhook URL | `http://dev-echo:8080/hook` |
| | HMAC secret | `dev-only-hmac-secret-123` |
| | Extra header | `X-Token` = `dev-only-header` |
| Slack | Webhook URL | your real `https://hooks.slack.com/services/...` URL |

Click **Send test** on each: the email appears in Mailpit, and `make dev-echo-logs` shows the
webhook with its `x-signature-256` header. Verify the signature of a logged body (copy the `body`
string into `body.json` exactly, without a trailing newline):

```bash
python3 -c 'import hmac,hashlib,sys; print("sha256="+hmac.new(b"dev-only-hmac-secret-123", open("body.json","rb").read(), hashlib.sha256).hexdigest())'
```

Checklist:

1. **Failure and recovery.** On a MySQL job select both channels (Notify: On failure, recovery
   on). Change the source password to something wrong and **Run now**: Mailpit and the echo log get
   one `run_finished` / `failed` message each, with no password or connection string. Fix the
   password and run again: exactly one `recovery` message.
2. **Heartbeat.** Set the job's heartbeat URL to `http://dev-echo:8080/ping/<any-uuid>`. A
   successful run logs `/ping/<uuid>/start` then `/ping/<uuid>`; a failed one `/start` then
   `/fail`. Stop the echo container and run again: the run still succeeds, the app logs a warning
   without the URL.
3. **Schedule.** Set the schedule to a minute two minutes ahead (e.g. `17 * * * *`) in your time
   zone; the dialog's preview shows it. The run appears with trigger `schedule` and *Scheduled for*.
4. **Catch-up.** Set a schedule two minutes ahead, `docker compose stop dbbm`, wait until the
   minute has passed, `docker compose start dbbm`: one catch-up run starts ("missed its run ...
   starting one catch-up run" in the log). With Settings > Missed-run grace at 60 and a restart
   more than 60 s after the due time: no run, and the log says "outside the grace window".
5. **Single owner.** Start a second container on the same `./data` (e.g. `docker run` with the
   same volumes on another port): its `/healthz` returns 503 with `"state": "not_owner"`.
6. **Retention.** Run a job five times, set **Keep last** to 3 on its destination, **Preview**
   (2 runs to delete), save (confirm), **Run retention now**: three runs keep their files, files you
   put in the folder yourself are untouched.
7. **Blocked targets.** A webhook URL `http://169.254.169.254/latest/meta-data` or
   `http://127.0.0.1/x` is rejected when saving, even with the dev override.

