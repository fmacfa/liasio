#!/usr/bin/env bash
# Always restore into newly named, isolated volumes. Never replaces live data.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
if (($# != 1)); then printf 'Usage: scripts/restore-test.sh backups/DATE\n' >&2; exit 2; fi
backup_path="$(cd "$1" && pwd)"
python3 scripts/backup_manifest.py verify "$backup_path"
restore_project="linkvault-restore-$(date -u +%s)-$$"
cleanup() { docker compose -p "$restore_project" down --volumes --remove-orphans; }
trap cleanup EXIT
docker compose -p "$restore_project" up -d --wait db
docker compose -p "$restore_project" exec -T db psql -v ON_ERROR_STOP=1 -U linkvault linkvault < "$backup_path/database.sql"
docker compose -p "$restore_project" run -T --rm --no-deps --entrypoint tar app -xzf - -C /data < "$backup_path/files.tar.gz"
docker compose -p "$restore_project" run -T --rm --no-deps -e STORAGE_BACKEND=local app python manage.py migrate --noinput
docker compose -p "$restore_project" run -T --rm --no-deps -e STORAGE_BACKEND=local app python manage.py verify_storage
# The source instance is changed only after the isolated restore passed.
snapshot_time="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["created_at"])' "$backup_path/checksums.json")"
docker compose run -T --rm --no-deps app python manage.py record_backup_check --snapshot-created-at "$snapshot_time"
printf 'Isolated restore passed. The temporary instance will be removed.\n'
