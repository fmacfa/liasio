# Encrypted off-server backups and restoration

The local backup command remains `scripts/backup-and-verify.sh`. It briefly stops all application writers, dumps PostgreSQL and exports database-referenced originals plus pending upload blocks into `files.tar.gz`, then tests restoration in isolated Docker volumes. This works for local and S3 originals. Local archives are private **but not encrypted**; restrict access and retention.

The optional `scripts/offsite_backup.py` sends a verified snapshot and its original `.env` into an encrypted [restic repository](https://restic.readthedocs.io/en/stable/040_backup.html). The repository must be on another host/provider for disaster recovery. Restic encrypts before upload. Keep its password and an emergency recovery copy separately from the VPS; loss of the password makes the repository unrecoverable.

## Configure and test

Install a supported restic CLI on the backup host. Create a private password file with a long generated password (mode `0600`, minimum 16 bytes). In a private environment file, configure the repository, password file and provider credentials. Example variable names:

```dotenv
RESTIC_REPOSITORY=sftp:backup-user@backup.example.com:/srv/backups/liasio
RESTIC_PASSWORD_FILE=/etc/liasio-backup.password
RESTIC_HOST=liasio-production
```

Use a dedicated SFTP account with pinned SSH host keys, or a private S3/restic backend with TLS and least-privilege credentials. Load these values in the shell without printing them. Do not put a password directly in `RESTIC_REPOSITORY`. The application S3 bucket and backup repository should be separate.

```sh
python3 scripts/offsite_backup.py init
python3 scripts/offsite_backup.py backup --keep-daily 7 --keep-weekly 4 --keep-monthly 6
python3 scripts/offsite_backup.py check
```

`backup` creates and restore-tests a fresh local backup, includes `config.env` inside the encrypted snapshot, checks repository metadata and applies retention/pruning only to this host's `liasio` tag. `check` reads all stored data and can take time. Save the returned snapshot ID. The defaults retain seven daily, four weekly and six monthly snapshots according to restic's retention rules, not a fixed number of copies. Backup files and config must come from the same installation.

`--source /verified/backup` reuses an already tested snapshot; it checks archive hashes but does not repeat the Docker restore test. `--allow-local-test` exists for disposable integration tests and is not off-server protection. Retention affects remote snapshots; local `backups/` retention remains an operator task. Failed backup/check commands never run the retention step.

## Schedule on Linux

Review `docker/liasio-backup.service` and `.timer`: adjust `/opt/liasio` to the checkout and create `/etc/liasio-backup.env` with mode `0600`. The service account needs Docker and backup-repository access. Install the files into `/etc/systemd/system/`, reload systemd and enable the timer. The sample runs daily around 03:30 server time and may briefly interrupt new transfers. Inspect timer/service failures and Operations; scheduling is not enabled automatically by installation.

## Restore onto another empty VPS

1. Obtain the verified release/commit, restic credentials and repository password through secure channels. Prepare an empty target directory and sufficient local disk for both the archive and restored bytes.
2. Run `python3 scripts/offsite_backup.py restore --snapshot EXACT_ID --target /srv/liasio-recovery`. The target must be empty; the command verifies restored file/config checksums and never changes live services.
3. Copy the restored `config.env` to the new release checkout as `.env` (mode `0600`). Keep original signing/encryption keys. Set `DOMAIN`/`APP_URL` for the intended recovery domain and use `STORAGE_BACKEND=local` for the first restore, even when the source used S3. Do not connect the recovery instance to the production bucket.
4. Run the database/file restore procedure in [OPERATIONS.md](OPERATIONS.md#restaurer-sur-une-installation-vide) on **empty volumes**, using the restored `database.sql` and `files.tar.gz`. Run migrations, then `python manage.py verify_storage` before starting public traffic.
5. Initially keep `mail` and `webhooks` stopped to avoid replaying queued production messages. Verify an account, a historical link, permissions, quotas, a versioned file and an antivirus-protected test upload. Decide which pending messages to cancel before starting workers.
6. If moving back to S3, copy and verify objects using [the storage migration procedure](ADVANCED_HOSTING.md). Change DNS only after the recovery instance passes validation. Old-domain links require a controlled redirect or restoring the original domain.

Test this procedure periodically. A successful checksum or repository check alone is not proof that accounts, keys, database and files restore together. No independent human disaster-recovery exercise is claimed by the automated tests.
