"""Beste Aufstellung fuer einen Spieltag.

Aufruf (im Chat-Sandbox oder lokal):
  python scripts/optimize.py                       naechster Spieltag, ohne Zusatzinfos
  python scripts/optimize.py --input infos.json    mit Quoten, Ausfaellen, Torhueter-Starts
  python scripts/optimize.py --spieltag 4 --budget 60

infos.json (alle Felder optional):
{
  "odds": {"12-44": {"p_home60": 0.52, "p_away60": 0.28, "total": 5.6}},   # HEIM_ID-GAST_ID
  "availability": {"550": 0, "1404": 0.5},       # 0 = faellt aus, 0.5 = fraglich
  "goalie_start": {"122": 1.0, "4154": 0.0},     # bestaetigte/erwartete Starter
  "force": [2209], "exclude": [3567],            # Spieler erzwingen/ausschliessen
  "budget": 60
}
Ergebnis: data/aufstellung.json und data/projektion.csv
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from del_fantasy.project import project  # noqa: E402

TZ = ZoneInfo("Europe/Berlin")
SLOTS = {"goal": 2, "def": 7, "for": 12}
MAX_FOREIGN = 9


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def start_dt(g):
    return datetime.strptime(f"{g['datum']} {g['uhrzeit'] or '19:30'}", "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


def pick_round(schedule, now):
    """Naechster Spieltag, dessen erstes Spiel noch nicht begonnen hat."""
    by_st = {}
    for g in schedule:
        if g["spieltag"]:
            by_st.setdefault(int(g["spieltag"]), []).append(g)
    open_rounds = [(min(start_dt(g) for g in gs), st) for st, gs in by_st.items()
                   if min(start_dt(g) for g in gs) > now]
    return min(open_rounds)[1] if open_rounds else None


def optimize(players, budget, max_foreign=MAX_FOREIGN, force=(), exclude=()):
    """Ganzzahlige Optimierung (HiGHS ueber scipy): maximale erwartete Punkte unter allen Regeln."""
    n = len(players)
    c = -np.array([p["xp"] for p in players], dtype=float)
    rows, lo, hi = [], [], []
    for pos, k in SLOTS.items():
        rows.append([1.0 if p["pos"] == pos else 0.0 for p in players]); lo.append(k); hi.append(k)
    rows.append([float(p["preis"]) for p in players]); lo.append(0); hi.append(budget)
    rows.append([0.0 if p["dt_pass"] else 1.0 for p in players]); lo.append(0); hi.append(max_foreign)
    lb, ub = np.zeros(n), np.ones(n)
    ids = [p["spieler_id"] for p in players]
    for pid in force:
        if pid in ids:
            lb[ids.index(pid)] = 1
    for pid in exclude:
        if pid in ids:
            ub[ids.index(pid)] = 0
    res = milp(c, constraints=LinearConstraint(np.array(rows), lo, hi),
               integrality=np.ones(n), bounds=Bounds(lb, ub))
    if not res.success:
        raise RuntimeError(f"Keine gueltige Aufstellung gefunden: {res.message}")
    return [p for p, v in zip(players, res.x) if v > 0.5]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--input", help="JSON mit Quoten, Ausfaellen, Torhueter-Starts")
    ap.add_argument("--spieltag", type=int)
    ap.add_argument("--budget", type=float)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    infos = {}
    if args.input:
        with open(args.input, encoding="utf-8") as f:
            infos = json.load(f)
    schedule = read_csv(os.path.join(args.data, "spielplan.csv"))
    game_rows = read_csv(os.path.join(args.data, "spielerspiele.csv"))
    with open(os.path.join(args.data, "fantasy_options.json"), encoding="utf-8") as f:
        options = {int(o["id"]): o for o in json.load(f)["options"]}
    team_names = {}
    for g in schedule:
        team_names[int(g["heim_id"])] = g["heim"]
        team_names[int(g["gast_id"])] = g["gast"]

    now = datetime.now(TZ)
    st = args.spieltag or pick_round(schedule, now)
    round_games = [g for g in schedule if g["spieltag"] and int(g["spieltag"]) == st]
    if not round_games:
        sys.exit(f"Keine Spiele fuer Spieltag {st} gefunden.")
    deadline = min(start_dt(g) for g in round_games)

    to_int = lambda d: {int(k): v for k, v in (d or {}).items()}
    players, games = project(round_games, schedule, game_rows, options,
                             odds=infos.get("odds"),
                             availability=to_int(infos.get("availability")),
                             goalie_start=to_int(infos.get("goalie_start")))
    for p in players:
        p["team"] = team_names.get(p["team_id"], p["team_id"])
        p["gegner"] = team_names.get(p["gegner_id"], p["gegner_id"])
        p["wert_pro_puck"] = round(p["xp"] / p["preis"], 2) if p["preis"] else 0

    budget = args.budget or infos.get("budget") or 60
    lineup = optimize(players, budget, force=[int(i) for i in infos.get("force", [])],
                      exclude=[int(i) for i in infos.get("exclude", [])])
    chosen = {p["spieler_id"] for p in lineup}
    alternatives = {pos: [p for p in players if p["pos"] == pos and p["spieler_id"] not in chosen][:5]
                    for pos in SLOTS}
    order = {"goal": 0, "def": 1, "for": 2}
    lineup.sort(key=lambda p: (order[p["pos"]], -p["xp"]))

    result = {
        "spieltag": st,
        "deadline": deadline.strftime("%a %d.%m.%Y %H:%M"),
        "erstellt": now.strftime("%d.%m.%Y %H:%M"),
        "budget": budget,
        "pucks": sum(p["preis"] for p in lineup),
        "auslaender": sum(1 for p in lineup if not p["dt_pass"]),
        "xp_gesamt": round(sum(p["xp"] for p in lineup), 1),
        "spiele": games,
        "aufstellung": lineup,
        "alternativen": alternatives,
    }
    out = args.out or os.path.join(args.data, "aufstellung.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    cols = ["spieler_id", "name", "team", "pos", "preis", "dt_pass", "xp", "wert_pro_puck", "gegner",
            "heim", "spiele", "gewaehlt_pct", "hinweis", "ppg", "toi_faktor", "matchup", "p_einsatz",
            "p_start", "p_sieg60", "xga", "xsv"]
    with open(os.path.join(os.path.dirname(out), "projektion.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(players)

    print(f"Spieltag {st}, Deadline {result['deadline']}")
    for g in games:
        print(f"  {g['spiel']:48} xG {g['xg_heim']}:{g['xg_gast']}  ({g['quelle']})")
    print(f"\nAufstellung: {result['xp_gesamt']} erwartete Punkte, {result['pucks']}/{budget} Pucks, "
          f"{result['auslaender']}/{MAX_FOREIGN} Auslaender")
    for p in lineup:
        print(f"  {p['pos']:4} {p['name'][:24]:24} {str(p['team'])[:22]:22} {p['preis']}P "
              f"xP {p['xp']:5}  {'' if p['dt_pass'] else 'A'}  {p['hinweis']}")


if __name__ == "__main__":
    main()
