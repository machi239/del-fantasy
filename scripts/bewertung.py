"""Schaetzt Auf- und Abwertungskandidaten fuer die naechste Neubewertung der Spielerpreise.

Regeln laut DEL: Nach den Spieltagen 13, 26, 39 und 52 werden die Spieler "anhand ihrer
gezeigten Leistungen in der aktuellen Saison neu bewertet" (Preise 1-5 Pucks). Die genaue
Formel ist nicht veroeffentlicht. Annahme hier:
  - Bewertet wird je Position nach Fantasy-Punkten pro Spiel.
  - Die Preisstufen bleiben je Position ungefaehr so verteilt wie heute
    (z. B. 12 Stuermer zu 5 Pucks, 40 zu 4 Pucks ...).
Vorgehen:
  1. Punkte pro Spiel je Spieler aus data/spielerspiele.csv (Torhueter: Spiele ab 30 Minuten).
  2. Hochrechnung bis zur Neubewertung: Die noch ausstehenden Spiele werden mit einem
     Schaetzwert gefuellt, der zwischen Saisonschnitt und dem typischen Schnitt der heutigen
     Preisstufe liegt (wenige Spiele -> naeher an der Preisstufe).
  3. Rangfolge je Position, Preisstufen nach heutiger Verteilung vergeben -> "fairer Preis".
  4. Abstand zur naechsten Stufengrenze ergibt "deutlich" oder "knapp".

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
K_PRIOR = {"goal": 3, "def": 4, "for": 4}   # Gewicht der Preisstufe in "Spielen"
MIN_MIN_TW = 1800                          # Torhueter: Spiel zaehlt ab 30 Minuten


def read_csv(name):
    with open(os.path.join(DATA, name), encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def main():
    with open(os.path.join(DATA, "fantasy_options.json"), encoding="utf-8") as f:
        options = {o["id"]: o for o in json.load(f)["options"] if o.get("active") and o.get("price")}
    plan = read_csv("spielplan.csv")
    rows = read_csv("spielerspiele.csv")

    # Stand: hoechster vollstaendig gespielter regulaerer Spieltag
    gespielt = defaultdict(lambda: [0, 0])
    for g in plan:
        if g["spieltag"]:
            gespielt[int(g["spieltag"])][0] += 1
            gespielt[int(g["spieltag"])][1] += g["ausgewertet"] == "ja"
    fertig = [st for st, (n, a) in gespielt.items() if n == a]
    stand = max([st for st in fertig if all(s in fertig for s in range(1, st + 1))] or [0])
    ziel = next((n for n in NEUBEWERTUNG if n > stand), None)
    if ziel is None:
        print("Keine Neubewertung mehr ausstehend.")
        return

    # Ausstehende Spiele je Team bis einschliesslich Zielspieltag
    rest_team = defaultdict(int)
    gespielt_team = defaultdict(int)
    for g in plan:
        if g["ausgewertet"] == "ja":
            gespielt_team[g["heim_id"]] += 1
            gespielt_team[g["gast_id"]] += 1
    for g in plan:
        if g["spieltag"] and int(g["spieltag"]) <= ziel and g["ausgewertet"] != "ja":
            rest_team[g["heim_id"]] += 1
            rest_team[g["gast_id"]] += 1

    stats = defaultdict(lambda: {"spiele": 0, "punkte": 0.0})
    for r in rows:
        if r["gespielt"] != "True":
            continue
        if r["pos"] == "goal" and float(r["min_s"] or 0) < MIN_MIN_TW:
            continue
        s = stats[r["spieler_id"]]
        s["spiele"] += 1
        s["punkte"] += float(r["punkte"])

    # Typischer Schnitt je Position und heutiger Preisstufe (Median, mind. 3 Spiele)
    prior = {}
    for pos in ("goal", "def", "for"):
        for preis in range(1, 6):
            v = [stats[i]["punkte"] / stats[i]["spiele"] for i, o in options.items()
                 if o["position"] == pos and o["price"] == preis and stats[i]["spiele"] >= 3]
            prior[(pos, preis)] = statistics.median(v) if len(v) >= 3 else None
        # Luecken fuellen (monoton von unten)
        last = 0.0
        for preis in range(1, 6):
            if prior[(pos, preis)] is None:
                prior[(pos, preis)] = last
            last = prior[(pos, preis)]

    ergebnis = {"stand_spieltag": stand, "neubewertung_nach": ziel, "positionen": {}}
    for pos in ("goal", "def", "for"):
        ps = []
        for i, o in options.items():
            if o["position"] != pos:
                continue
            s = stats[i]
            n, summe = s["spiele"], s["punkte"]
            pri = prior[(pos, o["price"])]
            k = K_PRIOR[pos]
            erwartung = (summe + k * pri) / (n + k)           # Schaetzwert fuer kuenftige Spiele
            # Ausstehende Einsaetze nach bisheriger Einsatzquote (Ersatztorhueter spielen selten)
            quote = min(1.0, n / gespielt_team[o["team"]]) if gespielt_team.get(o["team"]) else 0
            rest = rest_team.get(o["team"], 0) * quote
            gesamt_n = n + rest
            wert = (summe + rest * erwartung) / gesamt_n if gesamt_n else None
            ps.append({"id": i, "name": o["name"], "team_id": o["team"], "preis": o["price"],
                       "dt_pass": o["german_license"], "spiele": n,
                       "schnitt": round(summe / n, 2) if n else None,
                       "wert": round(wert, 3) if wert is not None else None})
        # Rangfolge: Spieler mit Bewertung nach Wert, ohne Einsatz ans Ende
        bewertet = sorted([p for p in ps if p["wert"] is not None], key=lambda p: -p["wert"])
        verteilung = {preis: sum(1 for p in ps if p["preis"] == preis) for preis in range(1, 6)}
        # Stufen von oben vergeben, Grenzwerte merken
        grenzen, idx = {}, 0
        for preis in range(5, 0, -1):
            anz = verteilung[preis]
            for p in bewertet[idx: idx + anz]:
                p["fair"] = preis
            idx += anz
            if preis > 1 and idx <= len(bewertet) and idx > 0:
                # Grenze zwischen dieser und der naechsttieferen Stufe
                unten = bewertet[idx]["wert"] if idx < len(bewertet) else bewertet[-1]["wert"]
                grenzen[preis] = (bewertet[idx - 1]["wert"] + unten) / 2
        for p in bewertet[idx:]:
            p["fair"] = 1
        auf, ab = [], []
        for p in bewertet:
            if p["fair"] == p["preis"]:
                continue
            # Abstand zur relevanten Grenze
            if p["fair"] > p["preis"]:
                g = grenzen.get(p["preis"] + 1)
                abstand = p["wert"] - g if g is not None else 0
            else:
                g = grenzen.get(p["preis"])
                abstand = g - p["wert"] if g is not None else 0
            p["abstand"] = round(abstand, 2)
            p["sicherheit"] = ("deutlich" if (abs(p["fair"] - p["preis"]) >= 2 or abstand >= 0.35)
                               and p["spiele"] >= 5 else "knapp")
            (auf if p["fair"] > p["preis"] else ab).append(p)
        key = lambda p: (p["sicherheit"] != "deutlich", -abs(p["fair"] - p["preis"]), -p["abstand"])
        ergebnis["positionen"][pos] = {
            "aufwertung": sorted(auf, key=key),
            "abwertung": sorted(ab, key=key),
            "ohne_einsatz": [p for p in ps if p["wert"] is None and p["preis"] >= 3],
            "grenzen": {str(k): round(v, 2) for k, v in grenzen.items()},
        }
        print(f"{pos}: {len(auf)} Aufwertungs-, {len(ab)} Abwertungskandidaten")

    with open(os.path.join(DATA, "bewertung.json"), "w", encoding="utf-8") as f:
        json.dump(ergebnis, f, ensure_ascii=False, indent=1)
    print(f"Stand nach Spieltag {stand}, Neubewertung nach Spieltag {ziel}.")


if __name__ == "__main__":
    main()
