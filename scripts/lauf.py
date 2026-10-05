"""Rechnet die Aufstellung und baut die Seite fuer GitHub Pages.

Ablauf:
1. Recherche (infos.json) aus Google Drive laden, falls vorhanden.
2. Entscheiden, ob gerechnet wird:
   --immer                         immer rechnen (manueller Start, nach Daten-/Quotenlauf)
   sonst (Zeitplan alle 10 Min.)   nur wenn sich die Recherche geaendert hat,
                                   der Spieltag gewechselt hat, die Seite fehlt
                                   oder die Deadline 0 bis 95 Minuten entfernt ist.
3. Veraltetes aus der Recherche entfernen (anderer Spieltag, abgelaufene Ausfaelle).
4. optimize.py und seite.py ausfuehren.

Ergebnis: data/infos.json, data/aufstellung.json, data/projektion.csv, docs/index.html
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from optimize import TZ, pick_round, read_csv, start_dt  # noqa: E402

DATA = os.path.join(ROOT, "data")
F_INFOS = os.path.join(DATA, "infos.json")
F_STAND = os.path.join(DATA, "infos_stand.txt")
F_AUF = os.path.join(DATA, "aufstellung.json")
F_SEITE = os.path.join(ROOT, "docs", "index.html")
FENSTER_MIN = 95


def lies(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f) if path.endswith(".json") else f.read().strip()


def bereinigen(infos, spieltag, heute):
    infos = dict(infos)
    if infos.get("spieltag") not in (None, spieltag):
        print(f"Recherche gehoert zu Spieltag {infos.get('spieltag')}, aktuell {spieltag}: "
              "Torhueter, Notizen, Quoten und kurzfristige Risiken verworfen.")
        for k in ("goalie_start", "notizen", "odds", "odds_quellen"):
            infos[k] = {}
        infos["risiken"] = [r for r in infos.get("risiken", []) if r.get("bis")]
    infos["risiken"] = [r for r in infos.get("risiken", []) if not r.get("bis") or r["bis"] >= heute]
    infos["spieltag"] = spieltag
    return infos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--immer", action="store_true")
    args = ap.parse_args()

    now = datetime.now(TZ)
    schedule = read_csv(os.path.join(DATA, "spielplan.csv"))
    st = pick_round(schedule, now)
    if st is None:
        print("Kein offener Spieltag.")
        return
    deadline = min(start_dt(g) for g in schedule if g["spieltag"] and int(g["spieltag"]) == st)
    print(f"Jetzt {now:%d.%m.%Y %H:%M}, Spieltag {st}, Deadline {deadline:%d.%m.%Y %H:%M}")

    # 1. Recherche aus Drive
    neu_recherche = False
    infos = lies(F_INFOS, {}) or {}
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        try:
            from drive_infos import lade_infos
            d_infos, geaendert, name = lade_infos()
            if d_infos is None:
                print("Keine Recherche-Datei in Drive gefunden.")
            elif geaendert != lies(F_STAND):
                print(f"Neue Recherche aus Drive: {name} (geaendert {geaendert})")
                infos, neu_recherche = d_infos, True
                with open(F_STAND, "w", encoding="utf-8") as f:
                    f.write(geaendert + "\n")
            else:
                print(f"Recherche unveraendert (Stand {geaendert}).")
        except Exception as e:  # Rechnen soll auch ohne Drive funktionieren
            print(f"WARNUNG: Recherche aus Drive nicht ladbar: {type(e).__name__}: {e}")

    # 2. Rechnen?
    alt = lies(F_AUF, {}) or {}
    im_fenster = timedelta(0) < deadline - now <= timedelta(minutes=FENSTER_MIN)
    gruende = [g for g, ok in [("manuell/nach Datenlauf", args.immer), ("neue Recherche", neu_recherche),
                               ("Spieltag gewechselt", alt.get("spieltag") != st),
                               ("Seite fehlt", not os.path.exists(F_SEITE)),
                               ("Deadline-Fenster", im_fenster)] if ok]
    if not gruende:
        print("Nichts zu tun.")
        return
    print("Rechne: " + ", ".join(gruende))

    # 3. Bereinigen und speichern
    infos = bereinigen(infos, st, now.strftime("%Y-%m-%d"))
    with open(F_INFOS, "w", encoding="utf-8") as f:
        json.dump(infos, f, ensure_ascii=False, indent=1)

    # 4. Rechnen und Seite bauen
    os.makedirs(os.path.dirname(F_SEITE), exist_ok=True)
    py = sys.executable
    subprocess.run([py, os.path.join(ROOT, "scripts", "optimize.py"), "--input", F_INFOS,
                    "--out", F_AUF], check=True)
    subprocess.run([py, os.path.join(ROOT, "scripts", "seite.py"), "--aufstellung", F_AUF,
                    "--infos", F_INFOS, "--out", F_SEITE], check=True)


if __name__ == "__main__":
    main()
