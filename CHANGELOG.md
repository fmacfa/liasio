# Changelog

## Unreleased

- Rename the application to Liasio in French and English, including sharing pages, account e-mails, exports, monitoring messages, newly enrolled authenticator accounts and built-in guides.
- Use the approved geometric logo throughout the interface, sharing-page credit, favicon and home-screen icon. Custom workspace branding remains available.
- Preserve existing share signatures, MFA encryption keys, API v1 header names, deployment identifiers and repository URLs for upgrade compatibility.

## Unreleased

### Changed

- Share links can expire on a specific date or after 1–3,650 days. Download limits use a wheel-safe text field; optional link passwords have no minimum length (account requirements are unchanged). The API accepts `expires_in_days` and preserves the original deadline on an idempotent replay.
- Mixed file/folder drops and successive selections accumulate in one queue. New resumable manifests preserve selection order, and sender/recipient pages show expandable directories. Upload progress now includes real in-flight bytes, server-confirmed bytes, speed and estimated remaining time; scanning is a separate phase. Empty folders and hidden files are explicitly excluded.
- Branding supports an uploaded logo, background image and custom colors with a live preview and contrast-aware button text. Images are browser-optimized, strictly bounded/canonicalized PNGs, scanned and stored in the backed-up database (a separate 2.5 MiB/account ceiling). Brand media requires the same active, unlocked share link as the page. No native image decoder is added to the web process.

- First-time setup now starts directly with email and password, followed by name and password confirmation in a compact two-step form. No installation key is needed. Server-side validation, CSRF, throttling and atomic first-administrator creation remain enforced; setup closes permanently after initialization. English/French guides and screenshots cover the new flow.
- `SETUP_TOKEN` is no longer read or generated. Existing installations keep their accounts and remain closed to public signup. On a fresh instance, the first person to finish setup becomes the administrator; initialize it before sharing its address.

### Hardened

- Webhook scheduling rotates across accounts in shared database state, with one in-flight delivery per account. Compose runs two supervised processes, so one slow account cannot occupy both send slots. Five-minute crash leases, bounded attempts and receiver deduplication remain in place.
- Manual pause, overflow and account suspension cancel pending deliveries, including delayed retries; reactivation also clears legacy disabled queues. Pending MFA login challenges expire across suspension/reactivation.
- The upload client locks a private state directory, writes unpredictable exclusive temporary files atomically and retains source descriptors while checking for modification. Non-object upload JSON returns 400. Automatic trash purge rechecks retention under the owner lock.
- Production preview decoders use an additional Landlock filesystem boundary; unsupported hosts refuse previews. Runtime Docker contents are allowlisted and vendor images are pinned by digest. Releases test/export exact build digests, assemble and attest the index from those digests, and document workflow/tag/commit-bound attestation verification.

### Upgrade notes

- Migration `0007` adds branding fields and upload-part ordering only; back up first and restart all writers with the updated code. Existing files, passwords and expiry dates stay unchanged. Existing pending uploads retain their prior path ordering. Branding images are included in database backups.

- Back up the database, files and original keys. Stop webhook workers before migration `0006`, then restart the complete updated stack; do not run old and new dispatchers together. Use `deliver_webhooks --loop --workers 2` for a manual deployment. A single worker remains supported but waits for its current attempt.
- Production previews require Linux Landlock ABI 3+ (normally Linux 6.2+ with Landlock enabled). Original downloads remain available if this boundary is unavailable. macOS development uses the existing local decoder only in debug mode.
- Move upload-client state into a caller-owned directory with mode 0700 and keep source files in a trusted, unchanged tree. See the updated English/French automation guides. Existing server-side files and links are unchanged.
- Pinned image updates must be reviewed and tested; pulling an unchanged digest will not apply newer vendor fixes.

## 0.4.0 — 2026-10-22

### Added

- Scoped, expiring and revocable API keys for collection creation, resumable file/folder imports, metadata reads and share-link creation/revocation. Browser and API uploads share antivirus, path, quota and disk-admission checks.
- Encrypted 24-hour idempotency receipts for creation requests, owner isolation, rate limits and credential invalidation after password/MFA changes.
- Transactional outgoing webhooks for collection/file/link/open/download events, HMAC signatures, pinned public HTTPS destinations, a dedicated worker, bounded retries, pause/rotation and delivery history. No share capabilities or file contents in webhook payloads.
- Eleven illustrated user guides in both French and English, served from the same Markdown on GitHub and inside the application.
- Full French/English interface selection, including recipient pages, validation, email recovery templates, progress and previews. Business status codes are independent of translated labels.
- Dependency-free Python upload/resume client and receiver signature-verification example.

### Upgrade

- Back up `.env`, database and files; preserve encryption keys. The new migration only adds integration tables. Existing accounts and links keep their settings.
- Start the complete Compose stack, including the new `webhooks` worker. Prebuilt users should pull `app maintenance preview webhooks` with the same version.
- Manual deployments must run `deliver_webhooks --loop` and collect static assets, including documentation screenshots. Compiled translation catalogs are included.
- Webhooks accept public HTTPS port 443 only. Delivery is at least once, not exactly once; receivers must verify signatures and deduplicate event IDs.

## 0.3.0 — 2026-10-21

### Added

- Optional file galleries on sharing pages, with image thumbnails, a first-page PDF preview, keyboard navigation and a responsive lightbox.
- Per-link preview controls. New links offer previews by default; existing links remain opted out until their owner enables them.
- Reduced JPEG derivatives for JPEG, PNG, WebP, GIF and PDF files up to 20 MiB. Image metadata is removed; originals and download counters remain unchanged by previews.
- A networkless, read-only renderer container with no application secrets or source-file volume, bounded disposable decoder processes, and a private Unix socket.
- A private, regenerable cache bounded to 256 MiB / 256 entries, with expiry, purge cleanup and authorization checks before every response.

### Upgrade notes

- Use the updated Compose file, which adds the `preview` service and socket volume. Pull the same 0.3.0 image for `app`, `maintenance` and `preview`, then start the stack. The new migration defaults existing links to previews disabled.
- A visible preview can be copied. Preview access never consumes a download, including on single-use links. Disable previews for links that should reveal no content before download.
- See [file previews](docs/PREVIEWS.md) for supported formats, limits and deployment details.

## 0.2.0 — 2026-10-21

### Added

- TOTP authentication, encrypted authenticator secrets, replay prevention, ten one-use recovery codes and session invalidation.
- A 30-day configurable trash, file/folder restoration, confirmed permanent deletion and automatic expiry. Trashed files continue to use quota.
- Resumable, checksummed 4 MiB upload blocks with durable offsets, quota reservations, pause/resume and cancellation. Defaults increased to 1 GiB/file and 5 GiB/folder; 100-file limit retained.
- Operations page with quota editing, disk and antivirus health, backup verification freshness, MFA enrollment and deduplicated optional SMTP alerts.
- Protected JSON readiness endpoint and a five-minute maintenance heartbeat.
- Isolated Docker/PostgreSQL backup restoration checks, full file hashes, quota reconciliation and MFA-key verification.
- Reproducible concurrent HTTP load probe, PostgreSQL/ClamAV load evidence in CI, dependency auditing and static security checks.
- Versioned container publishing workflow with provenance and SBOM, and downloadable container archives attached to releases.

### Security fixes

- Password changes and resets lock and reload account state, reject changed authority and update only the password. Concurrent suspension, MFA and quota updates cannot be overwritten.
- Multipart reception admits account/disk capacity before parsing, checks actual received bytes and limits simultaneous classic uploads per account.
- ZIP generation and streaming hold a temporary disk reservation shared with upload admission; worker exit releases the live lock.
- See the [review and regression evidence](docs/SECURITY_REVIEW.md).

### Upgrade notes

- Back up the original `.env`, database and files. Preserve encryption/signing keys. See [the upgrade guide](docs/RELIABILITY.md#upgrade-from-01).
- Deletion now moves items to trash; quota is released only by permanent purge.
- Existing size overrides remain unchanged. Plain multipart requests remain limited to 500 MiB.
- This is an early release, not a certified security audit or an end-to-end encrypted service.

## 0.1.0 — 2026-10-21

Initial public MIT implementation: invited accounts, reusable file collections, customized sharing pages, password/expiry/download limits, dated events, storage management, Docker deployment, antivirus and English/French READMEs.
