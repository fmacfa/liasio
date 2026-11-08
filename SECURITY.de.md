# Sicherheitsrichtlinie

[English](SECURITY.md) · [Français](SECURITY.fr.md) · **Deutsch**

Die zuletzt veröffentlichte Version 0.5 erhält Fehlerbehebungen; ältere Nebenversionen sollten aktualisiert werden. Das Projekt befindet sich vor Version 1.0 und hat noch kein unabhängiges menschliches Sicherheitsaudit durchlaufen. Siehe [Wartungs- und Veröffentlichungsrichtlinie](docs/MAINTENANCE.md) (Englisch).

## Vertrauliche Meldung

Nutzen Sie [GitHubs vertrauliche Schwachstellenmeldung](https://github.com/fmacfa/liasio/security/advisories/new). Eröffnen Sie keine öffentlichen Issues mit Zugangsdaten, echten Freigabe-URLs, Kundendateien oder Angriffsbeschreibungen gegen eine laufende Installation. Nennen Sie die unveränderliche Version bzw. den Commit, eine isolierte Reproduktion, erwartetes und beobachtetes Verhalten sowie die Auswirkungen. Prüfen Sie nur Systeme, für die Sie eine Berechtigung besitzen. Es gibt keine garantierte Antwortfrist.

## Sensible Grenzen

Prüfbereiche sind Einrichtung, Einladungen, Passwortwiederherstellung, MFA/OIDC, Sitzungswiderruf, Gruppen- und Einzelberechtigungen, Eigentumsübertragung, reine Uploadberechtigungen, Pfadmanipulation, Virenschutz, Speicher-/Kontingentzulassung, unveränderliche Versionen, Freigabeberechtigungen/Passwörter/Empfängercodes, gleichzeitige Zugriffe, API-Berechtigungen und Wiederholungen, SSRF-resistente Webhooks, freigegebene E-Mail-Warteschlangen, privater Objektspeicher, Exporte, Löschung und Wiederherstellbarkeit. Bereitstellungsskripte und native Vorschauisolation gehören ebenfalls dazu.

Das [Sicherheitsmodell](docs/SECURITY_MODEL.md), die [Updateanleitung für 0.5](docs/UPGRADING_0_5.md) und die [Vorbereitung eines unabhängigen Audits](docs/INDEPENDENT_AUDIT.md) beschreiben auf Englisch beabsichtigte Eigenschaften und Prüfnachweise. Ein bestandener automatisierter Test beweist nicht die Abwesenheit von Schwachstellen.

Serveradministratoren und Betreiber können Dateien lesen; es gibt keine Ende-zu-Ende-Verschlüsselung. Empfänger können erhaltene Inhalte weitergeben. Widerruf sperrt künftige Autorisierungen, löscht keine Kopien und stoppt einen bereits autorisierten Download nicht zwingend. E-Mail-Annahme und Downloadautorisierung beweisen keinen vollständigen Empfang. Das sind dokumentierte Produktgrenzen.
