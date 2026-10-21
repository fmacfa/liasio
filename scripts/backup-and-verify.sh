#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Use the exact newly created directory, never guess by directory ordering.
marker="$(mktemp)"
trap 'rm -f "$marker"' EXIT
scripts/backup.sh | tee "$marker"
backup_dir="$(sed -n 's/^Backup created in \(backups\/[^ ]*\)\. Encrypt.*$/\1/p' "$marker")"
test -n "$backup_dir"
scripts/restore-test.sh "$backup_dir"
