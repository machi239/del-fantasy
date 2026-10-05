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
F_SPERREN = os.path.join(DATA, "sperren.json")
F_FEHLENDE = os.path.join(DATA, "fehlende.json")
F_GESAMT = os.path.join(DATA, "infos_gesamt.json")
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

    # 4. Sperren (DEL-Strafbank) und fehlende Spieler ergaenzen.
    #    data/infos.json bleibt die reine Recherche; gerechnet wird mit data/infos_gesamt.json.
    py = sys.executable
    sperren_alt = lies(F_SPERREN, {}) or {}
    stand = sperren_alt.get("stand", "")
    if args.immer or not stand or stand < (now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M"):
        r = subprocess.run([py, os.path.join(ROOT, "scripts", "sperren.py")])
        if r.returncode != 0:
            print("WARNUNG: Strafbank nicht lesbar, verwende letzten Stand.")
    gesamt = mit_sperren(infos, lies(F_SPERREN, {}) or {}, schedule, st)
    fehlende = fehlende_spieler(schedule)
    with open(F_FEHLENDE, "w", encoding="utf-8") as f:
        json.dump(fehlende, f, ensure_ascii=False, indent=1)
    with open(F_GESAMT, "w", encoding="utf-8") as f:
        json.dump(gesamt, f, ensure_ascii=False, indent=1)

    # 5. Rechnen
    os.makedirs(os.path.dirname(F_SEITE), exist_ok=True)
    subprocess.run([py, os.path.join(ROOT, "scripts", "optimize.py"), "--input", F_GESAMT,
                    "--out", F_AUF], check=True)

    # 6. Spieler der Aufstellung, die zuletzt ohne bekannten Grund fehlten, als Risiko
    auf = lies(F_AUF, {})
    bekannt = set(gesamt.get("availability", {}))
    for p in auf.get("aufstellung", []):
        f = fehlende.get(str(p["spieler_id"]))
        if f and str(p["spieler_id"]) not in bekannt:
            gesamt.setdefault("risiken", []).insert(0, {
                "text": f"{p['name']} ({p['team']}) fehlte im letzten Spiel. "
                        f"Zuletzt eingesetzt am {f['zuletzt_gespielt']}, Grund nicht recherchiert.",
                "schwer": True})
    with open(F_GESAMT, "w", encoding="utf-8") as f:
        json.dump(gesamt, f, ensure_ascii=False, indent=1)
    subprocess.run([py, os.path.join(ROOT, "scripts", "seite.py"), "--aufstellung", F_AUF,
                    "--infos", F_GESAMT, "--out", F_SEITE], check=True)


def mit_sperren(infos, sperren, schedule, st):
    """Gesperrte Spieler fuer die Spiele dieses Spieltags auf availability 0 setzen."""
    gesamt = json.loads(json.dumps(infos))
    av = gesamt.setdefault("availability", {})
    risiken = gesamt.setdefault("risiken", [])
    quellen = gesamt.setdefault("quellen", [])
    runde = [g for g in schedule if g["spieltag"] and int(g["spieltag"]) == st]
    for s in sperren.get("sperren", []):
        tage = {g["datum"] for g in runde if s["team_id"] in (g["heim_id"], g["gast_id"])}
        if tage & set(s["gesperrte_spiele"]):
            av[str(s["spieler_id"])] = 0
            letzter = max(s["gesperrte_spiele"])
            risiken.append({"text": f"{s['name']} gesperrt. {s['spiele']} Spiel(e) laut DEL-Strafbank "
                                    f"(gemeldet {s['gemeldet']}), letztes gesperrtes Spiel am {letzter}.",
                            "schwer": True})
            quellen.append({"titel": "DEL-Strafbank: " + s["titel"], "url": s["url"]})
            print(f"Sperre beruecksichtigt: {s['name']}")
    return gesamt


def fehlende_spieler(schedule):
    """Spieler, die in mindestens 2 der letzten 5 Teamspiele dabei waren, aber im letzten fehlten."""
    rows = read_csv(os.path.join(DATA, "spielerspiele.csv"))
    spiele_team = {}
    for r in rows:
        spiele_team.setdefault(r["team_id"], {}).setdefault((r["datum"], r["game_id"]), []).append(r)
    out = {}
    for team, spiele in spiele_team.items():
        folge = sorted(spiele)
        if len(folge) < 2:
            continue
        letzte5 = folge[-5:]
        im_letzten = {r["spieler_id"] for r in spiele[folge[-1]] if r["gespielt"] == "True"}
        zaehler, zuletzt, namen = {}, {}, {}
        for key in letzte5[:-1]:
            for r in spiele[key]:
                if r["gespielt"] == "True" and r["pos"] != "goal":  # Torhueter rotieren
                    zaehler[r["spieler_id"]] = zaehler.get(r["spieler_id"], 0) + 1
                    zuletzt[r["spieler_id"]] = key[0]
                    namen[r["spieler_id"]] = r["name"]
        for sid, n in zaehler.items():
            if n >= 2 and sid not in im_letzten:
                out[sid] = {"name": namen[sid], "team_id": team, "zuletzt_gespielt": zuletzt[sid],
                            "verpasst": folge[-1][0]}
    return out


if __name__ == "__main__":
    main()
