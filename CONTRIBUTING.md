# Contributing to Liasio

**English** · [Français](CONTRIBUTING.fr.md)

Submit reproducible issues and focused changes. Report vulnerabilities privately through [SECURITY.md](SECURITY.md). Contributions are under MIT; no CLA is required.

## Development and validation

Follow the local README setup, use fictional files and keep `.env`, credentials and private data out of Git. Dependencies are locked with uv; application JavaScript is self-hosted and requires no frontend build.

```sh
uv sync --frozen
uv run ruff check vault linkvault scripts
uv run ruff format --check vault linkvault scripts
uv run --env-file .env python manage.py collectstatic --noinput
uv run --env-file .env python manage.py test --noinput
uv run --env-file .env python manage.py makemigrations --check --dry-run
uv run --env-file .env pip-audit --local
uv run python scripts/check_documentation.py
node --test scripts/tests/*.test.cjs
uv run playwright install chromium
uv run --env-file .env python manage.py test scripts.browser_tests --noinput
```

Use a disposable PostgreSQL database with permission to create its test database for row-lock/concurrency coverage. Mock S3/OIDC tests use Moto/responses; real browser tests run separately. Release images omit development dependencies and skip only those optional mock/contract tests; CI runs them with the full development environment. Integration CI also exercises ClamAV, the isolated renderer, measured uploads and a full backup restore. The restic integration test uses a disposable local encrypted repository, never an operator repository.

## Architecture and invariants

- `vault/access.py`, `workspaces.py`, `team_access.py`: explicit ownership/group grants. Administration does not grant file access. Recheck authorization inside mutations and before replaying API responses.
- `storage.py`, `resumable.py`, `versions.py`, `file_requests.py`: immutable originals, bounded writes, checksums, scan/quota enforcement, snapshots and public upload-only scopes.
- Lock order: workspace(s), ordered accounts, collection, dependent rows. Upload admission takes capacity then Instance before those locks. Never acquire capacity while holding an account lock. Mail/webhook delivery releases DB locks before network I/O.
- Session-based mutations retain CSRF. Only bearer-only API v1 routes are CSRF-exempt; cookies never authorize API calls. HTML remains escaped, native previews isolated and original blob URLs private.
- `mail.py`, `recipients.py`, `oidc.py`, `object_storage.py`: respect explicit approval, current access, bounded retries, verified identities and private storage. Never log tokens, codes or private URLs.
- Do not claim completed receipt from a download authorization or SMTP acceptance. Keep user-visible limitations and migration/rollback instructions accurate.

## Documentation and localization

Source UI strings are French; English/German catalogs are in `locale/{en,de}/LC_MESSAGES/`. Use Django translation helpers and stable untranslated business/API identifiers. JavaScript uses `gettext`/`interpolate` through `/jsi18n/`.

```sh
uv run --env-file .env python manage.py makemessages -l en -l de --ignore=.venv --ignore=.local --ignore=staticfiles --ignore=vault/migrations --ignore=vault/tests
uv run --env-file .env python manage.py makemessages -l en -l de -d djangojs --ignore=.venv --ignore=.local --ignore=staticfiles
uv run --env-file .env python manage.py compilemessages -l en -l de --ignore=.venv --ignore=.local
uv run python scripts/build_openapi.py
uv run python scripts/check_documentation.py
```

Include `.po` and `.mo` files with no empty/fuzzy entries. Guides in `docs/guide/{fr,en,de}` are rendered directly in the app and on GitHub; update all languages and real screenshots with fictional data. Use the new feature guide's screenshots, not unrelated placeholders. Validate desktop/mobile layout, visible labels, keyboard use and reduced-motion preferences. The OpenAPI build must match the checked-in contract and implemented routes.
