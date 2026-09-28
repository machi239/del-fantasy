"""Wettquoten der DEL automatisch abrufen (OddsPapi, https://oddspapi.io).

Pro Lauf genuegt in der Regel EINE Abfrage (odds-by-tournaments). Beim allerersten Lauf
kommen einmalig drei Abfragen dazu (Ligen, Maerkte, Buchmacher), deren Ergebnis in
data/oddspapi_meta.json zwischengespeichert wird.

Ergebnis:
  data/quoten.json         aktuelle Wahrscheinlichkeiten je Spiel (Schluessel "HEIM_ID-GAST_ID")
  data/quoten_verlauf.csv  jede Abfrage als Zeile (fuer spaetere Auswertungen)

Benoetigt die Umgebungsvariable ODDSPAPI_KEY (GitHub-Secret).
Aufruf: python scripts/odds.py [--meta-neu]
"""
import csv
import json
import math
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
F_META = os.path.join(DATA, "oddspapi_meta.json")
F_OUT = os.path.join(DATA, "quoten.json")
F_HIST = os.path.join(DATA, "quoten_verlauf.csv")
F_SCHEDULE = os.path.join(DATA, "spielplan.csv")
BASE = "https://api.oddspapi.io/v4"
SPORT_ID = 15            # Eishockey
TZ = ZoneInfo("Europe/Berlin")
# Bevorzugte Buchmacher (max. 3 pro Abfrage). Pinnacle hat die schaerfsten Quoten.
WANTED_BOOKMAKERS = os.environ.get("ODDS_BOOKMAKERS", "pinnacle,bet365,unibet").split(",")
LOOKAHEAD_H = 72  # nur abfragen, wenn in diesem Zeitraum ein Spiel beginnt

# Erkennungsmerkmale der 14 Teams (ohne Umlaute, klein geschrieben)
TEAM_KEYS = {
    12: ["munchen", "munich", "red bull"], 44: ["frankfurt", "lowen"],
    3: ["eisbaren", "berlin"], 7: ["iserlohn"], 8: ["wolfsburg", "grizzly"],
    9: ["bremerhaven", "fischtown"], 13: ["augsburg"], 11: ["koln", "kolner", "cologne", "haie"],
    5: ["krefeld"], 15: ["schwenning", "wild wings"], 14: ["nurnberg", "nuremberg", "ice tigers"],
    6: ["straubing"], 1: ["ingolstadt"], 2: ["mannheim", "adler"],
}


class ApiError(RuntimeError):
    pass


def norm(text):
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t)


def team_id(name):
    n = norm(name)
    hits = [tid for tid, keys in TEAM_KEYS.items() if any(k in n for k in keys)]
    return hits[0] if len(hits) == 1 else None


def get(path, **params):
    """Abfrage ohne den Schluessel je in Logs oder Fehlermeldungen auszugeben."""
    key = os.environ.get("ODDSPAPI_KEY")
    if not key:
        raise ApiError("ODDSPAPI_KEY ist nicht gesetzt.")
    for attempt in range(3):
        try:
            r = requests.get(f"{BASE}/{path}", params=dict(params, apiKey=key), timeout=60)
        except requests.RequestException as exc:
            raise ApiError(f"/{path}: Verbindung fehlgeschlagen ({type(exc).__name__})") from None
        if r.status_code == 429:
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code != 200:
            body = r.text[:300].replace(key, "***")
            raise ApiError(f"/{path}: HTTP {r.status_code}: {body}")
        time.sleep(1.1)  # Cooldown der API: 1000 ms
        return r.json()
    raise ApiError(f"/{path}: Rate-Limit (429) nach 3 Versuchen")


# --------------------------------------------------------------------------- Metadaten

def load_meta(force=False):
    if os.path.exists(F_META) and not force:
        with open(F_META, encoding="utf-8") as f:
            return json.load(f)
    print("Lade einmalig Ligen, Maerkte und Buchmacher ...")
    tournaments = get("tournaments", sportId=SPORT_ID)
    ger = [t for t in tournaments if "german" in norm(t.get("categoryName")) or
           "germany" in norm(t.get("categorySlug"))]
    print("  Deutsche Eishockey-Wettbewerbe:",
          [(t.get("tournamentId"), t.get("tournamentName")) for t in ger])

    def is_del(t):
        n = norm(t.get("tournamentName"))
        s = norm(t.get("tournamentSlug"))
        return (n in ("del", "deutsche eishockey liga", "penny del") or s in ("del", "penny-del")) \
            and "2" not in n
    dels = [t for t in ger if is_del(t)]
    if len(dels) != 1:
        raise ApiError("DEL nicht eindeutig gefunden. Kandidaten stehen oben im Log.")
    markets = [m for m in get("markets") if m.get("sportId") == SPORT_ID]
    bookmakers = get("bookmakers")
    slugs = sorted({b.get("slug") or b.get("bookmaker") for b in bookmakers if isinstance(b, dict)} - {None})
    meta = {"tournamentId": dels[0]["tournamentId"], "tournamentName": dels[0].get("tournamentName"),
            "markets": markets, "bookmakers": slugs,
            "geladen": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with open(F_META, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return meta


def classify_markets(markets):
    """Ordnet die Eishockey-Maerkte von OddsPapi den benoetigten Typen zu.

    Zeitraum "fulltime" = 60 Minuten, "result" = inkl. Verlaengerung und Penaltyschiessen.
    reg1x2     "Regular Time Result" (1x2, fulltime)
    ml         "Winner (incl. overtime and penalties)" (moneyline, result)
    totals_reg "Total" (totals, fulltime), eine Marktnummer je Linie
    totals_ot  "Total (incl. overtime and penalties)" (totals, result)
    """
    out = {"reg1x2": {}, "ml": {}, "totals_reg": {}, "totals_ot": {}}
    for m in markets:
        if m.get("playerProp"):
            continue
        mtype = norm(m.get("marketType"))
        period = norm(m.get("period"))
        outs = {str(o["outcomeId"]): norm(o.get("outcomeName")) for o in m.get("outcomes", [])}
        mid = str(m["marketId"])
        entry = {"name": m.get("marketName"), "outcomes": outs}
        if mtype == "1x2" and period == "fulltime" and set(outs.values()) == {"1", "x", "2"}:
            out["reg1x2"][mid] = entry
        elif mtype == "moneyline" and period == "result" and set(outs.values()) == {"1", "2"}:
            out["ml"][mid] = entry
        elif mtype == "totals" and period in ("fulltime", "result") and set(outs.values()) == {"over", "under"}:
            entry["line"] = float(m.get("handicap") or 0)
            out["totals_reg" if period == "fulltime" else "totals_ot"][mid] = entry
    return out


# --------------------------------------------------------------------------- Umrechnung

def devig(prices):
    inv = [1 / p for p in prices]
    s = sum(inv)
    return [i / s for i in inv]


def poisson_over(line, lam):
    k = math.floor(line)
    cdf = sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(k + 1))
    return 1 - cdf


def lam_from_total(line, p_over):
    lo, hi = 0.5, 15.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if poisson_over(line, mid) < p_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def price(outcome):
    players = (outcome or {}).get("players") or {}
    p = players.get("0") or (next(iter(players.values())) if players else None)
    if not p or not p.get("active", True) or not p.get("price"):
        return None, False
    return float(p["price"]), bool(p.get("mainLine"))


def extract(fixture, mk):
    """Wahrscheinlichkeiten eines Spiels aus allen Buchmachern mitteln."""
    reg, ml, tot_reg, tot_ot, books = [], [], [], [], []
    for book, bo in (fixture.get("bookmakerOdds") or {}).items():
        if not bo.get("bookmakerIsActive", True) or bo.get("suspended"):
            continue
        markets = bo.get("markets") or {}
        used = False
        for mid, meta in mk["reg1x2"].items():
            m = markets.get(mid)
            if not m or m.get("marketActive") is False:
                continue
            by_name = {}
            for oid, oname in meta["outcomes"].items():
                pr, _ = price((m.get("outcomes") or {}).get(oid))
                by_name[oname] = pr
            trio = [by_name.get("1") or by_name.get("home"), by_name.get("x") or by_name.get("draw"),
                    by_name.get("2") or by_name.get("away")]
            if all(trio):
                reg.append(devig(trio)); used = True
        for mid, meta in mk["ml"].items():
            m = markets.get(mid)
            if not m or m.get("marketActive") is False:
                continue
            by_name = {oname: price((m.get("outcomes") or {}).get(oid))[0] for oid, oname in meta["outcomes"].items()}
            duo = [by_name.get("1") or by_name.get("home"), by_name.get("2") or by_name.get("away")]
            if all(duo):
                ml.append(devig(duo)[0]); used = True
        for kind, bucket in (("totals_reg", tot_reg), ("totals_ot", tot_ot)):
            lines = []
            for mid, meta in mk[kind].items():
                m = markets.get(mid)
                if not m or m.get("marketActive") is False:
                    continue
                ov = un = None
                main = False
                for oid, oname in meta["outcomes"].items():
                    pr, is_main = price((m.get("outcomes") or {}).get(oid))
                    main = main or is_main
                    if oname.startswith("over") or oname in ("o", "+"):
                        ov = pr
                    elif oname.startswith("under") or oname in ("u", "-"):
                        un = pr
                if ov and un and meta["line"] % 1 == 0.5:  # nur x,5-Linien (kein Einsatz zurueck)
                    p_over = devig([ov, un])[0]
                    lines.append((main, abs(p_over - 0.5), meta["line"], p_over))
            if lines:
                lines.sort(key=lambda x: (not x[0], x[1]))   # Hauptlinie, sonst die ausgeglichenste
                _, _, line, p_over = lines[0]
                bucket.append(lam_from_total(line, p_over)); used = True
        if used:
            books.append(book)
    res = {"buchmacher": books}
    if reg:
        res["p_home60"] = round(sum(r[0] for r in reg) / len(reg), 3)
        res["p_draw60"] = round(sum(r[1] for r in reg) / len(reg), 3)
        res["p_away60"] = round(sum(r[2] for r in reg) / len(reg), 3)
    if ml:
        res["p_home_inkl_ot"] = round(sum(ml) / len(ml), 3)
    if tot_reg:
        res["total"] = round(sum(tot_reg) / len(tot_reg), 2)
    elif tot_ot:
        # Linie inkl. Verlaengerung: im Schnitt faellt dort gut 0,1 Tor mehr
        res["total"] = round(sum(tot_ot) / len(tot_ot) - 0.12, 2)
    return res


# --------------------------------------------------------------------------- Ablauf

def main():
    meta = load_meta(force="--meta-neu" in sys.argv)
    mk = classify_markets(meta["markets"])
    print("Maerkte:", {k: [f"{i}:{v['name']}" + (f" ({v['line']})" if "line" in v else "") for i, v in d.items()][:30]
                       for k, d in mk.items()})
    with open(F_SCHEDULE, encoding="utf-8") as f:
        schedule = list(csv.DictReader(f))
    open_games = {(g["datum"], int(g["heim_id"]), int(g["gast_id"])): g
                  for g in schedule if g["status"] != "beendet" and g["heim_id"]}

    # Kontingent schonen: nur abfragen, wenn in den naechsten 72 Stunden ein Spiel ansteht
    now_local = datetime.now(TZ)
    soon = [g for g in open_games.values()
            if 0 <= (datetime.strptime(f"{g['datum']} {g['uhrzeit'] or '19:30'}", "%Y-%m-%d %H:%M")
                     .replace(tzinfo=TZ) - now_local).total_seconds() <= LOOKAHEAD_H * 3600]
    if not soon and "--immer" not in sys.argv:
        print(f"Kein Spiel in den naechsten {LOOKAHEAD_H} Stunden - keine Abfrage.")
        return

    # Der Liga-Endpunkt erlaubt genau einen Buchmacher pro Abfrage (Parameter "bookmaker")
    known = meta.get("bookmakers") or []
    books = [b for b in WANTED_BOOKMAKERS if not known or b in known]
    merged = {}
    used_books = []
    for book in books:
        try:
            data = get("odds-by-tournaments", tournamentIds=meta["tournamentId"],
                       bookmaker=book, oddsFormat="decimal", verbosity=3)
        except ApiError as exc:
            print(f"  {book}: keine Quoten ({str(exc)[:120]})")
            continue
        fixtures = data if isinstance(data, list) else data.get("fixtures") or data.get("data") or [data]
        n = 0
        for fx in fixtures:
            fid = fx.get("fixtureId") or f"{fx.get('participant1Name')}|{fx.get('startTime')}"
            if fid not in merged:
                merged[fid] = {k: v for k, v in fx.items() if k != "bookmakerOdds"}
                merged[fid]["bookmakerOdds"] = {}
            merged[fid]["bookmakerOdds"].update(fx.get("bookmakerOdds") or {})
            n += 1
        print(f"  {book}: {n} Spiele")
        used_books.append(book)
    fixtures = list(merged.values())
    books = used_books

    now = datetime.now(timezone.utc)
    result, hist, unmatched = {}, [], []
    for fx in fixtures:
        h = team_id(fx.get("participant1Name"))
        a = team_id(fx.get("participant2Name"))
        start = fx.get("startTime")
        if not (h and a and start):
            unmatched.append(f"{fx.get('participant1Name')} - {fx.get('participant2Name')}")
            continue
        local = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(TZ)
        if (local.strftime("%Y-%m-%d"), h, a) not in open_games:
            unmatched.append(f"{fx.get('participant1Name')} - {fx.get('participant2Name')} {local:%d.%m. %H:%M}")
            continue
        probs = extract(fx, mk)
        if not ({"p_home60", "p_home_inkl_ot"} & probs.keys()):
            continue
        key = f"{h}-{a}"
        g = open_games[(local.strftime("%Y-%m-%d"), h, a)]
        result[key] = {"spiel": f"{g['heim']} - {g['gast']}", "beginn": local.strftime("%d.%m.%Y %H:%M"),
                       **probs}
        hist.append({"abgerufen": now.isoformat(timespec="minutes"), "schluessel": key,
                     "spieltag": g["spieltag"], "beginn": local.isoformat(timespec="minutes"),
                     **{k: probs.get(k, "") for k in ("p_home60", "p_draw60", "p_away60",
                                                      "p_home_inkl_ot", "total")},
                     "buchmacher": "+".join(probs["buchmacher"])})

    out = {"stand": now.astimezone(TZ).strftime("%d.%m.%Y %H:%M"), "quelle": "OddsPapi",
           "buchmacher_abgefragt": books, "spiele": result}
    with open(F_OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    new_file = not os.path.exists(F_HIST)
    cols = ["abgerufen", "schluessel", "spieltag", "beginn", "p_home60", "p_draw60", "p_away60",
            "p_home_inkl_ot", "total", "buchmacher"]
    with open(F_HIST, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new_file:
            w.writeheader()
        w.writerows(hist)

    print(f"Quoten fuer {len(result)} Spiele gespeichert ({out['stand']}).")
    if not result:
        print("Hinweis: Noch keine Quoten fuer anstehende Spiele. Buchmacher stellen DEL-Quoten oft erst 1-2 Tage vorher ein.")
    for k, v in result.items():
        print(f"  {v['beginn']}  {v['spiel']:48} "
              f"60min {v.get('p_home60', '-')}/{v.get('p_draw60', '-')}/{v.get('p_away60', '-')}  "
              f"inkl.OT {v.get('p_home_inkl_ot', '-')}  Tore {v.get('total', '-')}  [{'+'.join(v['buchmacher'])}]")
    if unmatched:
        print("Nicht zugeordnet (vergangene Spiele oder unbekannte Namen):", unmatched[:10])


if __name__ == "__main__":
    try:
        main()
    except ApiError as exc:
        print(f"FEHLER: {exc}")
        sys.exit(1)
