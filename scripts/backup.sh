#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
backup_dir="backups/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$backup_dir"
running_services=()
while IFS= read -r service; do
  case "$service" in app|maintenance|webhooks|mail) running_services+=("$service");; esac
done < <(docker compose ps --status running --services)
restart_services() {
  if ((${#running_services[@]})); then docker compose start "${running_services[@]}"; fi
}
trap restart_services EXIT
if ((${#running_services[@]})); then docker compose stop -t 330 "${running_services[@]}"; fi
docker compose exec -T db pg_dump -U linkvault --no-owner --no-acl linkvault > "$backup_dir/database.sql"
docker compose run -T --rm --no-deps app python manage.py export_blobs > "$backup_dir/files.tar.gz"
python3 scripts/backup_manifest.py create "$backup_dir"
printf 'Backup created in %s. Encrypt it and keep .env separately.\n' "$backup_dir"
