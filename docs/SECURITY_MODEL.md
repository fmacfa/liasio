# Liasio 0.3 security model

**English** · [Français](SECURITY_MODEL.fr.md) · [Deutsches Kontosicherheitshandbuch](guide/de/account.md)

<a id="frontières-de-confiance"></a>
## Trust boundaries

- The browser and uploaded files are untrusted. Titles, descriptions and names remain escaped text.
- Members may administer their own resources. Access to another account's resources requires an explicit owner grant, never merely guessing their UUIDs.
- Application administrators invite/suspend accounts and see storage usage. Library routes check ownership or an explicit Reader/Collaborator grant.
- Server administrators and backup operators can read files and the database. Disk/backup encryption is a hosting responsibility; no browser-side encryption secret is implemented.
- The reverse proxy is trusted only when isolated as in Compose. It replaces `X-Real-IP` and `X-Forwarded-Proto`. Never expose the Gunicorn port with `TRUST_PROXY=true`.

<a id="comptes"></a>
## Accounts

On a fresh instance, the first email/password registration creates the administrator without an installation key. Anyone with access to the uninitialized instance can claim this account. The operator must complete the wizard before distributing the address or opening access to others. This registration does not verify control of the email inbox. A pre-created, locked singleton prevents concurrent administrator creation. The existence of an account or initialized state closes the wizard, even after deletion of the last account. CSRF, rate limits and password validation remain active. The two steps submit credentials only at final confirmation; no password is retained in browser storage.

Invitations bind an administrator-selected email address to a signed capability. Acceptance consumes the row in a transaction. They do not prove inbox control if the administrator gives the link to the wrong person. Dedicated email verification is planned in the roadmap.

Passwords use Argon2id through Django. SMTP reset uses Django tokens, valid for one hour and invalidated by a password change. A change invalidates other sessions; suspension also deletes sessions and revokes links. The last administrator cannot be suspended or deleted through the member screens.

<a id="liens"></a>
## Links

A capability is the link UUID accompanied by a purpose-bound HMAC-SHA256 (sharing or invitation), calculated with `SECRET_KEY`. Knowing the database alone does not reconstruct links. Knowing both the key and database does: protect them together. Key rotation invalidates old links and invitations.

Files and metadata behind a password-protected link are visible only after unlocking. Session permission expires after one hour. Link state and owner activity are checked again at download.

The ZIP is created on disk, then an atomic conditional update reserves one use and writes the event in the same transaction. A missing file consumes no use. If the browser subsequently abandons the transfer, that use remains consumed. Two concurrent requests cannot obtain the same single-use authorization.

Revocation and reservation have an atomic database ordering: if revocation wins, the download is blocked; if reservation has already won, the transfer may continue. Adding files is blocked while an active link exists. Deleting files revokes all non-revoked links for the affected collections.

<a id="fichiers-et-disponibilité"></a>
## Files and availability

Limits apply at multiple levels: proxy, request body, chunk reception, file, collection, file count and locked user quota. Unauthenticated uploads, or uploads without Collaborator access to the collection, are rejected before multipart parsing.

The extension is an initial rule, not proof of actual type. Antivirus scans the bytes. The application never extracts archives. Downloads use `application/octet-stream` and `attachment`; no active user content executes. Version 0.4 does not fully validate the semantics of every accepted format.

ClamAV uses INSTREAM on a private Docker network. Any response other than `stream: OK` blocks saving. Daemon configuration reports exceeded limits and encrypted archives. The application marks a file scanned only after an accepted result.

Temporary ZIPs are bounded by collection size (5 GiB by default). Each request needs a temporary-space reservation before creation, retained until the transfer closes. A shared POSIX lock serializes admission on the same host and accounts for other ZIPs, classic uploads and resumable reservations. Classic uploads check quota and capacity before parsing and during reception. Per-IP rate limits and server limits reduce abuse without replacing DDoS protection. There is no global bandwidth budget or archive-generation queue yet.

SQL deletion and physical deletion do not form a distributed transaction. Blobs are removed after SQL commit; a system failure can leave an inaccessible blob. Maintenance deletes orphan blobs older than 24 hours. After an upload crash, the same mechanism recovers files written before a rollback.

<a id="vie-privée-et-logs"></a>
## Privacy and logs

The activity log retains event type and time, not IP addresses, browser identity or a claimed recipient identity. Cookies support sessions, CSRF, unlocking and deduplication of opens. They are not a reliable measure of human audience.

Rate-limit keys contain a time-windowed HMAC of the IP or login address and are purged. Histories are purged after 90 days by default. Download counters remain to enforce limits. Files persist until explicitly deleted; link expiry does not delete its library.

Caddy/Gunicorn access logs are disabled by default. Django logs redact sharing, invitation and recovery URLs. Do not add request, body, cookie or environment-variable logging in production. Outbound links send no referrer; the `same-origin` policy retains CSRF checks within the application.

<a id="vérifications"></a>
## Verification

Automated tests cover permissions, passwords, invitations, quotas, CSRF, paths, no consumption on GET/HEAD, revocation, concurrency, deletion and CSV exports. ClamAV protocol tests cover clean, malicious and invalid responses; CI includes an EICAR integration test.

These tests replace neither an independent audit, version monitoring nor load testing appropriate to your hosting.

<a id="protection-et-fiabilité-ajoutées-en-02"></a>
## Protection and reliability added in 0.2

TOTP uses a Fernet-encrypted key, non-reusable time steps and hashed, single-use recovery codes. Password login creates only a five-minute provisional session. MFA changes revoke other sessions.

Deleted files remain private in trash and count toward quota. Restoration never reactivates links. Chunked uploads reserve quota under lock, check hashes and publish the batch only after antivirus scanning. Temporary chunks expire after 24 hours. Trash filenames remain reserved.

Monitoring exposes metrics only on a Bearer-token route; email alerts require explicit configuration. Backups are restored into an isolated instance to check bytes, counters and MFA keys. See [RELIABILITY.md](RELIABILITY.md) for operational assumptions and limits.

The [pre-0.2 code review](SECURITY_REVIEW.md) describes fixes and their tests.

<a id="aperçus-ajoutés-en-03"></a>
## Previews added in 0.3

The gallery is optional per link and disabled for links created before migration. Each derivative is authorized before cache access and after rendering: password, file membership, account state, trash, revocation, expiry and download count. Viewing a preview consumes no download; displayed content can still be copied.

The preview route never returns originals. Pillow and PDFium produce a reduced, metadata-free JPEG in a container without network, secrets or file volume, with a read-only root. Renderer processes, cache, dimensions, sizes and durations are bounded. The [preview guide](PREVIEWS.md) explains these limits and local development mode, which lacks Docker isolation. This new surface requires native decoders to be kept up to date.

<a id="automatisations-ajoutées-en-04"></a>
## Automations added in 0.4

The API accepts only an explicit Bearer header, never session cookies. Every key belongs to an account, has permissions and expiry, and is stored only as an HMAC digest. Writes recheck the key under the same user lock as revocation. Password/MFA changes invalidate existing keys; suspension revokes them permanently and pauses webhooks. Uploads reuse the interface's file, antivirus and quota checks. Idempotency receipts are encrypted, bounded and removed after 24 hours.

Webhooks transmit only identifiers and event type. URLs and signing secrets are encrypted with the instance encryption key. Each attempt resolves and checks every DNS address, pins a public IP for TLS with the original hostname, forbids redirects and ignores environment proxies. The dedicated process has PostgreSQL and outbound HTTPS access without a file volume. HMAC signatures, timestamps and identifiers let recipients verify origin and reject replays; they must implement those checks. Queues, durations, attempts and retention are bounded. See the [API reference](API.md) for the contract and limits.

In-application guides come only from versioned repository Markdown with raw HTML disabled. Translations cover the interface, not users' content.

## Branding images

Branding has a separate bounded image path from document previews. The browser converts selected PNG/JPEG/WebP images into small RGB/RGBA PNGs. The server validates the PNG signature, chunk bounds and CRCs, fixed 8-bit RGB/RGBA non-interlaced encoding, scanline filters and exact decompressed length using a bounded zlib inflater. It rejects unknown critical chunks, corrupt/trailing data and excess dimensions/bytes; ancillary chunks are discarded and scanlines recompressed. No Pillow/PDF/native image decoder is invoked by this path. Production antivirus scans the canonical result before saving. SVG, remote image URLs and arbitrary CSS are not accepted.

Authenticated branding POSTs require CSRF, are limited to 30/hour/IP and 3 MiB/request, and reserve temporary disk space before parsing. Stored logos are capped at 512 KiB/512 pixels per side, backgrounds at 2 MiB/1,600 pixels per side. This separate 2.5 MiB/account allowance is shown on Storage and included in database backups. Images and generated styles are served without cache, only to their owner or through an active, unlocked share capability. Expiry, revocation, exhaustion and owner suspension apply on every media request. Hex colors are validated and rechecked before emitting a stylesheet under the existing CSP; no inline-style exception is needed.

## Interactive batches and spreadsheet lists

Browser batch preparation, reads, completion, rename, deletion and export require an authenticated Owner or Collaborator session. Browser writes retain CSRF and MFA session checks. The API separately accepts only Bearer keys, with `links:write` for creation and `read` for list/detail/export; cookies never grant API access. Completion takes the user, collection and batch locks in that order, rechecks active accounts and current permissions, live files and antivirus state, and commits all links, events, webhook deliveries and list associations together. Repeating a completed draft UUID returns the original batch without new links. One shared password is hashed once per submission, outside the locks; its clear text is never stored in the draft or exported. Renaming touches only the private label. Removing a list deletes its source data and associations while deliberately retaining its links; revocation remains a separate explicit action.

Imports accept up to 1 MiB, with a 1.125 MiB HTTP boundary and a 3 MiB temporary-disk reservation before multipart parsing. Limits apply per IP and per account, including ten preparations/hour/account. A list has at most 200 data rows, 20 columns, 1,000 characters/cell and 256 KiB of normalized text. At most 100 saved batches per account use a separate database allowance, outside file quota. Drafts expire after 24 hours; completed lists persist until removed and follow database backup retention. Maintenance locks and rechecks expired drafts before deletion.

XLSX is untrusted ZIP/XML. Preflight rejects encryption, binary/macro payloads, external-workbook members, duplicate/traversal names and unsupported compression. It bounds the archive to 64 members, 8 MiB expanded total and 4 MiB per member; actual bounded reads and CRC validation precede parsing. All XML members pass defusedxml with DTDs/entities prohibited, depth capped at 32 and a global 50,000-element limit. Worksheet coordinates are bounded; formulas are rejected. openpyxl then reads the first worksheet in read-only mode with external workbook links disabled and explicit row/column bounds, ignoring claimed worksheet dimensions. Production antivirus must accept the list before openpyxl reads it. The library's [security guidance](https://openpyxl.readthedocs.io/en/stable/#security) calls for defusedxml; both dependencies are locked and audited in CI.

Exports construct a new workbook from plain values, never reuse the imported ZIP, and explicitly mark cells as strings. Only server-generated Liasio URLs become XLSX hyperlinks. CSV uses quoted cells and prefixes formula-like or leading control values with an apostrophe. Imported names/values are escaped in HTML and in JSON script data; no source strings become executable JavaScript. Lists and exports use private no-store responses, never appear on share pages, and are not included in webhook payloads.

API batch creation accepts at most 256 KiB of JSON and 200 single-line names (80 characters each), or an integer quantity. It reuses the same completion and export code, saved-batch ceiling and preparation/export budgets as the browser. Password hashing happens between two short authorization checks, outside transactions; token validity, ownership, quotas and conditions are rechecked under the owner/collection locks before committing the batch and encrypted receipt. Replay is checked before quota/rate/date validation and again under the final lock. A failed transaction rolls back the entire batch and its webhook deliveries/receipt. List responses are paginated summaries; details and exports require ownership or Collaborator access, exclude trashed collections and never bypass rate limits. No CORS credential exposure or incoming unauthenticated webhook is introduced. The example CLI refuses redirects and existing export paths, writes downloads with mode 0600 and reads secrets from the environment.

API keys can expire in 1–365 days or explicitly have no expiry. Non-expiring keys count toward the limit of ten non-expired/non-revoked keys and remain subject to permissions, revocation and credential version (password/MFA). Maintenance retains active non-expiring keys and purges keys expired or revoked for more than 30 days. Only a signed-in owner can view a full webhook URL in its detail page, with `Cache-Control: no-store, private`; events never include it. Suggested commands never embed an API key.

<a id="partage-des-dossiers-entre-membres"></a>
## Sharing collections between members

Collections remain private by default. Only their owner can grant, change or remove Reader/Collaborator access through a session-authenticated, CSRF-protected POST. Administrator status does not bypass this boundary. Readers can inspect files and download an authenticated ZIP without accessing links, batches, recipients or histories. Collaborators can add files, edit presentation and manage links/batches, but cannot delete files/collections or change the owner's permissions, accounts, secrets or branding.

Mutations lock actor/owner accounts in identifier order, then the collection, and recheck permission and API credentials under those locks. Role changes use the same order. Member downloads recheck access and the file list after ZIP preparation; they do not change recipient counters. A previously authorized transfer cannot recall bytes already transmitted.

Storage, quota, the five-pending-session limit and the hundred-retained-batch limit remain attached to the owner. Sessions add a separate initiator and can be controlled only by that initiator or the owner. Removing Collaborator access cancels that actor's pending collection sessions and releases reserved quota. Events record the actual actor; webhooks belong to the collection owner. API reads/resumes and creation receipts remain subject to the current role. Existing public links remain accessible under their own conditions and must be revoked separately.

<a id="compléments-05"></a>
## Additions in 0.5

Group access requires a shared workspace. Group managers choose members but do not automatically receive files. Changes lock the workspace, then accounts in a stable order, then the collection. Transfers move the complete quota (including versions and trash) and cancel pending uploads. Existing sessions are inventoried during migration; a team's MFA policy also applies to API keys.

Versions freeze file bytes; following links explicitly accept changes. Public links can combine an opaque capability, password and session-bound email code. Client deposits grant no read access, use lifetime quotas and pass through the same antivirus checks. Distribution emails require review/confirmation; deliveries recheck permissions and expire, with a per-sender limit.

OIDC requires prior explicit linking, state/nonce/PKCE and a signed token for the expected issuer/audience; matching email addresses never link accounts automatically. S3 originals stay private without direct URLs. Encrypted restic backups are optional and require external configuration and a restoration test. The [independent audit brief](INDEPENDENT_AUDIT.md) details boundaries to examine; no independent human audit is claimed.
