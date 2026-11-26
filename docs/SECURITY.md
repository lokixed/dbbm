# Security notes

## What DBBM protects against

- **Stolen database file (`app.db`):** source, destination and channel credentials are encrypted
  (Fernet) with a key derived from the master key. The master key is never stored in the database.
- **Stolen backups or a leaked Drive account:** optional age encryption. The server only holds
  public keys; losing the private key makes backups unrecoverable.
- **Web attacks on the UI:** same-site session cookie (`HttpOnly`, `Secure` behind HTTPS), CSRF
  tokens on every state-changing request, login rate limiting with lockout, a strict
  Content-Security-Policy, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `nosniff`,
  HSTS behind HTTPS, and `Cache-Control: no-store` on API responses.
- **Command injection:** tools run with argument lists, never a shell. Credentials go through
  private `0600` files or the child's environment, never the command line.
- **Secret leaks:** secrets are write-only in the API and scrubbed from logs, audit entries,
  manifests and notification payloads. Notification targets on loopback or link-local addresses
  (including cloud metadata) are always refused.
- **Path escapes:** destination paths are checked and symlinks that leave the backup directory are
  rejected.
- **Tampered backups:** SHA-256 is recorded at backup time and checked before any download or
  restore. Downloads use one-use links that expire after 60 seconds.
- **Abuse of the container:** non-root user, read-only root file system, all capabilities dropped,
  `no-new-privileges`, memory and process limits (docker-compose.yml).
- **Vulnerable dependencies:** CI runs `pip-audit`, `yarn npm audit`, Trivy on the image, and
  gitleaks on the repository.
- **Interactive API docs** (`/api/docs`) are off unless `ENABLE_DOCS=true`.
- **A stolen OAuth code (Connect Google Drive):** Google redirects straight back to DBBM over
  https. The code is useless without the client secret and the PKCE verifier, which stay on the
  server, and the callback accepts it only for a live, one-use connect request of the same
  signed-in session.

## What it does not protect against

- **Someone with the master key and `app.db`:** they can read every stored credential.
- **Someone with shell access to the host or the Docker socket:** they can read the key file, the
  data and the backups. The admin password reset also needs that access.
- **A compromised database server or Drive account at backup time:** the tool backs up what the
  source returns. Use age encryption and keep an offline copy for ransomware scenarios.
- **Compromised admin session:** there is one admin role. Every signed-in user can download,
  delete and restore. Multi-user roles are not part of 1.0.
- **Plain HTTP:** without a reverse proxy, passwords and cookies cross the network in clear text.
  Keep the port on `127.0.0.1` or put HTTPS in front.
- **Unencrypted backups on shared storage:** local and Drive copies are as private as the volume
  or account that holds them unless you turn on age encryption.
- **Denial of service:** there is login rate limiting, but no general request limit. Do not expose
  the UI to the internet without a proxy that adds one.

## Hardening checklist

- Serve over HTTPS and set `COOKIE_SECURE=true`.
- Keep the master key and the age private keys off the server's backup volume.
- Use read-only backup users and separate restore users with write access.
- Review the **Audit log** after unexpected activity.
- Rotate the master key after a suspected leak ([OPERATIONS.md](OPERATIONS.md)) and change
  database passwords at the source.
- Report vulnerabilities privately to the maintainer; do not open a public issue with details.
