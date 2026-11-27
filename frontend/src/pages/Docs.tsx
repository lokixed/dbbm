import { useEffect, type ElementType } from "react";
import { Link, Navigate, useLocation, useParams } from "react-router-dom";
import AppBar from "xedonium/AppBar";
import ButtonLink from "xedonium/ButtonLink";
import Chip from "xedonium/Chip";
import Container from "xedonium/Container";
import Flex from "xedonium/Flex";
import Markdown from "xedonium/Markdown";
import Sidebar from "xedonium/Sidebar";
import type { SidebarItem } from "xedonium/Sidebar";
import DatabaseIcon from "xedonium/icons/Database";
import DownloadIcon from "xedonium/icons/Download";
import RocketIcon from "xedonium/icons/Rocket";
import BellIcon from "xedonium/icons/Bell";
import RotateCcwIcon from "xedonium/icons/RotateCcw";
import KeyRoundIcon from "xedonium/icons/KeyRound";
import ShieldCheckIcon from "xedonium/icons/ShieldCheck";
import SettingsIcon from "xedonium/icons/Settings";
import WrenchIcon from "xedonium/icons/Wrench";
import { useMe } from "@/api/auth";
import { AppVersion } from "@/components/AppVersion";
import { Logo } from "@/components/Logo";

// Public page: no sign-in required, and only xedonium components. The text mirrors README.md,
// docs/OPERATIONS.md and docs/SECURITY.md; keep them in sync when those change.

// One icon per section, all in the app blue. Tints use opacity so they work in both themes.
type Accent = { icon: ElementType; banner: string; badge: string; heading: string };

const ACCENTS: Record<string, Accent> = {
  features: { icon: DatabaseIcon, banner: "bg-app-strong/10", badge: "bg-app-strong/20 text-app-strong", heading: "[&_h3]:text-app-strong" },
  install: { icon: DownloadIcon, banner: "bg-app-strong/10", badge: "bg-app-strong/20 text-app-strong", heading: "[&_h3]:text-app-strong" },
  "first-backup": { icon: RocketIcon, banner: "bg-app-strong/10", badge: "bg-app-strong/20 text-app-strong", heading: "[&_h3]:text-app-strong" },
  notifications: { icon: BellIcon, banner: "bg-app-strong/10", badge: "bg-app-strong/20 text-app-strong", heading: "[&_h3]:text-app-strong" },
  restore: { icon: RotateCcwIcon, banner: "bg-app-strong/10", badge: "bg-app-strong/20 text-app-strong", heading: "[&_h3]:text-app-strong" },
  encryption: { icon: KeyRoundIcon, banner: "bg-app-strong/10", badge: "bg-app-strong/20 text-app-strong", heading: "[&_h3]:text-app-strong" },
  security: { icon: ShieldCheckIcon, banner: "bg-app-strong/10", badge: "bg-app-strong/20 text-app-strong", heading: "[&_h3]:text-app-strong" },
  operations: { icon: SettingsIcon, banner: "bg-app-strong/10", badge: "bg-app-strong/20 text-app-strong", heading: "[&_h3]:text-app-strong" },
  troubleshooting: { icon: WrenchIcon, banner: "bg-app-strong/10", badge: "bg-app-strong/20 text-app-strong", heading: "[&_h3]:text-app-strong" },
};

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

Open http://localhost:8080, create the admin account, then sign in.

- Port 8080 is published on all host interfaces. To reach it from other machines, put Caddy or Traefik in front for HTTPS and set \`COOKIE_SECURE=true\` in \`.env\`.
- The master key comes from \`APP_SECRET_KEY\` or \`APP_SECRET_KEY_FILE\`. Losing it means re-entering every stored credential.
- Health check: \`curl http://localhost:8080/healthz\`.
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

**All databases** skips system databases (for PostgreSQL: \`postgres\`, \`template0\`, \`template1\`). On Supabase and similar hosts your data is in \`postgres\`, so choose **Selected databases** and tick it, and use the Session pooler connection string.

Passwords, connection URIs and service-account keys are write-only: once saved they show as "Saved" and are never returned by the API.

### Backup user privileges

- **MySQL:** \`SELECT\`, \`SHOW VIEW\`, \`TRIGGER\`, \`EVENT\` on the databases, and the global \`SHOW_ROUTINE\` privilege (8.0.20+) if they contain stored routines.
- **PostgreSQL:** \`CONNECT\` and \`SELECT\` on everything, for example membership in \`pg_read_all_data\`.`,
  },
  {
    id: "notifications",
    label: "Notifications",
    body: `## Notifications

Channels tell you when something needs attention. Add them under **Notifications**, then pick the channels on each job (**Notifications** section of the job form). Choose when a job sends with **Notify**: on failure, always, or never; **Notify on recovery** also sends one message when a failing job succeeds again.

Channels also receive system warnings, whichever jobs they are attached to: an overdue job, an expired Google Drive authorization, a failed retention run and low disk space.

- **Send test** in the channel form sends one message with the values on screen, before you save. Nothing is stored for an unsaved test.
- Each delivery is tried up to 3 times with a short pause. A channel sends at most 20 messages an hour; extra messages are counted and summarised in one later message. Open **Delivery events** on a channel to see every attempt and its error.
- Webhook URLs, SMTP passwords, HMAC secrets and custom headers are write-only. Once saved they show as "Saved" and are never shown again.
- Targets on loopback, link-local and private networks are refused. If your webhook or mail server is on a private network, set \`ALLOW_PRIVATE_NOTIFICATION_TARGETS=true\` in \`.env\`.

### Slack

1. In Slack open https://api.slack.com/apps, choose **Create New App > From scratch**, name it \`DBBM\` and pick your workspace.
2. Open **Incoming Webhooks**, turn **Activate Incoming Webhooks** on, then click **Add New Webhook to Workspace** and choose the channel the messages should go to.
3. Copy the webhook URL. It looks like \`https://hooks.slack.com/services/T000/B000/XXXX\`.
4. In DBBM click **Add channel**, set **Type** to Slack, enter a name, paste the URL and click **Send test**, then **Save**.

The URL must be \`https\`. Messages show the job and status with a coloured bar, the trigger, duration, files and size, each destination, any error, and **Open run** and **Job's runs** buttons. The buttons appear once **Public base URL** is set under Settings > General. To change the destination channel, create a new webhook in Slack and replace the URL.

### Email

1. Get the SMTP details from your mail provider: host, port, security mode, and a username and password (many providers need an app password or an SMTP-only credential, not your normal password).
2. In DBBM add a channel, set **Type** to Email and fill in **SMTP host**, **Security** and **Port**.
3. Enter **Username** and **Password** if the server requires sign-in, a **From** address the server accepts, and one or more **To** addresses separated by commas (up to 20).
4. Click **Send test**, check the mailbox, then **Save**.

| Security | Port | Use it for |
| --- | --- | --- |
| STARTTLS | 587 | Most providers. The default. |
| SSL/TLS | 465 | Providers that use implicit TLS. |
| None | 25 | An internal relay only. Credentials and mail travel unencrypted, so DBBM shows a warning. |

Choosing a security mode fills in its usual port. Messages are sent as HTML with a plain-text part and a subject starting with \`[DBBM]\`. If the test fails with an authentication error, check that the provider allows SMTP sign-in for that account and that the From address belongs to it.

### Webhook

A webhook posts one JSON document to a URL you control, for example a chat bridge, Zapier, n8n or your own service.

1. Create an endpoint that accepts \`POST\` with \`Content-Type: application/json\` and answers with a \`2xx\` status.
2. In DBBM add a channel, set **Type** to Webhook and paste the **Webhook URL** (\`http\` or \`https\`; use \`https\` for anything outside your network).
3. Optional: set an **HMAC secret** so you can check that a request really came from DBBM (see below).
4. Optional: add up to 10 custom **headers**, for example \`Authorization: Bearer ...\`. Header values are stored encrypted. \`Content-Type\`, \`Host\`, \`Content-Length\` and \`X-Signature-256\` are reserved.
5. Click **Send test**, then **Save**.

Example body for a finished run:

\`\`\`json
{
  "event": "run_finished",
  "timestamp": "2026-11-25T09:00:12Z",
  "status": "failed",
  "title": "Backup nightly failed",
  "job": { "id": "...", "name": "nightly" },
  "run": {
    "id": "...",
    "status": "failed",
    "trigger": "schedule",
    "started_at": "2026-11-25T09:00:00Z",
    "finished_at": "2026-11-25T09:00:12Z",
    "duration_seconds": 12,
    "artifacts": { "count": 0, "total_bytes": 0 },
    "destinations": [{ "name": "local", "type": "local", "status": "failed", "error": "..." }],
    "error": "..."
  },
  "url": "https://dbbm.example.com/runs/..."
}
\`\`\`

\`event\` is one of \`run_finished\`, \`recovery\`, \`overdue\`, \`auth_expired\`, \`retention_failed\`, \`low_disk\` or \`test\`. Other events carry their own fields next to \`event\`, \`timestamp\`, \`status\`, \`title\` and \`url\`.

**Verifying the signature.** With an HMAC secret set, every request has an \`X-Signature-256\` header holding \`sha256=\` and the hex HMAC-SHA256 of the raw request body, keyed with your secret. Compute it over the exact bytes you received and compare in constant time:

\`\`\`python
import hashlib, hmac

def valid(secret: str, body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)
\`\`\`

Respond within 10 seconds. A non-2xx answer or a timeout counts as a failed attempt and is retried.`,
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

1. Generate a key pair: click **Generate a key pair** in **Settings > Backup encryption keys** (made in your browser; the private key downloads as a file and is never sent to the server; needs HTTPS or localhost), or run \`age-keygen -o backup-key.txt\` on your own machine. Store the private key offline.
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
- **A stolen OAuth code.** Connect Google Drive redirects straight back to DBBM over https. The code is useless without the client secret and PKCE verifier, which stay on the server, and the callback only accepts a live, one-use request of the same session.

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
curl -s http://localhost:8080/healthz
\`\`\`

Migrations run automatically and only move forward. To downgrade, stop the container, restore the \`app.db\` copy from step 1 and start the older image.

### Orchestrators

- \`GET /healthz\` returns 200 when the database is reachable and this instance owns the scheduler, 503 otherwise. Allow up to 15 seconds after start.
- Stop with \`SIGTERM\` and allow a 30 second grace period. Running backups are marked \`interrupted\` and temporary files are removed.

### HTTPS behind a reverse proxy

Put a proxy in front of the published port, set \`COOKIE_SECURE=true\` and the Public base URL in Settings. Caddy:

\`\`\`nginx
backup.example.com {
    reverse_proxy localhost:8080
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
| Run fails: "There are no databases to back up on this source" | The source is set to **All databases**, which skips system databases, and the server has no others. On Supabase and similar hosts the data is in \`postgres\`: edit the source, choose **Selected databases** and tick it. |
| Supabase source test times out or cannot resolve the host | The direct connection is IPv6-only. Use the **Session pooler** connection string (port 5432, user \`postgres.<project-ref>\`), not the Transaction pooler (port 6543). |
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
    href: `/docs/${section.id}`,
  })),
];

export default function DocsPage() {
  // Only the top-right button depends on this; a 401 here is expected and does not redirect.
  const me = useMe();
  const { section: sectionId } = useParams();
  const { hash } = useLocation();
  // The text is rendered after load, so the browser cannot scroll to a #heading link by itself.
  useEffect(() => {
    if (hash) document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView();
  }, [hash, sectionId]);
  const index = SECTIONS.findIndex((section) => section.id === sectionId);
  if (index < 0) return <Navigate to={`/docs/${SECTIONS[0].id}`} replace />;
  const current = SECTIONS[index];
  const accent = ACCENTS[current.id];
  const AccentIcon = accent.icon;
  const previous = SECTIONS[index - 1];
  const next = SECTIONS[index + 1];
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
              activeKey={current.id}
              linkComponent={Link}
              linkProp="to"
              label="Docs"
              bordered={false}
              width="w-full"
              textCase="none"
              density="dense"
            />
          </Container>
          <Container as="main" className="min-w-0 flex-1 !mx-0 !w-auto !px-0">
            {index === 0 && (
              <Container className="!mx-0 !w-auto bg-app-strong/10 !px-6 py-4">
                <Markdown>{INTRO}</Markdown>
              </Container>
            )}
            <Flex wrap gap={2} className="py-4 lg:hidden">
              {SECTIONS.map((section) => (
                <Link key={section.id} to={`/docs/${section.id}`}>
                  <Chip>{section.label}</Chip>
                </Link>
              ))}
            </Flex>
            <Flex
              align="center"
              gap={3}
              className={`mt-4 px-4 py-3 ${accent.banner}`}
            >
              <span className={`flex h-9 w-9 items-center justify-center ${accent.badge}`}>
                <AccentIcon className="h-5 w-5" />
              </span>
              <span className="font-semibold">{current.label}</span>
              <span className={`ml-auto px-2 py-0.5 text-xs font-medium ${accent.badge}`}>
                {index + 1} / {SECTIONS.length}
              </span>
            </Flex>
            <Container
              as="section"
              className={`py-6 [&_a]:text-app-strong [&_h2]:sr-only [&_h3]:scroll-mt-20 [&_strong]:text-app-text ${accent.heading}`}
            >
              <Markdown>{current.body}</Markdown>
            </Container>
            <Flex justify="between" gap={4} className="pb-6">
              {previous ? (
                <ButtonLink href={`/docs/${previous.id}`} linkComponent={Link} linkProp="to">
                  {`← ${previous.label}`}
                </ButtonLink>
              ) : (
                <span />
              )}
              {next && (
                <ButtonLink href={`/docs/${next.id}`} linkComponent={Link} linkProp="to">
                  {`${next.label} →`}
                </ButtonLink>
              )}
            </Flex>
            <Markdown>{`---\n\n${FOOTER}`}</Markdown>
            <AppVersion className="block pb-4 text-app-muted" />
          </Container>
        </Flex>
      </Container>
    </Flex>
  );
}
