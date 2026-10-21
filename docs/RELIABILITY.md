# Reliability and operations in 0.2

## Upgrade from 0.1

Back up the database, files and original `.env` before upgrading. Keep those backups offline and encrypted. Check out the release tag, rebuild or pull the released image, and run `docker compose up -d`. The application applies migrations at startup. Do not run `configure.py` over an existing installation or rotate keys as part of this upgrade.

0.2 adds nullable deletion dates, MFA fields, reserved quota counters, upload sessions and operational state. Existing accounts, file paths and share links remain valid. Downgrading requires restoring the complete pre-upgrade backup; do not run 0.1 against a database containing trashed files.

New installations generate a separate `MFA_ENCRYPTION_KEY`. Existing installations without that variable derive a stable encryption key from the original `SECRET_KEY`. **Keep the original choice and value** after enabling MFA. Changing either the explicit MFA key, or the fallback secret when no explicit key is set, makes existing authenticator secrets unreadable. Recovery codes remain usable. A password reset does not disable MFA.

## Account protection

Users enable TOTP under **Mon compte → Gérer la double authentification**, after confirming their current password. They must enter a valid application code before activation. Ten random, single-use recovery codes are displayed only once and stored as password hashes. Save them in a password manager.

A successful password check creates only a five-minute pending login. No account resources are accessible before the second factor succeeds. TOTP codes have a 30-second step and a one-step clock tolerance; previously accepted steps cannot be reused. Account/IP throttles also apply to recovery codes. Setup expires after ten minutes. Disabling MFA or generating new recovery codes requires the password and a current second factor. Changes invalidate other authenticated sessions.

MFA is optional per account in this release. Administrators can see enrollment status in **Exploitation**. Central MFA enforcement, passkeys and account recovery without a recovery code require additional policy work.

## Trash and quota

Deleting a folder or a file moves it to **Corbeille** and immediately revokes affected links. It remains in the stored-byte quota until permanently purged. Restoring never reactivates old links. Files that were already individually trashed stay in the trash when their parent folder is restored.

`TRASH_RETENTION_DAYS` defaults to 30. Maintenance purges expired items every five minutes. Permanent deletion requires a separate confirmation page; blobs are deleted only after the database transaction commits. An orphan sweep retries abandoned physical files after 24 hours. Paths and folder limits include trash, so purge or restore an old path before replacing it with an upload.

The storage page lists active files. The operations page counts all stored files and distinguishes trash and reserved uploads. Administrators adjust quotas in GiB; a new quota cannot be below current stored bytes plus pending reservations.

## Resumable uploads

The browser uploads 4 MiB blocks, with a SHA-256 checksum for each block. The server keeps durable offsets and private staging files for 24 hours from session creation. It reserves the complete declared size at creation, limits each account to five pending sessions, and checks ownership and CSRF on every write.

Pause, reload or reconnect, then select the same files and click **Importer**. The browser matches normalized paths, sizes and a fingerprint of the file metadata and first/last 64 KiB to the pending session. It resumes at the server's confirmed offset. This fingerprint identifies a selection; the server computes the complete stored file's SHA-256 independently. If the local file changed, cancel the old session and start again. The browser does not retain file contents or persistent filesystem access.

A repeated block returns the authoritative offset instead of duplicating bytes. Each block is flushed to disk before its offset commits. An uncommitted crash tail is truncated on retry. Files become visible only when the complete batch passes antivirus, folder and quota checks. Antivirus failure leaves the staged upload available for retry or cancellation and never publishes partial files.

Defaults: **1 GiB per file, 5 GiB per folder, 100 files per folder**, and a 5 GiB quota for new accounts. A plain multipart upload without JavaScript remains limited to 500 MiB per request. Existing explicit environment overrides remain in force. Keep ClamAV's stream, file and expanded archive limits in sync with any limit changes; files that exceed scanning limits are rejected.

Reserve disk headroom for staging, the stored copy, temporary download ZIPs, the database and backups. `MIN_FREE_DISK_BYTES` defaults to 1 GiB. New upload reservations account conservatively for twice all reserved bytes. Multipart uploads reserve temporary capacity before body parsing, enforce the remaining account allowance while receiving and allow at most two simultaneous classic uploads per account. ZIP downloads reserve their complete uncompressed size plus metadata before generation and hold that budget until the response closes. These paths share a global admission lock and count outstanding resumable reservations. Private POSIX file locks release automatically when a worker exits; the next admission removes abandoned lease records. This design requires the documented single-host deployment and a local filesystem with working POSIX locks (Linux containers or local macOS/Linux development). It deliberately counts some bytes twice to keep headroom. External processes, PostgreSQL and backup jobs must still be monitored separately. This is capacity protection, not an OS filesystem quota. Finalization is serialized per account; for production use PostgreSQL, as SQLite serializes writers globally.

## Monitoring and alerts

The maintenance container refreshes health and alerts every five minutes. **Exploitation** shows storage, quota usage, MFA enrollment, antivirus status, active alerts and the last successful backup restoration check. Alerts cover disk use at 85%, minimum free disk, accounts at 90% of quota, unavailable antivirus and stale backup verification. The maintenance heartbeat is also available to an external monitor.

Configure `EMAIL_HOST` and the other SMTP values, plus `MONITOR_EMAIL`, to opt into administrator emails. One email is sent for each newly active condition. A recovered condition can trigger a new email if it recurs. SMTP failures are retried next cycle. No operational email is sent by default.

`GET /health/` is a minimal database liveness check. `GET /ready/` requires `Authorization: Bearer <MONITORING_TOKEN>` with a configured token of at least 32 characters. It returns JSON with readiness, disk capacity, database/antivirus status, maintenance freshness and backup verification freshness. It contains no account identifiers. Readiness is HTTP 503 when the database, mandatory antivirus or minimum disk capacity fails. An external monitor should also alert when `maintenance` or `backup_verified_recently` is false; a stopped maintenance process cannot send its own email.

```sh
docker compose exec -T app python manage.py check_health
# Explicitly refresh alerts and send opted-in notifications:
docker compose exec -T app python manage.py check_health --notify
```

Keep monitoring tokens out of query strings, logs and public dashboards. `BACKUP_MAX_AGE_HOURS` defaults to 48.

## Back up and prove that restoration works

```sh
scripts/backup-and-verify.sh
```

This stops active application/maintenance services, dumps PostgreSQL, archives files **including trash and staged uploads**, writes SHA-256 checksums and restarts only services that were previously running. Downloads/uploads may be interrupted during that maintenance window.

It then creates a uniquely named, isolated Docker Compose project, restores the database and file archive into its own new volumes, applies migrations, and checks every stored file's complete SHA-256, recorded size, quota counters, staged offsets and MFA secret decryption. Its temporary containers and volumes are removed on exit. Only a successful check updates the source instance's backup verification record. Freshness is based on the backup snapshot time, so repeatedly checking an old backup cannot hide a stale backup schedule. The live database and files are never replaced by this test.

Run an existing backup check separately with:

```sh
scripts/restore-test.sh backups/YOUR-BACKUP-DIRECTORY
```

Backups are local and **not encrypted by these scripts**. Store `.env` separately in an encrypted secret backup, encrypt backup archives, copy them off the server and define retention. Checksums detect accidental corruption; they do not authenticate a backup from an untrusted source. Restore only your own trusted backups. Ensure enough additional disk capacity for a full second copy. Schedule the command using your server's scheduler if unattended backups are desired; the application does not install a host timer automatically.

The CI workflow runs this real restore check against PostgreSQL with fixture data on every pull request and main-branch update. For disaster restoration into your intended production destination, follow [OPERATIONS.md](OPERATIONS.md); that operation is deliberately separate from the disposable test.

## Load evidence and reproducibility

```sh
uv run --env-file .env python scripts/load_test.py --workers 4 --mib 256
# In the CI application container, using a newly created disposable PostgreSQL database:
python scripts/load_test.py --database postgres --workers 4 --mib 256 --output /tmp/load-report.json
```

The probe creates a disposable loopback server and test accounts. It uploads one 256 MiB file per concurrent account, deliberately retries a committed block, resumes from a durable offset, finalizes, creates a single-use link, downloads the ZIP, checks the complete SHA-256 and confirms the link is exhausted. It then checks the storage counters. No existing account or database is used. PostgreSQL mode requires permission to create its randomly named temporary database; it removes only that database afterwards.

A report records volume, timings, database, antivirus state, integrity and replay outcomes. The committed [local reference report](benchmarks/local-v0.2.json) represents one machine, SQLite and disabled antivirus. CI uploads a separate `load-report-postgres-clamav` artifact for its PostgreSQL + real ClamAV run. These are reproducible workload probes, not Internet throughput or maximum capacity guarantees. Repeat on your production hardware and network before choosing service-level targets.
