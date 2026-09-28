"""Offline-Test fuer scripts/odds.py mit einer nachgebauten OddsPapi-Antwort."""
import json, os, sys, tempfile, shutil
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import odds

def player(p, main=False): return {"players": {"0": {"active": True, "price": p, "mainLine": main}}}
MARKETS = [
    {"marketId": 151, "marketName": "Regular Time Result", "marketType": "1x2", "period": "fulltime", "sportId": 15,
     "handicap": 0, "outcomes": [{"outcomeId": 151, "outcomeName": "1"}, {"outcomeId": 152, "outcomeName": "X"}, {"outcomeId": 153, "outcomeName": "2"}]},
    {"marketId": 161, "marketName": "Winner (incl. overtime and penalties)", "marketType": "12", "period": "fulltime", "sportId": 15,
     "handicap": 0, "outcomes": [{"outcomeId": 161, "outcomeName": "1"}, {"outcomeId": 162, "outcomeName": "2"}]},
    {"marketId": 1010, "marketName": "Total Goals", "marketType": "totals", "period": "fulltime", "sportId": 15,
     "handicap": 5.5, "outcomes": [{"outcomeId": 1011, "outcomeName": "Over"}, {"outcomeId": 1012, "outcomeName": "Under"}]},
    {"marketId": 1020, "marketName": "Total Goals", "marketType": "totals", "period": "fulltime", "sportId": 15,
     "handicap": 6.5, "outcomes": [{"outcomeId": 1021, "outcomeName": "Over"}, {"outcomeId": 1022, "outcomeName": "Under"}]},
    {"marketId": 2000, "marketName": "1st Period Total", "marketType": "totals", "period": "p1", "sportId": 15,
     "handicap": 1.5, "outcomes": [{"outcomeId": 2001, "outcomeName": "Over"}, {"outcomeId": 2002, "outcomeName": "Under"}]},
]
def fixture(p1, p2, start, reg, ml, tot55, tot65):
    return {"participant1Name": p1, "participant2Name": p2, "startTime": start, "bookmakerOdds": {
        "pinnacle": {"bookmakerIsActive": True, "suspended": False, "markets": {
            "151": {"outcomes": {"151": player(reg[0]), "152": player(reg[1]), "153": player(reg[2])}},
            "161": {"outcomes": {"161": player(ml[0]), "162": player(ml[1])}},
            "1010": {"outcomes": {"1011": player(tot55[0], True), "1012": player(tot55[1], True)}},
            "1020": {"outcomes": {"1021": player(tot65[0]), "1022": player(tot65[1])}},
        }}}}
FIX = [fixture("Red Bull Munich", "Lowen Frankfurt", "2026-10-02T17:30:00.000Z", (1.55, 4.6, 5.0), (1.25, 3.9), (1.80, 2.00), (2.60, 1.48)),
       fixture("Kolner Haie", "Nurnberg Ice Tigers", "2026-10-02T17:30:00.000Z", (2.30, 4.2, 2.60), (1.85, 1.95), (1.70, 2.10), (2.40, 1.55)),
       fixture("Straubing Tigers", "SERC Wild Wings", "2026-09-27T14:30:00.000Z", (2.0, 4.0, 3.0), (1.6, 2.3), (1.9, 1.9), (2.5, 1.5))]

CALLS = []
def fake_get(path, **params):
    assert path == "odds-by-tournaments", path
    assert "bookmaker" in params and "bookmakers" not in params, params
    CALLS.append(params["bookmaker"])
    if params["bookmaker"] != "pinnacle":
        raise odds.ApiError("/odds-by-tournaments: HTTP 404: No fixtures found")
    return FIX

tmp = tempfile.mkdtemp()
try:
    shutil.copy(os.path.join(ROOT, "data", "spielplan.csv"), tmp)
    import csv
    rows = list(csv.DictReader(open(os.path.join(tmp, "spielplan.csv"), encoding="utf-8")))
    # zwei kuenftige Testspiele anhaengen
    rows.append({**rows[0], "spieltag": "5", "datum": "2026-10-02", "uhrzeit": "19:30", "heim": "EHC Red Bull München",
                 "gast": "Löwen Frankfurt", "heim_id": "12", "gast_id": "44", "status": "offen", "game_id": "", "url": "",
                 "tore_heim": "", "tore_gast": "", "entscheidung": "", "ausgewertet": ""})
    rows.append({**rows[-1], "heim": "Kölner Haie", "gast": "Nürnberg Ice Tigers", "heim_id": "11", "gast_id": "14"})
    with open(os.path.join(tmp, "spielplan.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    json.dump({"tournamentId": 999, "markets": MARKETS, "bookmakers": ["pinnacle", "bet365"]},
              open(os.path.join(tmp, "oddspapi_meta.json"), "w"))
    odds.F_META = os.path.join(tmp, "oddspapi_meta.json"); odds.F_OUT = os.path.join(tmp, "quoten.json")
    odds.F_HIST = os.path.join(tmp, "quoten_verlauf.csv"); odds.F_SCHEDULE = os.path.join(tmp, "spielplan.csv")
    odds.get = fake_get
    sys.argv.append("--immer")
    odds.main()
    assert CALLS == ["pinnacle", "bet365"], CALLS
    out = json.load(open(odds.F_OUT))["spiele"]
    m = out["12-44"]
    assert abs(m["p_home60"] + m["p_draw60"] + m["p_away60"] - 1) < 0.01
    assert m["p_home60"] > 0.55 and 5.3 < m["total"] < 6.0, m
    assert "11-14" in out and "6-15" not in out   # vergangenes Spiel wird ignoriert
    print("TEST OK", json.dumps(out, ensure_ascii=False))
    print(open(odds.F_HIST).read())
finally:
    shutil.rmtree(tmp)
