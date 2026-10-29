# Mehrere Links ohne API erstellen

Ein Stapel erstellt unabhängige Links zu **derselben Sammlung**. Jeder Link hat eine eigene URL, eigene Zähler und einen eigenen Verlauf. Es werden keine E-Mails verschickt. Verwenden Sie die Links einzeln oder die exportierte Liste in Ihrem gewohnten Werkzeug.

![Einen Linkstapel vorbereiten](../../screenshots/user-guide/de/batch-prepare.png)

## 1. Sammlung und Liste wählen

Öffnen Sie eine Sammlung mit Dateien und wählen Sie **Mehrere Links erstellen**. Alternativ öffnen Sie **Meine Links → Meine Stapel**, wählen eine Sammlung und klicken auf **Stapel vorbereiten**. Geben Sie einen **Stapelnamen** ein, damit Sie die Liste später wiederfinden.

- **Anzahl angeben:** Wählen Sie zwischen 1 und 200. Der nächste Schritt schlägt Namen wie `Link 001`, `Link 002` vor. Das Mausrad verändert die Anzahl nicht.
- **Excel- oder CSV-Liste importieren:** Wählen Sie `.xlsx` oder eine `.csv` in UTF-8. **Excel-Vorlage** und **CSV-Vorlage** enthalten Beispielspalten für Name, E-Mail und Referenz. Ersetzen Sie die Beispiele vor dem Import durch Ihre eigenen Zeilen.

Jede nicht leere Datenzeile nach der Kopfzeile erhält einen neuen Link. Reihenfolge und doppelte Namen bleiben erhalten. Zusätzliche Spalten wie E-Mail-Adressen oder Kundennummern werden für den Export gespeichert, lösen aber keine Nachrichten aus.

Wählen Sie **Namen und Bedingungen prüfen**. Zu diesem Zeitpunkt wurden noch keine Links erstellt.

## 2. Prüfen und erstellen

Bei importierten Listen wählen Sie **Spalte für die Linknamen** und **Als Namen übernehmen**. Danach können Sie jeden Namen mit höchstens 80 Zeichen bearbeiten. Die Namen sind privat und nur für Sie sichtbar. Unter **Ursprüngliche Daten** prüfen Sie die Quellwerte einer Zeile.

Die gewählten Bedingungen gelten für jeden Link einzeln: Ablaufdatum oder Laufzeit in Tagen, maximale Downloads, optionales gemeinsames Passwort und Vorschauen. Beispielsweise erlauben 20 Links mit dem Limit `1` je einen Download, insgesamt höchstens 20. Die Laufzeit beginnt bei der Linkerstellung, nicht beim Listenimport.

Wählen Sie **X Links erstellen**. Der Vorgang ist atomar: Alle Links entstehen oder keiner. Erneutes Absenden desselben Entwurfs erzeugt keine Duplikate. Ein neuer Stapel aus derselben Datei erzeugt dagegen neue Links.

## 3. Links verwenden und Liste exportieren

![Erstellte Links und Exportaktionen](../../screenshots/user-guide/de/batch-result.png)

Kopieren Sie einzelne URLs oder wählen Sie **Excel herunterladen** beziehungsweise **CSV herunterladen**. Der Export enthält die ursprünglichen Spalten, den aktuellen Liasio-Namen, URL, Status, UTC-Datumsangaben und Zähler. URLs sind in Excel anklickbar.

Zellen werden als Werte exportiert. Formeln, Makros, Formatierungen und weitere Tabellenblätter der ursprünglichen Arbeitsmappe werden nicht übernommen. Als Text eingegebene Kennungen wie `00127` bleiben Text. CSV verwendet UTF-8 mit BOM und Semikolon als Trennzeichen. Mögliche Formelzellen erhalten ein schützendes Apostroph. **Das Passwort aus den Bedingungen wird niemals exportiert**; übermitteln Sie es separat.

Bearbeiten Sie Namen, wählen Sie **Namen speichern** und laden Sie die Liste erneut herunter. **Umbenennen** unter **Meine Links** ist ebenfalls möglich. URLs und Bedingungen bleiben gleich. Exporte zeigen den Stand zum Download-Zeitpunkt; heruntergeladene Dateien aktualisieren sich nicht automatisch.

## Liste finden oder entfernen

Unter **Meine Stapel** finden Sie alle Listen. Entwürfe laufen nach 24 Stunden ab; pro Konto bleiben bis zu 100 Stapel gespeichert. **Liste löschen** entfernt Quelldaten und Zuordnungen. Links bleiben nutzbar und unter **Meine Links** sichtbar. Für eine Zugriffssperre verwenden Sie **Widerrufen**. Backups folgen ihrer eigenen Aufbewahrungsfrist.

Listen gehören ausschließlich dem Eigentümer der Sammlung, erscheinen nie für Empfänger und sind Teil der Datenbank-Backups. Im Papierkorb ist der Zugriff auf ihre Sammlung bis zur Wiederherstellung gesperrt; endgültiges Löschen der Sammlung entfernt auch die Listen.

## Formate und Grenzen

- Höchstens 1 MiB pro Datei, 200 Datenzeilen, 20 Spalten und 1.000 Zeichen pro Zelle. Insgesamt höchstens 256 KiB Textinhalt.
- Eindeutige, nicht leere Spaltenüberschriften mit höchstens 80 Zeichen. Vollständig leere Zeilen werden ignoriert.
- Nur das erste Excel-Tabellenblatt wird importiert. Verwenden Sie möglichst eine Arbeitsmappe nur mit dieser Tabelle; Sicherheitsgrenzen gelten für die gesamte Datei.
- `.xls`, `.xlsm`, verschlüsselte Dateien, Formeln und Verknüpfungen zu anderen Arbeitsmappen werden nicht unterstützt. In Excel: **Kopieren → Inhalte einfügen → Werte**, dann als `.xlsx` speichern.
- Zehn Stapelvorbereitungen pro Stunde und Konto; zusätzliche IP-Grenzen gelten. Warten Sie bei einer Begrenzung bis zum nächsten Versuch.
- Ist Virenschutz vorgeschrieben, müssen Liste und Sammlungsdateien die Prüfung bestehen. Listendaten haben eigene Datenbankgrenzen und zählen nicht zum Dateispeicherkontingent.

Für automatisierte Listen lesen Sie [Stapel per API](bulk-links-api.md). Diese erscheinen ebenfalls unter **Meine Stapel**.

[Linkbedingungen](sharing.md) · [API und Webhooks](automation.md) · [Zum Benutzerhandbuch](README.md)

## Bedingungen wiederverwenden und E-Mails vorbereiten

Wählen Sie eine Freigabevorlage und prüfen Sie Gültigkeit, Limits und Passwortpflicht. Bei konfiguriertem SMTP können Sie die E-Mail-Spalte und **E-Mail-Adresse jeder Zeile prüfen** auswählen, um jeden Link auf seinen Empfänger zu beschränken, oder eine gemeinsame Empfängerliste angeben. Nach der Erstellung übernimmt **E-Mails vorbereiten** die Adressen aus der Tabelle. Prüfen Sie jede Nachricht und bestätigen Sie den Versand ausdrücklich. Vorlagen und Limits erklärt die [E-Mail-Anleitung](email.md).
