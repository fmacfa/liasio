#!/bin/sh
set -eu
umask 077
if [ "${1:-}" = "gunicorn" ]; then
  python manage.py check --deploy --fail-level ERROR
  python manage.py migrate --noinput
  python manage.py collectstatic --noinput
fi
exec "$@"
