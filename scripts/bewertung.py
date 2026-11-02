"""Schaetzt Auf- und Abwertungskandidaten fuer die naechste Neubewertung der Spielerpreise.

Regeln laut DEL: Nach den Spieltagen 13, 26, 39 und 52 werden die Spieler "anhand ihrer
gezeigten Leistungen in der aktuellen Saison neu bewertet" (Preise 1-5 Pucks). Die Formel ist
nicht veroeffentlicht.

Erkenntnisse aus der Neubewertung nach Spieltag 13 (Abgleich am 02.11.2026):
  - Kein Spieler hat sich um mehr als 1 Puck veraendert.
  - Geaendert wurden nur rund 12 % der Spieler je Position (46 von 382).
  - Am besten erklaert wird die Auswahl durch die Fantasy-Punkte der Saison (Summe) im Vergleich
    zum Median der eigenen Preisstufe: 35 von 46 Aenderungen getroffen (vorheriges Verfahren 24).

Vorgehen:
  1. Saison-Punkte je Spieler aus data/spielerspiele.csv (nur Spieltage bis zur Neubewertung,
     vorgezogene Spiele spaeterer Spieltage zaehlen noch nicht).
  2. Je Position: Abweichung vom Median der eigenen Preisstufe, geteilt durch die Streuung.
  3. Die 12 % der Spieler je Position mit der groessten Abweichung sind Kandidaten fuer +1 bzw.
     -1 Puck (5-Puck-Spieler koennen nicht steigen, 1-Puck-Spieler nicht fallen).
  4. Die vorderen zwei Drittel gelten als "deutlich", der Rest als "knapp".

Ergebnis: data/bewertung.json
"""
import csv
import json
import os
import statistics
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
NEUBEWERTUNG = (13, 26, 39, 52)
ANTEIL = 0.12          # Anteil der Spieler je Position, die sich aendern
ANTEIL_DEUTLICH = 2 / 3


def read_csv(name):
    with open(os.path.join(DATA, name), encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def main():
    with open(os.path.join(DATA, "fantasy_options.json"), encoding="utf-8") as f:
        options = {o["id"]: o for o in json.load(f)["options"] if o.get("active") and o.get("price")}
    plan = read_csv("spielplan.csv")
    rows = read_csv("spielerspiele.csv")

    # Stand: hoechster vollstaendig gespielter regulaerer Spieltag (lueckenlos ab 1)
    gespielt = defaultdict(lambda: [0, 0])
    for g in plan:
        if g["spieltag"]:
            gespielt[int(g["spieltag"])][0] += 1
            gespielt[int(g["spieltag"])][1] += g["ausgewertet"] == "ja"
    fertig = {st for st, (n, a) in gespielt.items() if n == a}
    stand = 0
    while stand + 1 in fertig:
        stand += 1
    ziel = next((n for n in NEUBEWERTUNG if n > stand), None)
    if ziel is None:
        print("Keine Neubewertung mehr ausstehend.")
        return

    summe, spiele = defaultdict(float), defaultdict(int)
    for r in rows:
        if not r["spieltag"].isdigit() or int(r["spieltag"]) > ziel:
            continue
        summe[r["spieler_id"]] += float(r["punkte"] or 0)
        if r["gespielt"] == "True" and (r["pos"] != "goal" or float(r["min_s"] or 0) >= 1800):
            spiele[r["spieler_id"]] += 1

    ergebnis = {"stand_spieltag": stand, "neubewertung_nach": ziel, "methode": "Saisonpunkte vs. Median der Preisstufe, 12 % je Position, ±1 Puck",
                "positionen": {}}
    for pos in ("goal", "def", "for"):
        ids = [i for i, o in options.items() if o["position"] == pos]
        werte = {i: summe.get(i, 0.0) for i in ids}
        median = {p: statistics.median([werte[i] for i in ids if options[i]["price"] == p] or [0.0]) for p in range(1, 6)}
        streuung = statistics.pstdev(werte.values()) or 1.0
        kandidaten = []
        for i in ids:
            o = options[i]
            abw = (werte[i] - median[o["price"]]) / streuung
            if (abw > 0 and o["price"] == 5) or (abw < 0 and o["price"] == 1):
                continue
            n = spiele.get(i, 0)
            kandidaten.append({"id": i, "name": o["name"], "team_id": o["team"], "preis": o["price"],
                               "dt_pass": o["german_license"], "spiele": n,
                               "schnitt": round(werte[i] / n, 2) if n else None,
                               "summe": round(werte[i], 1), "median_stufe": round(median[o["price"]], 1),
                               "abstand": round(abs(abw), 2), "fair": o["price"] + (1 if abw > 0 else -1)})
        kandidaten.sort(key=lambda p: -p["abstand"])
        k = round(len(ids) * ANTEIL)
        auswahl = kandidaten[:k]
        for rang, p in enumerate(auswahl):
            p["sicherheit"] = "deutlich" if rang < round(k * ANTEIL_DEUTLICH) else "knapp"
        ergebnis["positionen"][pos] = {
            "aufwertung": [p for p in auswahl if p["fair"] > p["preis"]],
            "abwertung": [p for p in auswahl if p["fair"] < p["preis"]],
            "anzahl_erwartet": k,
        }
        print(f"{pos}: {k} Kandidaten ({sum(p['fair'] > p['preis'] for p in auswahl)} rauf, "
              f"{sum(p['fair'] < p['preis'] for p in auswahl)} runter)")

    with open(os.path.join(DATA, "bewertung.json"), "w", encoding="utf-8") as f:
        json.dump(ergebnis, f, ensure_ascii=False, indent=1)
    print(f"Stand nach Spieltag {stand}, Neubewertung nach Spieltag {ziel}.")


if __name__ == "__main__":
    main()
