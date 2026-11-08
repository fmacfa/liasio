# Zu Liasio beitragen

[English](CONTRIBUTING.md) · [Français](CONTRIBUTING.fr.md) · **Deutsch**

Reichen Sie reproduzierbare Fehlerberichte und klar abgegrenzte Änderungen ein. Melden Sie Schwachstellen vertraulich gemäß [SECURITY.de.md](SECURITY.de.md). Beiträge stehen unter MIT; ein CLA ist nicht erforderlich.

## Entwicklung und Prüfung

Befolgen Sie die lokale Einrichtung im README, verwenden Sie fiktive Dateien und halten Sie `.env`, Zugangsdaten und private Daten aus Git heraus. Abhängigkeiten sind mit uv festgeschrieben. Anwendungs-JavaScript wird selbst gehostet und benötigt keinen Frontend-Build.

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

Verwenden Sie für Zeilensperren und gleichzeitige Zugriffe eine entbehrliche PostgreSQL-Datenbank mit Berechtigung zum Erstellen der Testdatenbank. S3-/OIDC-Mocktests nutzen Moto/responses; echte Browsertests laufen separat. Releaseimages enthalten keine Entwicklungsabhängigkeiten und überspringen nur diese optionalen Mock-/Vertragstests; CI führt sie mit der vollständigen Entwicklungsumgebung aus. Integrations-CI prüft außerdem ClamAV, den isolierten Renderer, gemessene Uploads und eine vollständige Sicherungswiederherstellung. Der restic-Integrationstest verwendet ein entbehrliches lokales verschlüsseltes Repository, niemals das eines Betreibers.

## Architektur und feste Regeln

- `vault/access.py`, `workspaces.py`, `team_access.py`: ausdrückliche Eigentums-/Gruppenrechte. Administration gewährt keinen Dateizugriff. Berechtigungen bei Änderungen und vor Wiederholung von API-Antworten erneut prüfen.
- `storage.py`, `resumable.py`, `versions.py`, `file_requests.py`: unveränderliche Originale, begrenzte Schreibvorgänge, Prüfsummen, Viren-/Kontingentprüfung, Momentaufnahmen und öffentliche reine Uploadberechtigungen.
- Sperrreihenfolge: Arbeitsbereiche, geordnete Konten, Sammlung, abhängige Zeilen. Uploadzulassung sperrt Kapazität und dann Instance vor diesen Sperren. Niemals Kapazität anfordern, während eine Kontosperre gehalten wird. E-Mail-/Webhook-Zustellung gibt Datenbanksperren vor Netzwerkzugriffen frei.
- Sitzungsbasierte Änderungen behalten CSRF-Schutz. Nur ausschließlich per Bearer autorisierte API-v1-Routen sind davon ausgenommen; Cookies autorisieren niemals API-Aufrufe. HTML bleibt maskiert, native Vorschauen isoliert und Original-Blob-URLs privat.
- `mail.py`, `recipients.py`, `oidc.py`, `object_storage.py`: ausdrückliche Freigabe, aktuelle Berechtigungen, begrenzte Wiederholungen, bestätigte Identitäten und privaten Speicher beachten. Keine Tokens, Codes oder privaten URLs protokollieren.
- Aus Downloadautorisierung oder SMTP-Annahme keinen vollständigen Empfang ableiten. Sichtbare Produktgrenzen sowie Migrations- und Rückkehranweisungen korrekt halten.

## Dokumentation und Übersetzung

Die UI-Quelltexte sind Französisch; Englisch-/Deutsch-Kataloge liegen in `locale/{en,de}/LC_MESSAGES/`. Verwenden Sie Django-Übersetzungshilfen und stabile, unübersetzte Fach-/API-Kennungen. JavaScript nutzt `gettext`/`interpolate` über `/jsi18n/`.

```sh
uv run --env-file .env python manage.py makemessages -l en -l de --ignore=.venv --ignore=.local --ignore=staticfiles --ignore=vault/migrations --ignore=vault/tests
uv run --env-file .env python manage.py makemessages -l en -l de -d djangojs --ignore=.venv --ignore=.local --ignore=staticfiles
uv run --env-file .env python manage.py compilemessages -l en -l de --ignore=.venv --ignore=.local
uv run python scripts/build_openapi.py
uv run python scripts/check_documentation.py
```

Fügen Sie `.po` und `.mo` ohne leere oder unscharfe Einträge bei. Anleitungen in `docs/guide/{fr,en,de}` erscheinen direkt in Anwendung und GitHub; aktualisieren Sie alle Sprachen und echte Bildschirmaufnahmen mit fiktiven Daten. Verwenden Sie zum neuen Funktionshandbuch passende Aufnahmen. Prüfen Sie Desktop-/Mobilansicht, Beschriftungen, Tastaturbedienung und reduzierte Bewegung. Der OpenAPI-Build muss zum eingecheckten Vertrag und den implementierten Routen passen.

Die GitHub-Einstiegsseite und technischen Referenzen sind standardmäßig Englisch. Bewahren Sie die französischen Fassungen und halten Sie die Sprachverweise der drei READMEs, Richtlinien, Videoverzeichnisse und Benutzerhandbücher konsistent.
