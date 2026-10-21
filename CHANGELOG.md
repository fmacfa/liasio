# Changelog

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
