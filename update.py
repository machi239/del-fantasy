"""Taeglicher Datenabzug.

1. Spielplan aller Monate laden
2. Neue, abgeschlossene Spiele (Spielbeginn > 12 h her) auswerten
3. CSV-Dateien in data/ aktualisieren (dienen als Speicher und Historie)
4. Alles ins Google Sheet spiegeln (wenn Zugangsdaten gesetzt sind)

Aufruf:  python scripts/update.py            normaler Lauf
         python scripts/update.py --neu      alle Spiele neu auswerten
"""
import csv
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from del_fantasy import config
from del_fantasy.parse import parse_month_urls, parse_schedule, parse_boxscore, parse_overview
from del_fantasy.scoring import score_game

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
F_SCHEDULE = os.path.join(DATA, "spielplan.csv")
F_GAMES = os.path.join(DATA, "spielerspiele.csv")
F_MATRIX = os.path.join(DATA, "matrix.csv")
F_CHECK = os.path.join(DATA, "abgleich.csv")
F_OPTIONS = os.path.join(DATA, "fantasy_options.json")
TZ = ZoneInfo("Europe/Berlin")
FREEZE_HOURS = 12  # Fantasy-Punkte sind 12 h nach Spielbeginn endgueltig

GAME_COLS = ["game_id", "spieltag", "datum", "team_id", "gegner_id", "heim", "spieler_id", "name",
             "nr", "pos", "gespielt", "starter", "g", "a", "pm", "pim", "sog", "blk", "gwg", "shg",
             "toi_s", "pp_s", "sh_s", "shifts", "gt", "sv", "min_s", "entscheidung_tw", "shutout",
             "punkte"]
SCHEDULE_COLS = ["spieltag", "datum", "uhrzeit", "heim", "gast", "heim_id", "gast_id", "tore_heim",
                 "tore_gast", "entscheidung", "status", "game_id", "url", "ausgewertet"]

session = requests.Session()
session.headers.update(config.HEADERS)


def fetch(path):
    url = path if path.startswith("http") else config.BASE_URL + path
    for attempt in range(3):
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
            time.sleep(config.REQUEST_PAUSE_S)
            return r.text
        except requests.RequestException as exc:
            print(f"  Fehler bei {url}: {exc} (Versuch {attempt + 1}/3)")
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Seite nicht erreichbar: {url}")


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, cols):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in cols})


def load_options():
    if not os.path.exists(F_OPTIONS):
        print("Hinweis: data/fantasy_options.json fehlt, Preise und Paesse bleiben leer.")
        return {}
    with open(F_OPTIONS, encoding="utf-8") as f:
        data = json.load(f)
    return {int(o["id"]): o for o in data.get("options", [])}


def load_schedule():
    first = fetch(config.SCHEDULE_URL)
    months = parse_month_urls(first) or []
    games, seen = [], set()
    pages = [first] + [fetch(m) for m in months]
    for html in pages:
        for g in parse_schedule(html):
            key = (g["datum"], g["heim_id"], g["gast_id"])
            if key not in seen:
                seen.add(key)
                games.append(g)
    games.sort(key=lambda g: (g["datum"], g["uhrzeit"], g["heim"]))
    print(f"Spielplan: {len(games)} Spiele, davon {sum(g['status'] == 'beendet' for g in games)} beendet")
    return games


def is_final(game, now):
    if game["status"] != "beendet" or not game["game_id"]:
        return False
    start = datetime.strptime(f"{game['datum']} {game['uhrzeit'] or '19:30'}", "%Y-%m-%d %H:%M")
    return now >= start.replace(tzinfo=TZ) + timedelta(hours=FREEZE_HOURS)


def to_num(v):
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return v


def build_matrix(schedule, game_rows, options):
    team_names = {}
    for g in schedule:
        team_names[g["heim_id"]] = g["heim"]
        team_names[g["gast_id"]] = g["gast"]
    max_st = max((g["spieltag"] for g in schedule if g["spieltag"] and g["spieltag"] < 100), default=0)
    played_st = defaultdict(set)  # team_id -> Spieltage, die das Team schon gespielt hat
    for r in game_rows:
        played_st[int(r["team_id"])].add(int(r["spieltag"]))

    per_player = defaultdict(list)
    info = {}
    for r in game_rows:
        pid = int(r["spieler_id"]) if r["spieler_id"] else None
        if pid is None:
            continue
        per_player[pid].append(r)
        info[pid] = r

    ids = set(per_player) | set(options)
    out = []
    for pid in ids:
        o = options.get(pid, {})
        rows = sorted(per_player.get(pid, []), key=lambda r: (r["datum"], int(r["spieltag"])))
        team_id = int(o["team"]) if o.get("team") else int(info[pid]["team_id"])
        played = [r for r in rows if str(r["gespielt"]) == "True"]
        pts = [float(r["punkte"]) for r in played]
        toi = [int(r["toi_s"] or 0) for r in played if r["pos"] != "goal"]
        pp = [int(r["pp_s"] or 0) for r in played if r["pos"] != "goal"]
        row = {
            "spieler_id": pid,
            "name": o.get("name") or info[pid]["name"],
            "team": team_names.get(team_id, team_id),
            "pos": o.get("position") or info[pid]["pos"],
            "preis": o.get("price", ""),
            "dt_pass": {True: "ja", False: "nein"}.get(o.get("german_license"), ""),
            "aktiv": {True: "ja", False: "nein"}.get(o.get("active"), ""),
            "gewaehlt_pct": o.get("stat_perc_picked", ""),
            "spiele": len(played),
            "summe": round(sum(pts), 1),
            "schnitt": round(sum(pts) / len(pts), 2) if pts else "",
            "schnitt_l5": round(sum(pts[-5:]) / len(pts[-5:]), 2) if pts else "",
            "toi_min": round(sum(toi) / len(toi) / 60, 1) if toi else "",
            "toi_min_l3": round(sum(toi[-3:]) / len(toi[-3:]) / 60, 1) if toi else "",
            "pp_min_l3": round(sum(pp[-3:]) / len(pp[-3:]) / 60, 1) if pp else "",
        }
        by_st = {int(r["spieltag"]): r for r in rows}
        for st in range(1, max_st + 1):
            col = f"ST{st:02d}"
            r = by_st.get(st)
            if r and str(r["gespielt"]) == "True":
                row[col] = to_num(r["punkte"])
            elif st in played_st.get(team_id, set()):
                row[col] = config.NOT_PLAYED
            else:
                row[col] = ""
        out.append(row)
    out.sort(key=lambda r: (-(r["summe"] or 0), str(r["name"])))
    cols = list(out[0].keys()) if out else []
    return out, cols


def build_check(schedule, game_rows, options):
    """Vergleich mit den Fantasy-Gesamtpunkten, nur ueber vollstaendig gespielte Spieltage."""
    per_st = defaultdict(list)
    for g in schedule:
        per_st[g["spieltag"]].append(g)
    done_ids = set()
    for st, games in per_st.items():
        if games and all(g["status"] == "beendet" for g in games):
            done_ids |= {g["game_id"] for g in games}
    calc = defaultdict(float)
    for r in game_rows:
        if int(r["game_id"]) in done_ids and r["spieler_id"]:
            calc[int(r["spieler_id"])] += float(r["punkte"])
    out = []
    for pid, o in options.items():
        fs = o.get("stat_score_sum")
        if fs is None:
            continue
        diff = round(calc.get(pid, 0.0) - fs, 1)
        out.append({"spieler_id": pid, "name": o["name"], "berechnet": round(calc.get(pid, 0.0), 1),
                    "fantasy": fs, "differenz": diff, "ok": "ja" if abs(diff) < 0.05 else "PRUEFEN"})
    out.sort(key=lambda r: (r["ok"] == "ja", -abs(r["differenz"])))
    return out, ["spieler_id", "name", "berechnet", "fantasy", "differenz", "ok"]


def main():
    rebuild = "--neu" in sys.argv
    now = datetime.now(TZ)
    os.makedirs(DATA, exist_ok=True)
    options = load_options()
    fantasy_pos = {pid: o.get("position") for pid, o in options.items()}

    schedule = load_schedule()
    game_rows = [] if rebuild else read_csv(F_GAMES)
    done = {int(r["game_id"]) for r in game_rows}

    new_games = [g for g in schedule if is_final(g, now) and g["game_id"] not in done]
    print(f"Neu auszuwerten: {len(new_games)} Spiele")
    for g in new_games:
        print(f"  ST{g['spieltag']} {g['datum']} {g['heim']} - {g['gast']} {g['tore_heim']}:{g['tore_gast']}")
        overview = parse_overview(fetch(g["url"]))
        box = parse_boxscore(fetch(g["url"].rstrip("/") + "/boxscore"))
        rows = score_game(g, box, overview, fantasy_pos)
        game_rows.extend({k: str(v) for k, v in r.items()} for r in rows)
        done.add(g["game_id"])

    for g in schedule:
        g["ausgewertet"] = "ja" if g["game_id"] in done else ""
    game_rows.sort(key=lambda r: (r["datum"], int(r["game_id"]), r["team_id"], r["pos"], r["name"]))

    write_csv(F_SCHEDULE, schedule, SCHEDULE_COLS)
    write_csv(F_GAMES, game_rows, GAME_COLS)
    matrix, mcols = build_matrix(schedule, game_rows, options)
    write_csv(F_MATRIX, matrix, mcols)
    check, ccols = build_check(schedule, game_rows, options) if options else ([], [])
    if check:
        write_csv(F_CHECK, check, ccols)
        bad = sum(r["ok"] != "ja" for r in check)
        print(f"Abgleich mit Fantasy-Punkten: {len(check) - bad} ok, {bad} pruefen")

    if os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") and os.environ.get("SHEET_ID"):
        from del_fantasy.sheets import push_tables
        tables = [
            (config.TAB_MATRIX, mcols, matrix),
            (config.TAB_SCHEDULE, SCHEDULE_COLS, schedule),
            (config.TAB_GAMES, GAME_COLS, game_rows),
        ]
        if options:
            master_cols = ["id", "name", "team", "position", "price", "german_license",
                           "country_abbr", "active", "stat_perc_picked", "stat_prev_score", "stat_score_sum"]
            tables.append((config.TAB_MASTER, master_cols, list(options.values())))
        if check:
            tables.append((config.TAB_CHECK, ccols, check))
        push_tables(tables, stamp=now.strftime("%d.%m.%Y %H:%M"))
        print("Google Sheet aktualisiert.")
    else:
        print("Kein Google-Zugang gesetzt, nur CSV-Dateien geschrieben.")


if __name__ == "__main__":
    main()
