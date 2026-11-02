"""Preisanpassungen nach einer Neubewertung auswerten.

Vergleicht data/fantasy_options.json (aktueller Auszug) mit dem archivierten Stand vor der
letzten Neubewertung (data/preise/vor_neubewertung_NN.json) und prueft, wie gut die eigene
Schaetzung (data/preise/bewertung_vor_NN.json) die Richtung getroffen hat.

Ergebnis: data/preisaenderungen.json (nur wenn sich Preise geaendert haben)
"""
import csv
import glob
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "preisaenderungen.json")


def main():
    archive = sorted(glob.glob(os.path.join(DATA, "preise", "vor_neubewertung_*.json")),
                     key=lambda p: int(re.search(r"_(\d+)\.json$", p).group(1)))
    if not archive:
        print("Kein archivierter Preisstand vorhanden.")
        return
    basis = archive[-1]
    nach = int(re.search(r"_(\d+)\.json$", basis).group(1))
    with open(basis, encoding="utf-8") as f:
        alt = {o["id"]: o for o in json.load(f)["options"]}
    with open(os.path.join(DATA, "fantasy_options.json"), encoding="utf-8") as f:
        akt = json.load(f)
    neu = {o["id"]: o for o in akt["options"]}
    teamname = {}
    with open(os.path.join(DATA, "spielplan.csv"), encoding="utf-8") as f:
        for g in csv.DictReader(f):
            teamname[g["heim_id"]], teamname[g["gast_id"]] = g["heim"], g["gast"]

    # eigene Schaetzung vor der Neubewertung (fairer Preis je Spieler)
    schaetzung = {}
    f_bew = os.path.join(DATA, "preise", f"bewertung_vor_{nach}.json")
    if os.path.exists(f_bew):
        with open(f_bew, encoding="utf-8") as f:
            for pos in json.load(f)["positionen"].values():
                for p in pos["aufwertung"] + pos["abwertung"]:
                    schaetzung[p["id"]] = p["fair"]

    aenderungen, neu_dabei = [], []
    for pid, o in neu.items():
        if pid not in alt:
            neu_dabei.append({"id": pid, "name": o["name"], "team": teamname.get(o["team"], o["team"]),
                              "pos": o["position"], "preis": o["price"]})
            continue
        a, n = alt[pid].get("price") or 0, o.get("price") or 0
        if a and n and a != n:
            aenderungen.append({"id": pid, "name": o["name"], "team": teamname.get(o["team"], o["team"]),
                                "pos": o["position"], "dt_pass": o.get("german_license", True),
                                "alt": a, "neu": n, "diff": n - a, "schaetzung": schaetzung.get(pid)})
    if not aenderungen:
        print("Keine Preisaenderungen gegenueber dem archivierten Stand.")
        if os.path.exists(OUT):
            os.remove(OUT)
        return

    teams = {}
    for o in neu.values():
        if o["id"] in alt and o.get("price") and alt[o["id"]].get("price"):
            t = teams.setdefault(teamname.get(o["team"], o["team"]), {"auf": 0, "ab": 0, "netto": 0, "spieler": 0})
            t["spieler"] += 1
    for c in aenderungen:
        t = teams[c["team"]]
        t["auf" if c["diff"] > 0 else "ab"] += 1
        t["netto"] += c["diff"]

    # Trefferquote der eigenen Schaetzung (Richtung)
    mit_schaetzung = [c for c in aenderungen if c["schaetzung"] is not None]
    richtig = sum(1 for c in mit_schaetzung if (c["schaetzung"] - c["alt"]) * c["diff"] > 0)
    unveraendert_richtig = sum(1 for pid, o in neu.items()
                               if pid in alt and alt[pid].get("price") == o.get("price") and pid not in schaetzung)
    ergebnis = {
        "nach_spieltag": nach, "options_version": akt.get("options_version"),
        "anzahl": {"auf": sum(c["diff"] > 0 for c in aenderungen), "ab": sum(c["diff"] < 0 for c in aenderungen),
                   "gleich": sum(1 for pid, o in neu.items() if pid in alt and alt[pid].get("price") == o.get("price")),
                   "neu": len(neu_dabei)},
        "schaetzung": {"aenderungen_mit_schaetzung": len(mit_schaetzung), "richtung_richtig": richtig,
                       "aenderungen_gesamt": len(aenderungen), "unveraendert_richtig": unveraendert_richtig},
        "teams": dict(sorted(teams.items(), key=lambda kv: -kv[1]["netto"])),
        "aenderungen": sorted(aenderungen, key=lambda c: (-abs(c["diff"]), -c["diff"], c["name"])),
        "neu_dabei": neu_dabei,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(ergebnis, f, ensure_ascii=False, indent=1)
    a = ergebnis["anzahl"]
    print(f"Preisaenderungen nach Spieltag {nach}: {a['auf']} rauf, {a['ab']} runter, {a['gleich']} gleich, {a['neu']} neu")


if __name__ == "__main__":
    main()
