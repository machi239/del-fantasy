# DEL Fantasy – Datenbasis

Täglicher, automatischer Abzug der Spielerstatistiken der PENNY DEL, Umrechnung in
Fantasy-Punkte und Ablage als CSV (Ordner `data/`) sowie im Google Sheet.

## Ablauf
GitHub Actions startet `scripts/update.py` täglich um 09:00 UTC. Das Skript
1. lädt den Spielplan aller Monate von penny-del.org,
2. wertet jedes neue Spiel aus, dessen Beginn mehr als 12 Stunden zurückliegt
   (dann sind die Fantasy-Punkte endgültig),
3. schreibt `spielplan.csv`, `spielerspiele.csv`, `matrix.csv` (und `abgleich.csv`),
4. spiegelt alles ins Google Sheet.

Manuell starten: Reiter **Actions** → „Täglicher Datenabzug“ → **Run workflow**.

## Tabellen
| Blatt | Inhalt |
|---|---|
| Matrix | Eine Zeile pro Spieler, eine Spalte pro Spieltag (ST01 …). Zahl = Fantasy-Punkte, „–“ = Team hat gespielt, Spieler nicht eingesetzt, leer = Spiel steht noch aus. Dazu Summe, Schnitt, Schnitt letzte 5, Eiszeit (Saison, letzte 3), PP-Eiszeit letzte 3. |
| Spielerspiele | Rohdaten: eine Zeile pro Spieler und Spiel mit allen Einzelwerten (Tore, Assists, +/-, Schüsse, Blocks, GWG, Unterzahltore, Eiszeit gesamt/PP/SH, Starter, Torhüterwerte). |
| Spielplan | Alle Spiele inkl. Spieltag, Ergebnis, Entscheidung (REG/OT/SO). |
| Stammdaten | Preis, Pass, Position, Aufstellungsquote aus dem Fantasy-Export. |
| Abgleich | Berechnete Punkte gegen die offiziellen Fantasy-Punkte (nur komplett gespielte Spieltage). |

## Fantasy-Stammdaten aktualisieren (nach den Spieltagen 13, 26, 39, 52)
Im Fantasy Manager: F12 → Network → Fetch/XHR → „get options“ → Rechtsklick → *Copy response*.
Inhalt als Datei `fantasy_options.json` speichern und im Repo unter `data/` hochladen
(bestehende Datei ersetzen).

## Secrets (Settings → Secrets and variables → Actions)
- `GOOGLE_SERVICE_ACCOUNT_JSON`: kompletter Inhalt der Schlüsseldatei des Dienstkontos
- `SHEET_ID`: ID des Google Sheets (aus der URL zwischen `/d/` und `/edit`)
