# Upgrade to Liasio 0.5

[English README](../README.md) · [README français](../README.fr.md)

This release includes the changes made on `main` after 0.4.0. The registry is now `ghcr.io/fmacfa/liasio`. Existing `linkvault` Compose project names, volume names, environment variables, Python package paths, API headers and webhook signatures are deliberately retained. Do not rename your Compose project or volumes during this upgrade.

## Before changing an existing installation

1. Record the running commit/image digest and Compose project name. Preserve the original `.env`, `SECRET_KEY` and `MFA_ENCRYPTION_KEY`; changing keys invalidates capabilities or prevents decryption.
2. Run `scripts/backup-and-verify.sh` with the current release. Keep its database, file archive and checksums together, plus a secure copy of `.env`. Validate the restore before continuing.
3. Stop **all writers**: `docker compose stop -t 330 app maintenance webhooks mail` (omit `mail` when the previous Compose file does not define it).
4. Check out the verified `v0.5.0` sources and use its Compose file. Set `LINKVAULT_IMAGE=ghcr.io/fmacfa/liasio:0.5.0`, preferably the immutable reference in the release's `IMAGE-DIGEST.txt` after verifying its attestation.
5. Pull the same image for every application service: `docker compose pull app maintenance preview webhooks mail`.
6. Run `docker compose run --rm --no-deps app python manage.py migrate --noinput`, then `docker compose run --rm --no-deps app python manage.py collectstatic --noinput`.
7. Start `docker compose up -d --no-build`. Check `docker compose ps`, `check --deploy`, Operations, the mail/webhook heartbeats, a test login, an old recipient URL, a resumed upload and a new link.

Source builds use `docker compose build app` after checkout and the same migration/restart sequence. Never run old and new writers concurrently.

## Data and behavior changes

- Migrations `0012`–`0017` add workspaces/groups, presets, revisions, session inventory, recipient codes, email queues, file requests and OIDC identities. Migration `0013` changes file-path uniqueness to apply to current versions. Earlier migrations from 0.4 are applied automatically too.
- Existing folders stay private. No existing account is silently added to a group, and administrator status never grants file access.
- Existing recipient links keep their URL, password, expiry and counter. Legacy unversioned links still prevent file changes until the owner/collaborator explicitly uses **Versions → Freeze existing links**, or revokes them.
- New links default to a frozen file version. **Follow future updates** is explicit. Replacements keep old bytes and their quota cost while a version refers to them.
- API creations remain idempotent for 24 hours; permission removal invalidates replay access. Extra response fields are additive. Original bearer credentials and webhook headers are unchanged.
- Emails are disabled until SMTP is configured. Existing invitations remain copyable. The `mail` service is new; approval is required for sharing and invitation emails. OTP emails are sent when a visitor requests a code, and notifications only after opt-in.
- File requests are opt-in, upload-only capabilities. No public account registration is added. Production ingestion still requires antivirus; do not disable it to fix a deployment issue.
- S3 and OIDC remain disabled by default. Read [advanced hosting](ADVANCED_HOSTING.md) before enabling them. S3 does not eliminate local staging/ZIP storage.
- Container memory ceilings now total 6.75 GiB, excluding host/Caddy/build/backup overhead. Preserve the existing no-preview configuration on kernels without Landlock ABI 3+; no reboot is required to use original downloads.

## Recovery

If migration or validation fails, keep traffic stopped and investigate on a copy. A rollback to 0.4 requires restoring its **matching database, files, keys and code** into an empty isolated installation. Do not point an old application at the upgraded schema or revert only the container image. Once a short URL has been issued, any rollback build serving it must retain the short-code decoder/index.

The release remains pre-1.0. Read the [independent audit brief](INDEPENDENT_AUDIT.md) and [maintenance policy](MAINTENANCE.md).
