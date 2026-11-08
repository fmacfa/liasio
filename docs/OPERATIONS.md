# Install, back up and maintain

**English** · [Français](OPERATIONS.fr.md) · [Deutsches Betriebshandbuch](guide/de/operations.md)

<a id="topologie-de-production"></a>
## Production topology

```text
Internet :443 → Caddy → Gunicorn / Django → PostgreSQL (private network)
                                      → files (private Docker volume)
                                      → ClamAV (private network)
                        maintenance every 5 minutes → metadata and orphan cleanup
```

Only Caddy and ports 80/443 are accessible from the Internet. ClamAV has outbound access to update its signatures. Depending on configuration, the application uses the frontend network for SMTP, OIDC or S3; mail/webhook workers have their own outbound network. Provide enough disk space for files, temporary ZIPs, the database and backups.

Start with 8 GiB RAM and 2 vCPUs on a server dedicated to the complete stack. Container ceilings total 6.75 GiB, including 4 GiB for ClamAV; allow additional capacity for the operating system and maintenance. Signature updates can temporarily increase consumption. [Server / VPS requirements](../README.md#server--vps-requirements), also available in [French](../README.fr.md#besoins-serveur--vps) and [German](../README.de.md#server--vps-voraussetzungen), cover RAM, swap, storage, backups, kernel and capacity checks. Add other applications' requirements on a shared VPS.

<a id="démarrage-et-diagnostic"></a>
## Startup and diagnosis

```sh
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 app
docker compose logs --tail=100 clamav
docker compose exec app python manage.py check --deploy --fail-level ERROR
```

`check --deploy` may issue Django warnings about HSTS subdomains/preloading. These are disabled to avoid imposing HTTPS on other services on your domain. Secure cookies, HTTPS, the secret, antivirus and proxy must be configured. Do not expose port 8000 publicly.

A ClamAV outage blocks new uploads. Previously scanned files remain downloadable. If an unscanned demo file is moved into production, the application refuses to create a link or download it; upload it again into a collection with antivirus working.

For a 413 response, check sizes in `.env` and Caddy. ClamAV limits must also agree. Configure the required capacity rather than disabling antivirus to bypass a limit.

<a id="gestion-des-comptes"></a>
## Account management

```sh
# Change an individual quota; cannot go below current usage.
docker compose exec app python manage.py set_quota personne@example.com 10

# Recovery without SMTP; enter the password interactively, never as a secret argument.
docker compose exec app python manage.py changepassword personne@example.com

# Add an administrator from the server with interactive input.
docker compose exec app python manage.py createsuperuser

# Permanently erase a member and their files (administrators excluded).
docker compose exec app python manage.py erase_user personne@example.com --confirm
```

Web administrators can suspend members and revoke invitations. Each member manages their own files on Storage. The administration dashboard shows aggregate usage per account without granting access to their documents.

<a id="sauvegarder"></a>
## Back up

Version 0.5 also provides [encrypted off-server backups](OFFSITE_BACKUPS.md), [S3 storage and OIDC](ADVANCED_HOSTING.md), and an [upgrade guide](UPGRADING_0_5.md).

Backups must contain **the PostgreSQL database**, **the file volume** and a separate, encrypted copy of **`.env`**. Keep the matching code version/commit. Never put backups in the repository.

This script stops application writes, maintenance and webhook/email workers during export, then restarts them even if a command fails:

```sh
scripts/backup-and-verify.sh
```

It writes `backups/<date-UTC>/database.sql`, `files.tar.gz` and `checksums.json`. Archives include trash and temporary uploads. These files contain private data and are **not encrypted** by this script. Encrypt them and copy them to another medium using your backup solution. Test restoration regularly; checking that files exist is not sufficient reason to delete backups.

Storage measures the volume containing `/data/files`. Check capacity separately if PostgreSQL or backups use another disk. A quota is a logical limit, not a disk reservation.

<a id="restaurer-sur-une-installation-vide"></a>
## Restore to an empty installation

These commands assume a **new installation without data to preserve**, the correct code version and your securely recovered `.env`. Restoration overwrites the target's state; do not run it on your active instance.

```sh
docker compose build app
docker compose up -d db
# Wait for db to be healthy in docker compose ps.
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U linkvault -d linkvault < backups/DATE/database.sql
docker compose run -T --rm --no-deps --entrypoint tar app -xzf - -C /data < backups/DATE/files.tar.gz
# For an S3 backup, first set STORAGE_BACKEND=local in .env.
docker compose run -T --rm --no-deps app python manage.py migrate --noinput
docker compose run -T --rm --no-deps app python manage.py verify_storage
docker compose up -d db clamav app maintenance
docker compose ps
# After validation, open HTTPS traffic:
docker compose up -d caddy
# Check/cancel pending messages before starting mail and webhooks.
```

Check login, file lists, quota, a test download, expiry and a scanned upload. Restore the database and files from **the same backup**. If the domain changes, update `DOMAIN` and `APP_URL`; links on the old domain need a controlled external redirect or redistribution.

Backups may contain data deleted since they were created. The operator must organize their retention and deletion.

<a id="mise-à-jour"></a>
## Update

```sh
scripts/backup-and-verify.sh
docker compose stop app maintenance webhooks mail
git pull --ff-only
docker compose pull
docker compose build --pull app
docker compose up -d
docker compose ps
```

The application runs migrations before Gunicorn. The Python lockfile and image digests pin dependencies. Review and test digest updates regularly; pulling an unchanged digest does not fetch new vendor fixes. Dependabot tracks Dockerfile images; also check Compose service and CI images. Read release notes and test updates before applying them to an important instance.

For 0.5, follow the [upgrade guide](UPGRADING_0_5.md): stop every writer before migrations, then restart the complete stack. Old and new processes must not run together. Previews now require Linux Landlock ABI 3+; without it, previews remain unavailable while downloads keep working. The [automation](guide/en/automation.md) and [operations](guide/en/operations.md) guides explain upload-client and delivery-queue changes.

<a id="maintenance-et-secrets"></a>
## Maintenance and secrets

The `maintenance` container cleans up every five minutes: expired sessions, rate limits, old invitations, events beyond retention, and orphan files older than 24 hours. A link's expiry does **not** destroy files that other links may still use.

Do not change `SECRET_KEY` on every deployment: rotation invalidates sessions, invitations and sharing links. After a suspected leak, rotate deliberately, restart all services with the same key and distribute new links. `python manage.py refresh_share_urls` updates short-link digests with the new key; Docker runs it automatically after startup migrations. Also run it after migrations in manual installations. Old URLs remain invalid after rotation. Back up secrets separately from the public repository. Migration `0010` adds the short-link index; downgrades must retain this field and the 22-character decoder if short links have already been distributed. See [SHARE_LINKS.md](SHARE_LINKS.md).

<a id="autre-reverse-proxy"></a>
## Another reverse proxy

If replacing Caddy, preserve HTTPS and HSTS, body limits, receive timeouts, proxy-header replacement and Gunicorn network isolation. Do not log capability URLs. Do not put a public cache in front of private pages or downloads. Do not add `null` or wildcard origins to `CSRF_TRUSTED_ORIGINS`.

<a id="limites-de-la-version-05"></a>
## Version 0.5 limits

- Single-host deployment; originals local or in private S3, with local temporary uploads and archives. No distributed high availability.
- Private API with keys and permissions. Account-free client deposits require an opaque, user-created link, expiry and quotas; this is not an unlimited anonymous upload service.
- Default 1 GiB per file / 5 GiB per collection; resume for 24 hours by reselecting the same files.
- Complete or selective downloads; reduced image galleries and first PDF pages without active previews of originals. See [previews](PREVIEWS.md).
- Optional explicitly linked OIDC and email-code recipient verification. No end-to-end encryption; authorized recipients can retain copies.
- SMTP states indicate server acceptance; download counters indicate authorization, without measuring complete receipt.
- Trash kept for 30 days by default. Referenced older versions consume quota; unreferenced copies are purged after retention.
- An independent human audit and measurements on multiple VPS hosts remain necessary before 1.0: [maintenance policy](MAINTENANCE.md).

<a id="guide-de-fiabilité-02"></a>
## Reliability guide for 0.2

[RELIABILITY.md](RELIABILITY.md) describes new settings, MFA, alerts, isolated restoration checks and load tests. Keep `MFA_ENCRYPTION_KEY` stable; without it, the MFA key depends on `SECRET_KEY`. Back up this configuration before rotating anything. Password recovery does not disable MFA.

<a id="automatisations-e-mails-et-traductions"></a>
## Automations, email and translations

The `mail` service handles approved messages and recipient codes with two workers, one concurrent delivery per sender, bounded retries and encrypted database content. For manual installations supervise `python manage.py deliver_mail --loop --workers 2`. Configure SMTP and inspect states under **Emails**. Messages are not delivered without this service.

The full stack includes `webhooks`, with two processes, PostgreSQL and public outbound HTTPS access, without an originals volume. Check `docker compose logs --tail=50 webhooks` and the Automations page. For a manual installation supervise `python manage.py deliver_webhooks --loop --workers 2`. Accounts take turns and can each occupy only one concurrent delivery. An event queue delivers nothing without this service.

Destination, signing-secret and idempotency-receipt encryption uses the same stable key as MFA. Preserve `MFA_ENCRYPTION_KEY` (or `SECRET_KEY` when absent) during restoration. Password/MFA changes invalidate API keys. API receipts expire after 24 hours; deliveries after 30 days through `housekeeping`.

Compiled `.mo` catalogs are included. Run `collectstatic` after every update, including for guide screenshots. Browser language selects FR/EN/DE by default; a manual choice is saved in a cookie. User titles, filenames and presentations remain unchanged. [English guide](guide/en/README.md) · [French guide](guide/fr/README.md) · [German guide](guide/de/README.md) · [API and webhooks](API.md).
