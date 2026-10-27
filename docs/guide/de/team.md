# Mitglieder einladen und verwalten

Nur Administratoren können **Mitglieder** öffnen. Mitglieder haben eigene Dateien, Links, Gestaltung, API-Schlüssel und Webhooks. Administratorrechte zeigen betriebliche Zähler und Kontingente an, übernehmen aber keine fremden Dateilisten in Ihre Bibliothek.

![Mitglieder und Einladungsformular](../../screenshots/user-guide/de/team.png)

## Mitglied einladen

1. Geben Sie unter **Mitglieder → Mitglied einladen** die E-Mail-Adresse ein.
2. Wählen Sie **Einladung erstellen**.
3. Kopieren Sie die Einladung und senden Sie sie über einen selbst gewählten Kanal an die Person. Liasio verschickt Einladungen nicht automatisch per E-Mail.
4. Die eingeladene Person öffnet den Link, prüft die festgelegte Adresse und wählt Namen und Passwort. Eingeladene Mitglieder erhalten keine Administratorrechte.

Eine Einladung gilt sieben Tage lang und kann einmal angenommen werden. Die E-Mail-Adresse ist festgelegt. Eine bereits registrierte Adresse kann nicht eingeladen werden. **Widerrufen** beendet eine ungenutzte Einladung; eine abgelaufene Einladung ersetzen Sie durch eine neue.

## Sperren und reaktivieren

Wählen Sie beim Mitglied **Sperren** und bestätigen Sie. Dies beendet seine Sitzungen, widerruft Freigabelinks und API-Schlüssel und pausiert Webhooks. Dateien bleiben gespeichert und werden weiter auf das Kontingent angerechnet. Laufende Übertragungen oder Webhook-Anfragen können noch abgeschlossen werden.

**Reaktivieren** erlaubt die Anmeldung erneut. Widerrufene Links und Schlüssel bleiben widerrufen; Webhooks bleiben pausiert. Das Mitglied muss neue Links und Schlüssel erstellen und Webhooks bei Bedarf ausdrücklich reaktivieren. Administratorkonten können über diese Seite nicht gesperrt werden.

## Administration nachvollziehen

Die Seite zeigt die zehn neuesten Administrationsereignisse mit übersetzten Bezeichnungen. Die gespeicherten Ereigniskennungen bleiben sprachunabhängig. Unter **Betrieb → Speicherkontingente der Mitglieder** ändern Sie Kontingente.

Das endgültige Löschen eines Kontos erfolgt über einen Serverbefehl im [Betriebsleitfaden (Französisch)](../../OPERATIONS.md). Für eine vorübergehende Zugangssperre verwenden Sie die umkehrbare Sperrung.
