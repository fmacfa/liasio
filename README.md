# Liasio

Liasio is the new name of LinkVault. Existing repository URLs, deployment identifiers and API v1 headers keep their original names so existing installations and integrations continue to work.

**English** · [Français](README.fr.md)

**Your files. Your domain. Your rules.**

Liasio is an open-source file-sharing application that you host on your own server. Organize files into folders, customize their presentation, and create independent sharing links with passwords, expiration dates, or download limits.

French / English interface · **MIT** license · Python / Django · Docker Compose

> **Version 0.4 — connected workflows, in two languages.** Scoped API keys, signed webhooks and illustrated in-app guides join galleries, TOTP, resumable uploads and recoverable trash. This remains an early release; it has not undergone an independent human security audit and does not provide end-to-end encryption.

![Liasio dashboard with fictional demo data](docs/screenshots/dashboard.png)

## Features

| Need | Available in version 0.4 |
| --- | --- |
| First-time setup | Two short steps: email and password, then name and password confirmation; the first account becomes the administrator |
| Multiple accounts | Personal invitations valid for 7 days, single-use and revocable; isolated member accounts |
| File library | Upload files or a complete directory while preserving its structure; add a title, description, and message to the recipient |
| Upload page | Mixed file/folder drops, cumulative selection, real byte progress, pause/resume for 24 hours, then antivirus verification |
| Sharing page | Per-account logo, background image, custom colors, name, tagline and footer, with a live preview |
| File previews | Image gallery and first-page PDF previews, lightbox and keyboard navigation; optional per link |
| Multiple links per folder | Internal label, optional password without a minimum length, expiry date or duration, and maximum download count |
| Single-use links | One authorized download, including when requests arrive simultaneously |
| Activity tracking | Dated page opens and authorized downloads, filtering by link, and CSV export |
| Storage management | File counts, sizes, quotas, search, sorting by size/name/date, and individual/bulk deletion to a recoverable 30-day trash |
| Storage administration | Available disk space, total file count, and per-account usage; quotas editable in the administration page |
| Access management | Suspend members and revoke their links and sessions; password changes/recovery, optional TOTP and one-use recovery codes |
| Automations | Scoped, expiring API keys; resumable imports and link creation; signed webhooks, delivery history and bounded retries |
| Illustrated help | 11 guides in English and French, available on GitHub and under User guide in the application |
| Languages | French and English, including recipient pages, forms, messages and upload progress |
| Operations | Automatic HTTPS through Caddy, PostgreSQL, ClamAV antivirus, five-minute health checks, optional alerts, and automatically tested backup restoration |

**A folder is stored only once.** Create a password-protected link for one client, another that expires tomorrow, and a third that allows a single download.

### What “opened” and “downloaded” mean

- **Page opened:** the authorized sharing page was served to a browser. Opens within the same session are deduplicated for 30 minutes. A link-preview bot may also open a page.
- **Download authorized:** the server prepared the ZIP archive and reserved one use before sending it. This does not prove that the complete file was received. An interrupted transfer consumes that use; the owner can create a new link.
- **Single-use:** opening the page does not consume a use. The download button sends a CSRF-protected POST request. An atomic database operation prevents simultaneous downloads from exceeding the limit.
- **Revocation:** blocks future authorizations. It cannot recall files already received and does not necessarily interrupt a transfer already authorized.

## User guides and automations

Start with the **[illustrated English guide](docs/guide/en/README.md)** or **[guide français](docs/guide/fr/README.md)**. The same pages and screenshots are built into `/help/`.

For n8n, scripts and other tools, see the [API reference](docs/API.md), the [automation walkthrough](docs/guide/en/automation.md), the [Python upload client](examples/upload.py) and the [webhook signature verifier](examples/verify_webhook.py). No public registration or anonymous upload is added.

## Install on your domain

Use a Linux server with **Docker Engine and Docker Compose v2**, Git, and Python 3 for configuration. For the complete stack, start with **8 GB of RAM** (including up to 4 GB for ClamAV), 2 vCPUs, and enough disk space for your files. These are starting recommendations, not load-test results.

1. Point your domain's DNS A record, and AAAA record if used, to the server.
2. Allow inbound ports **80 and 443**. Do not expose the database, application, or antivirus service directly.
3. Clone the repository and generate unique secrets:

```sh
git clone https://github.com/fmacfa/linkvault.git
cd linkvault
python3 scripts/configure.py --domain files.example.com
docker compose up -d --build
docker compose ps
```

4. Wait for ClamAV to become ready; its first startup may take several minutes.
5. Open `https://files.example.com`. Enter your email and a password, choose **Continue**, then enter your name and confirm the password. Select **Create my account**. No installation key is needed.
6. Create a folder, upload files, then select **Create a link**. Choose **FR / EN** to change the interface language.

The configuration script creates `.env` with `0600` permissions and refuses to overwrite an existing file. It contains secrets: never publish it. The first person to complete setup becomes the administrator: initialize the instance before sharing its address. Setup permanently closes after this account is created; other users join by invitation.

Caddy obtains and renews the TLS certificate. If you use another reverse proxy, read the [operations guide](docs/OPERATIONS.md) (French) before changing the topology.

### Prebuilt images and upgrades

Versioned images are published by GitHub Actions with a software bill of materials (SBOM) and provenance. Releases also provide a compressed Docker archive for offline installation. See [releases](https://github.com/fmacfa/linkvault/releases) and the [changelog](CHANGELOG.md).

Set `LINKVAULT_IMAGE=ghcr.io/fmacfa/linkvault:0.4.0` in `.env` to use the registry image, then `docker compose pull app maintenance preview webhooks` and `docker compose up -d --no-build`. For source builds, keep the default and use `--build`.

Before upgrading from 0.1, preserve your original keys and take a complete backup. Read the [upgrade and reliability guide](docs/RELIABILITY.md), including MFA key handling, trash quotas, monitoring and restore verification.

Source builds after 0.4.0 include the [unreleased hardening changes](CHANGELOG.md#unreleased): fair webhook dispatch, stronger file/state handling and preview isolation. Stop webhook workers before migration `0006` and restart the updated stack together. Production previews require Linux Landlock ABI 3+; see the [updated operations guide](docs/guide/en/operations.md). The existing 0.4.0 image does not contain these changes. Vendor images are pinned by digest and require reviewed updates; future release notes provide workflow/tag/commit-bound attestation verification and an immutable image reference.

## Try it locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run:

```sh
git clone https://github.com/fmacfa/linkvault.git
cd linkvault
python3 scripts/configure.py --dev
uv sync --frozen
uv run --env-file .env python manage.py migrate
uv run --env-file .env python manage.py runserver 127.0.0.1:8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Create the first account with your email and password; no installation key is required. This mode uses SQLite, HTTP, and explicitly disabled antivirus scanning. It is intended only for your local machine and demonstration files.

To populate an **empty local installation** with fictional demo data, choose a demo password and run:

```sh
DEMO_PASSWORD='replace-with-your-demo-passphrase' uv run --env-file .env python manage.py seed_demo
```

The account email is `demo@example.com`. This command refuses to run in production or when an account already exists. Do not reuse demo data or secrets on your public server.

## Configuration

Configure values in `.env`; [.env.example](.env.example) provides a template.

| Setting | Default / purpose |
| --- | --- |
| `APP_URL` / `DOMAIN` | Canonical HTTPS origin without a path / domain name |
| `SECRET_KEY` | Random secret of at least 50 characters; signs sessions and access tokens |
| `MAX_FILE_BYTES` | 1073741824: 1 GiB per file |
| `MAX_UPLOAD_BYTES` | 5368709120: 5 GiB per folder; multipart fallback remains 500 MiB/request |
| `DEFAULT_QUOTA_BYTES` | 5368709120: 5 GiB for each new account |
| `EVENT_RETENTION_DAYS` | 90 days of activity and administrative history |
| `TIME_ZONE` | `Europe/Berlin`; timestamps are stored in UTC and displayed in this time zone |
| `ANTIVIRUS_REQUIRED` | `true` in production; uploads fail if scanning cannot complete |
| `CLAMAV_HOST` | `clamav` in Compose; a private service that must never be exposed to the Internet |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | Optional SMTP settings for password recovery |
| `EMAIL_USE_TLS` / `EMAIL_USE_SSL` | STARTTLS by default; do not enable both at the same time |
| `DEFAULT_FROM_EMAIL` | Sender address authorized by your SMTP server |
| `TRUST_PROXY` | `true` only behind the isolated reverse proxy supplied in Compose |

This version has a fixed limit of 100 files per folder. Accepted file extensions are configured through `ALLOWED_EXTENSIONS`. If you increase upload sizes, also adjust Caddy and ClamAV limits; see the operations guide.

Invitations generate a link to copy and share. They do not send email automatically. Without SMTP, the server administrator can reset a password using `manage.py changepassword`.

## Reliability

- **MFA:** enable an authenticator app from **Mon compte** and securely save the ten recovery codes.
- **Trash:** restore a file or folder for 30 days; permanently purging it frees the quota.
- **Resumable transfers:** reselect the same files after a pause or reload; confirmed blocks are retained for 24 hours.
- **Administration:** adjust quotas and inspect capacity, alerts and backup checks in **Exploitation**.
- **Backups:** `scripts/backup-and-verify.sh` creates a backup and proves it can be restored in isolated Docker volumes. Schedule it on your server and keep encrypted off-server copies.
- **Monitoring:** optional SMTP alerts and a bearer-protected `/ready/` endpoint; `/health/` remains a minimal liveness probe.

See [RELIABILITY.md](docs/RELIABILITY.md) for configuration, limits, failure behavior and reproducible load-test evidence. New settings include `MFA_ENCRYPTION_KEY`, `TRASH_RETENTION_DAYS`, `MIN_FREE_DISK_BYTES`, `MONITORING_TOKEN`, `MONITOR_EMAIL` and `BACKUP_MAX_AGE_HOURS`.

## Preview files before downloading

Enable previews when creating a link, or from **Mes liens** for an existing one. Recipients see image thumbnails and the first page of PDFs, and can open them in a large viewer before downloading the ZIP. JPEG, PNG, WebP, GIF and PDF files up to 20 MiB are eligible; other files retain their name, type and size.

Previews are reduced, metadata-free JPEGs generated by an isolated renderer. They honor passwords, revocation, expiry and download exhaustion, and never consume a download. **Visible previews can be copied.** Disable them when content should remain hidden until download. Existing links stay opted out after upgrading. See [preview limits and deployment](docs/PREVIEWS.md).

![Liasio gallery with image thumbnails, a PDF first page and a file without a preview](docs/screenshots/previews.png)

## Security and privacy

- **Argon2id** password hashing; account passwords require at least 12 characters. Optional share-link passwords have no minimum; all password forms accept at most 128 characters.
- No public registration; protected initialization and signed, expiring, single-use invitations.
- Ownership checks on every resource; web administrators cannot browse another account's files.
- Server-side sessions, HttpOnly/Secure cookies in production, CSRF protection, host validation, a restrictive CSP, and framing protection.
- HMAC-SHA256 access tokens that cannot be derived from database identifiers alone; hashed link passwords. Sharing tokens are not stored in plaintext in the database.
- Database-backed rate limits shared between workers; IP identities are HMAC-hashed for rate limiting and subsequently purged.
- Files stored outside the web root under random physical names; validated relative paths, extension restrictions, and size limits.
- Mandatory ClamAV scanning in the production stack; unexpected responses, malware detections, scanning limits, or service outages block uploads.
- Downloads served as attachments; HTML/SVG content is not previewed or executed.
- Activity history without IP addresses, geolocation, or advertising identifiers; CSV exports neutralize formula injection.
- Deletion revokes sharing links immediately; trash retains quota until permanent purge.
- TOTP secrets are encrypted; codes cannot be replayed, recovery codes are one-use, and pending logins expire.
- Resumable uploads reserve quota, verify block checksums and publish only after complete antivirus verification.

The operating-system administrator can read stored files: **this is not end-to-end encryption**. Use encrypted volumes and backups where needed. Antivirus scanning does not guarantee that a file is harmless. Extension checks do not fully validate the contents of every accepted format. This application is not designed for large-scale anonymous uploads.

Supporting guides are currently in French: [security policy](SECURITY.md), [security model](docs/SECURITY_MODEL.md), and [backup, restore, and maintenance](docs/OPERATIONS.md).

## Testing and contributing

```sh
uv sync --frozen
uv run --env-file .env python manage.py test --noinput
uv run --env-file .env python manage.py makemigrations --check --dry-run
uv run --env-file .env pip-audit --local
```

The [pre-release review summary](docs/SECURITY_REVIEW.md) records the automated source review, three corrected issues and regression evidence.

The test suite covers account isolation, upload limits, invitations, CSRF, link passwords, path traversal, deletion, and concurrent access. The GitHub workflow tests SQLite and PostgreSQL, builds the container image, and checks antivirus behavior using the EICAR test signature.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the code organization and [ROADMAP.md](ROADMAP.md) for planned improvements. Both documents are currently in French.

## Inspiration and scope

We reviewed [Pingvin Share](https://github.com/stonith404/pingvin-share), [ProjectSend](https://github.com/projectsend/projectsend), [Gokapi](https://github.com/Forceu/Gokapi), and [Send](https://github.com/timvisee/send). The [comparison](docs/COMPARISON.md) (French) describes their strengths and Liasio's design choices.

Liasio focuses on reusable folders, presentation, and explicit link rules in a compact application. This release does not aim to replace every feature in these more established projects. Liasio's application code and visual assets were created for this project; no code was copied from those four repositories.

### Upload preview

![Liasio upload page with a demonstration file selected](docs/screenshots/user-guide/en/upload.jpg)

## License

[MIT](LICENSE), copyright 2026 Filippo Maria Celi and contributors. Dependencies and container images retain their respective licenses. See [THIRD_PARTY.md](THIRD_PARTY.md).
