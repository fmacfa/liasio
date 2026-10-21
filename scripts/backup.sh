#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
backup_dir="backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup_dir"
docker compose stop app maintenance
trap 'docker compose start app maintenance' EXIT
docker compose exec -T db pg_dump -U linkvault --no-owner --no-acl linkvault > "$backup_dir/database.sql"
docker compose run -T --rm --no-deps --entrypoint tar app -czf - -C /data files > "$backup_dir/files.tar.gz"
test -s "$backup_dir/database.sql"
tar -tzf "$backup_dir/files.tar.gz" > /dev/null
printf 'Sauvegarde créée dans %s. Chiffrez-la et sauvegardez .env séparément.\n' "$backup_dir"
