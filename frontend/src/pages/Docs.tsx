import { Link } from "react-router-dom";
import AppBar from "xedonium/AppBar";
import ButtonLink from "xedonium/ButtonLink";
import Chip from "xedonium/Chip";
import Container from "xedonium/Container";
import Flex from "xedonium/Flex";
import Markdown from "xedonium/Markdown";
import Sidebar from "xedonium/Sidebar";
import type { SidebarItem } from "xedonium/Sidebar";
import { useMe } from "@/api/auth";
import { AppVersion } from "@/components/AppVersion";
import { Logo } from "@/components/Logo";

// Public page: no sign-in required, and only xedonium components. The text mirrors README.md,
// docs/OPERATIONS.md and docs/SECURITY.md; keep them in sync when those change.

const SECTIONS: { id: string; label: string; body: string }[] = [
  {
    id: "features",
    label: "Features",
    body: `## Features

- **Four database engines.** MySQL, PostgreSQL, MongoDB and Firebase Firestore, with per-database selection and connection tests.
- **Two places for every backup.** A local volume and Google Drive (through rclone). Each artifact is verified with SHA-256 and written next to a \`.manifest.json\`.
- **Schedules and retention.** Cron-style schedules with catch-up of missed runs, plus keep-last and keep-days retention per destination.
- **Restore wizard.** Restore a copy into a registered source, choose a new or existing database, restore only some tables or collections, and watch a live log.
- **Optional age encryption.** Encrypt every backup before it is stored or uploaded. The server only ever holds public keys.
- **Notifications and audit log.** Alert channels, heartbeat URLs for stopped services, and an audit log of sign-ins, changes, downloads and restores.

### Backup formats

| Engine | Artifact |
| --- | --- |
| MySQL | \`.sql.gz\` |
| PostgreSQL | \`.dump\` (always compressed) |
| MongoDB | \`.archive.gz\` |
| Firestore | \`.tar.gz\` |

Files are mode 0600, never overwritten, and stored under \`<destination>/<job>/<YYYY>/<MM>/\`. A run is \`success\` when every database reached every destination and was verified, \`partial\` when some did, and \`failed\` when none did. A second run of the same job cannot start while one is active.`,
  },
  {
    id: "install",
    label: "Install",
    body: `## Install

\`\`\`bash
# 1. Create the master key (encrypts stored credentials; keep a backup of it).
(umask 077; openssl rand -base64 32 > app_key.txt)

# 2. Data directories, and run the container as your user so it can use them.
mkdir -p data backups
printf 'DBBM_UID=%s\\nDBBM_GID=%s\\n' "$(id -u)" "$(id -g)" > .env

# 3. Build and start.
docker compose up --build -d
\`\`\`

Open http://127.0.0.1:8080, create the admin account, then sign in.

- The port is bound to \`127.0.0.1\` only. Put Caddy or Traefik in front for HTTPS and set \`COOKIE_SECURE=true\` in \`.env\`.
- The master key comes from \`APP_SECRET_KEY\` or \`APP_SECRET_KEY_FILE\`. Losing it means re-entering every stored credential.
- Health check: \`curl http://127.0.0.1:8080/healthz\`.
- Optional limits: \`DBBM_MEM_LIMIT\` (default 2g) and \`DBBM_CPUS\` (default 2).
- No databases to try? \`make dev-dbs-up\` starts sample MySQL, PostgreSQL and MongoDB servers.`,
  },
  {
    id: "first-backup",
    label: "First backup",
    body: `## First backup

1. Open **Sources**, click **Add source**, pick a type, fill in the connection details, click **Test connection** to check them, then **Save**. The result shows the server version and the databases the user can see.
2. Open **Destinations** and add a local path such as \`daily\` (relative to \`/backups\`) or a Google Drive folder. Paths that escape the backup directory are rejected.
3. Open **Jobs**, click **Add job**, choose the source and one or more destinations, and save.
4. Click **Run now**. The run page streams the log live; **Cancel run** stops the dump and removes its temporary files.

Passwords, connection URIs and service-account keys are write-only: once saved they show as "Saved" and are never returned by the API.

### Backup user privileges

- **MySQL:** \`SELECT\`, \`SHOW VIEW\`, \`TRIGGER\`, \`EVENT\` on the databases, and the global \`SHOW_ROUTINE\` privilege (8.0.20+) if they contain stored routines.
- **PostgreSQL:** \`CONNECT\` and \`SELECT\` on everything, for example membership in \`pg_read_all_data\`.`,
  },
  {
    id: "restore",
    label: "Restore",
    body: `## Restore

Open **Backups**, click the restore button on a copy and follow the wizard: choose the copy, the target source and database name, the options, then type the database name to confirm. The default is a new database name; restoring into the original database needs an extra confirmation. A restore is a run like a backup (live log, Cancel run) and it checks the file's SHA-256 before it changes anything.

- MongoDB and PostgreSQL per-database backups can restore only some collections or tables.
- Downloads use a one-use link that expires after 60 seconds.
- Firestore imports go into the target source's own database, overwrite documents with the same ID and never delete documents.
- The PostgreSQL roles file is not restored: download it and apply it with \`psql\`.

### Restore user privileges

Use a separate restore user with write access for the target source, not the read-only backup user.

- **MySQL:** \`ALL\` on the target databases plus \`CREATE\` and \`DROP\` on \`*.*\`.
- **PostgreSQL:** \`CREATEDB\`, or an existing, owned target database.
- **MongoDB:** the \`restore\` role on the target databases.
- **Firestore:** \`roles/datastore.importExportAdmin\` and permission to create and delete objects in the staging bucket.`,
  },
  {
    id: "encryption",
    label: "Encryption",
    body: `## Encryption

Jobs can encrypt every backup with age before it is checksummed, stored or uploaded. The server only ever holds **public keys**. Losing the private key means the backups are unrecoverable, even for the administrator.

1. Generate a key pair on your own machine: \`age-keygen -o backup-key.txt\`. Store the private key offline.
2. Make a second key pair as a recovery key and keep it somewhere else.
3. Open **Settings > Backup encryption keys** and add the public keys. A private key is refused.
4. Edit the job, turn on **Encrypt backups with age** and choose the keys.

Encrypted artifacts end in \`.age\`. If encryption is on and a key is missing or encryption fails, the run fails and no unencrypted backup is stored. To read a backup yourself:

\`\`\`bash
age --decrypt -i backup-key.txt -o backup.sql.gz backup.sql.gz.age
\`\`\`

Rotating keys: add the new public key, add it to the jobs, then remove the old one. Earlier backups stay readable only with the key they were encrypted to. Do a recovery drill at least once per quarter: decrypt a backup from your storage and restore it into a scratch database.`,
  },
  {
    id: "security",
    label: "Security",
    body: `## Security

### Architecture

- Backend: Python 3.12, FastAPI, SQLModel, Alembic, SQLite (WAL) and APScheduler.
- Frontend: React, Vite, TypeScript, TanStack Query and React Router.
- A single Docker image and a single uvicorn worker. Run one container per \`./data\`.
- All timestamps are UTC and all primary keys are UUIDs.
- Storage is behind a generic interface; local and Google Drive (via rclone) are implemented today.

### What it protects against

- **Stolen database file.** Source, destination and channel credentials are encrypted (Fernet) with a key derived from the master key, which is never stored in the database.
- **Stolen backups or a leaked Drive account.** Optional age encryption. Losing the private key makes backups unrecoverable.
- **Web attacks on the UI.** Same-site HttpOnly session cookie, CSRF tokens on every state-changing request, login rate limiting with lockout, a strict Content-Security-Policy, X-Frame-Options DENY, nosniff and HSTS behind HTTPS.
- **Command injection.** Tools run with argument lists, never a shell. Credentials go through private 0600 files or the child's environment, never the command line.
- **Secret leaks.** Secrets are write-only in the API and scrubbed from logs, audit entries, manifests and notification payloads. Loopback and link-local notification targets are refused.
- **Path escapes and tampering.** Destination paths and symlinks are checked. SHA-256 is verified before any download or restore, and downloads use one-use links that expire after 60 seconds.
- **Abuse of the container.** Non-root user, read-only root file system, all capabilities dropped, no-new-privileges, memory and process limits.
- **Vulnerable dependencies.** CI runs pip-audit, yarn npm audit, Trivy on the image and gitleaks on the repository.

### What it does not protect against

- Someone with the master key and \`app.db\` can read every stored credential.
- Someone with shell access to the host or the Docker socket can read the key file, the data and the backups.
- A compromised database server or Drive account at backup time: the tool backs up what the source returns.
- A compromised admin session: there is one admin role, and every signed-in user can download, delete and restore.
- Plain HTTP: without a reverse proxy, passwords and cookies cross the network in clear text.
- Unencrypted backups on shared storage are only as private as the volume or account that holds them.
- Denial of service: there is login rate limiting but no general request limit.

### Hardening checklist

- Serve over HTTPS and set \`COOKIE_SECURE=true\`.
- Keep the master key and the age private keys off the server's backup volume.
- Use read-only backup users and separate restore users with write access.
- Review the Audit log after unexpected activity.
- Rotate the master key after a suspected leak and change database passwords at the source.
- Report vulnerabilities privately to the maintainer; do not open a public issue with details.`,
  },
  {
    id: "operations",
    label: "Operations",
    body: `## Operations

### Upgrade

\`\`\`bash
# 1. Copy the tool's own data first.
docker compose exec dbbm python -m app.cli backup-db
# 2. Pull or build the new version and restart.
docker compose pull   # or: docker compose build
docker compose up -d
# 3. Check the version and the scheduler.
curl -s http://127.0.0.1:8080/healthz
\`\`\`

Migrations run automatically and only move forward. To downgrade, stop the container, restore the \`app.db\` copy from step 1 and start the older image.

### Orchestrators

- \`GET /healthz\` returns 200 when the database is reachable and this instance owns the scheduler, 503 otherwise. Allow up to 15 seconds after start.
- Stop with \`SIGTERM\` and allow a 30 second grace period. Running backups are marked \`interrupted\` and temporary files are removed.

### HTTPS behind a reverse proxy

Keep the published port on 127.0.0.1, set \`COOKIE_SECURE=true\` and the Public base URL in Settings. Caddy:

\`\`\`nginx
backup.example.com {
    reverse_proxy 127.0.0.1:8080
}
\`\`\`

Traefik, as labels on the dbbm service:

\`\`\`yaml
labels:
  - traefik.enable=true
  - traefik.http.routers.dbbm.rule=Host(\`backup.example.com\`)
  - traefik.http.routers.dbbm.entrypoints=websecure
  - traefik.http.routers.dbbm.tls.certresolver=letsencrypt
  - traefik.http.services.dbbm.loadbalancer.server.port=8080
\`\`\`

Do not set short proxy timeouts on \`/api/backups/download/\`.

### Back up the tool itself

Everything the tool knows is in \`./data\`. The master key \`app_key.txt\` is kept outside it on purpose. Back up both, in different places.

\`\`\`bash
docker compose exec dbbm python -m app.cli backup-db --out /data/app.db.copy
docker compose exec dbbm python -m app.cli export-config --out /data/config-export.json
\`\`\`

Do not copy \`app.db\` with \`cp\` while the app runs. The config export has no secrets but does contain hostnames and usernames: treat it as confidential.

### Disaster recovery drill

Practise this at least twice a year:

1. Install Docker and the compose file on a new server.
2. Restore \`app_key.txt\` and the \`data/\` directory.
3. Run \`docker compose up -d\`, open the UI and sign in.
4. Check \`/healthz\` shows the scheduler running, then open each destination and run Test connection. Google Drive tokens may need re-authorizing.
5. Restore a recent backup into a scratch database.

### Rotate the master key

Rotation re-encrypts every stored secret and signs sessions out. age-encrypted backups are not affected. It is all or nothing, and it keeps a pre-rotate copy of \`app.db\` for roll back.

\`\`\`bash
docker compose stop
(umask 077; openssl rand -base64 32 > app_key.new)
# Dry run: decrypts everything, changes nothing.
docker compose run --rm --no-deps -v "$PWD/app_key.new:/run/secrets/app_key_new:ro" dbbm \\
  python -m app.cli rotate-key --new-key-file /run/secrets/app_key_new --dry-run
# Real run.
docker compose run --rm --no-deps -v "$PWD/app_key.new:/run/secrets/app_key_new:ro" dbbm \\
  python -m app.cli rotate-key --new-key-file /run/secrets/app_key_new
mv app_key.txt app_key.old && mv app_key.new app_key.txt
docker compose up -d
\`\`\`

### Lost admin password

Use **Settings > Change password** while you can sign in. Otherwise, reset it from the host:

\`\`\`bash
docker compose exec dbbm python -m app.cli reset-password admin
\`\`\`

### Monitoring and retention

- Add a notification channel, select it on every job and use Send test.
- Give important jobs a heartbeat URL so a stopped service alerts you.
- The Audit log keeps entries for the period in Settings (default 365 days).
- Watch the Dashboard for failed and overdue jobs and low disk space.
- Estimate disk as average backup size x runs kept x copies, plus headroom for one extra dump and temporary files in \`/data/work\`.`,
  },
  {
    id: "troubleshooting",
    label: "Troubleshooting",
    body: `## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Container unhealthy, \`not_owner\` in \`/healthz\` | Another container uses the same \`./data\`. Stop it. |
| "Stored credentials cannot be decrypted" | The master key differs from the one used to save them. Restore the original key or re-enter the credentials. |
| Source test fails with a TLS or auth error | Check the TLS mode and the user's privileges. |
| \`pg_dump\` older than the server | Rebuild the image with \`--build-arg PG_MAJOR=<server major>\` or newer. |
| Drive run fails with \`auth_expired\` | The OAuth consent screen is in Testing (tokens expire after 7 days) or access was revoked. Publish it, then re-authorize the destination. |
| Drive run fails with \`quota_exceeded\` | Daily upload quota or a full Drive. Retry later or free space. |
| Run is \`interrupted\` | The container stopped during the run. Check the schedule and the stop grace period. |
| Backups show "Missing" | The file was removed outside the app. Rescan the destination, or delete the entry. |
| Restore refuses: checksum mismatch | The stored file is damaged. Restore from another copy. |
| Download link is "invalid or expired" | Links work once for 60 seconds. Click Download again. |
| Locked out after failed logins | Wait for the lockout (default 15 minutes) or restart the container. |
| \`permission denied\` on \`./data\` or \`./backups\` | Set \`DBBM_UID\` and \`DBBM_GID\` in \`.env\` to your user. |`,
  },
];

const INTRO = `# DBBM

**Database Backup & Management**

A self-hosted Docker app that backs up MySQL, PostgreSQL, MongoDB and Firestore to a local volume and Google Drive, with a web UI. One image, one process, SQLite for its own metadata.`;

const FOOTER =
  "DBBM - Database Backup & Management. This page is public and shows no data from your instance.";

const SIDEBAR_ITEMS: SidebarItem[] = [
  ...SECTIONS.map((section) => ({
    key: section.id,
    label: section.label,
    href: `#${section.id}`,
  })),
];

function jumpTo(id: string) {
  window.location.hash = id;
}

export default function DocsPage() {
  // Only the top-right button depends on this; a 401 here is expected and does not redirect.
  const me = useMe();
  return (
    <Flex direction="column" className="min-h-screen bg-app-bg text-app-text">
      <AppBar
        brand="DBBM"
        logo={<Logo size={28} />}
        brandHref="/"
        linkComponent={Link}
        linkProp="to"
        themeToggle
        actions={
          me.isPending ? null : (
            <ButtonLink href={me.data ? "/" : "/login"} linkComponent={Link} linkProp="to">
              {me.data ? "Open dashboard" : "Sign in"}
            </ButtonLink>
          )
        }
      />
      <Container maxWidth="max-w-6xl" className="px-4 py-8">
        <Flex gap={8} align="start">
          <Container as="aside" className="sticky top-20 hidden shrink-0 !mx-0 !w-52 !px-0 lg:block">
            <Sidebar
              items={SIDEBAR_ITEMS}
              label="On this page"
              bordered={false}
              width="w-full"
              textCase="none"
              density="dense"
            />
          </Container>
          <Container as="main" className="min-w-0 flex-1 !mx-0 !w-auto !px-0">
            <Markdown>{INTRO}</Markdown>
            <Flex wrap gap={2} className="py-4 lg:hidden">
              {SECTIONS.map((section) => (
                <Chip key={section.id} onClick={() => jumpTo(section.id)}>
                  {section.label}
                </Chip>
              ))}
            </Flex>
            {SECTIONS.map((section) => (
              <Container as="section" key={section.id} className="py-6 [&_h2]:scroll-mt-20">
                <Markdown>{section.body}</Markdown>
              </Container>
            ))}
            <Markdown>{`---\n\n${FOOTER}`}</Markdown>
            <AppVersion className="block pb-4 text-app-muted" />
          </Container>
        </Flex>
      </Container>
    </Flex>
  );
}
