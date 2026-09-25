"""Setzt Spielplan, Spielerstats und Ereignisse zu Fantasy-Punkten pro Spieler zusammen."""
from collections import defaultdict
from .config import SKATER, GOALIE


def _game_winning_goal(events, winner_id, loser_goals, decision):
    """Nummer (Team, Rueckennummer) des Game Winning Goals bzw. entscheidenden Penaltys."""
    goals = [e for e in events if e["typ"] == "tor"]
    if decision == "SO":
        gws = [e for e in goals if "GWS" in e["label"]]
        return (gws[-1]["team_id"], gws[-1]["nr"]) if gws else None
    winner_goals = [e for e in goals if e["team_id"] == winner_id and "GWS" not in e["label"]]
    if len(winner_goals) > loser_goals:
        e = winner_goals[loser_goals]
        return (e["team_id"], e["nr"])
    return None


def score_game(game, box, overview, fantasy_pos=None):
    """Liefert eine Zeile pro gemeldetem Spieler mit Einzelwerten und Fantasy-Punkten.

    game       Eintrag aus parse_schedule (mit tore_heim/tore_gast/heim_id/gast_id)
    box        Ergebnis von parse_boxscore
    overview   Ergebnis von parse_overview
    fantasy_pos  optional {spieler_id: 'for'|'def'|'goal'} aus den Fantasy-Stammdaten
    """
    fantasy_pos = fantasy_pos or {}
    decision = overview["entscheidung"] or game.get("entscheidung") or "REG"
    events = overview["ereignisse"]
    h, g = game["heim_id"], game["gast_id"]
    th, tg = game["tore_heim"], game["tore_gast"]
    winner, loser = (h, g) if th > tg else (g, h)
    # Im Penaltyschiessen zaehlt das Schlussergebnis ein Tor mehr fuer den Sieger
    loser_goals = min(th, tg)
    goals_against = {h: tg, g: th}
    if decision == "SO":
        goals_against[loser] -= 1

    gwg = _game_winning_goal(events, winner, loser_goals, decision)
    shg = defaultdict(int)
    ev_goals = defaultdict(int)
    ev_assists = defaultdict(int)
    ev_pim = defaultdict(int)
    for e in events:
        key = (e["team_id"], e["nr"])
        if e["typ"] == "tor" and "GWS" not in e["label"]:
            ev_goals[key] += 1
            if "SH" in e["label"]:
                shg[key] += 1
            for a in e["assists"]:
                ev_assists[(e["team_id"], a)] += 1
        elif e["typ"] == "strafe":
            ev_pim[key] += e["minuten"]

    # Torhueter-Entscheidung: der Torhueter mit den meisten Minuten je Team
    main_goalie = {}
    for p in box:
        if p["pos_box"] == "goal" and p["gespielt"]:
            cur = main_goalie.get(p["team_id"])
            if cur is None or p["min_s"] > cur["min_s"]:
                main_goalie[p["team_id"]] = p
    goalies_used = defaultdict(int)
    for p in box:
        if p["pos_box"] == "goal" and p["gespielt"]:
            goalies_used[p["team_id"]] += 1

    rows = []
    for p in box:
        team = p["team_id"]
        key = (team, p["nr"])
        pos = fantasy_pos.get(p["spieler_id"]) or p["pos_box"]
        row = {
            "game_id": game["game_id"], "spieltag": game["spieltag"], "datum": game["datum"],
            "team_id": team, "gegner_id": g if team == h else h, "heim": team == h,
            "spieler_id": p["spieler_id"], "name": p["name"], "nr": p["nr"], "pos": pos,
            "gespielt": p["gespielt"], "starter": p["starter"],
            "g": 0, "a": 0, "pm": 0, "pim": 0, "sog": 0, "blk": 0, "gwg": 0, "shg": 0,
            "toi_s": 0, "pp_s": 0, "sh_s": 0, "shifts": 0,
            "gt": 0, "sv": 0, "min_s": 0, "entscheidung_tw": "", "shutout": False,
            "punkte": 0.0,
        }
        is_gwg = 1 if gwg == key else 0
        if p["pos_box"] == "goal":
            row.update({"gt": p["gt"], "sv": p["sv"], "min_s": p["min_s"],
                        "a": ev_assists[key], "g": ev_goals[key], "pim": ev_pim[key]})
            if p["gespielt"]:
                pts = GOALIE["ga"] * p["gt"] + GOALIE["save"] * p["sv"]
                pts += GOALIE["assist"] * row["a"] + GOALIE["goal"] * row["g"] + GOALIE["pim"] * row["pim"]
                if main_goalie.get(team) is p:
                    won = team == winner
                    if won and decision == "REG":
                        row["entscheidung_tw"], bonus = "S60", GOALIE["win_reg"]
                    elif won:
                        row["entscheidung_tw"], bonus = "S" + decision, GOALIE["win_ot"]
                    elif decision != "REG":
                        row["entscheidung_tw"], bonus = "N" + decision, GOALIE["loss_ot"]
                    else:
                        row["entscheidung_tw"], bonus = "N60", 0.0
                    pts += bonus
                    if goals_against[team] == 0 and goalies_used[team] == 1:
                        row["shutout"] = True
                        pts += GOALIE["shutout"]
                row["punkte"] = round(pts, 2)
        else:
            row.update({k: p[k] for k in ("g", "a", "pm", "pim", "sog", "blk",
                                          "toi_s", "pp_s", "sh_s", "shifts")})
            row["gwg"] = is_gwg
            row["shg"] = shg[key]
            goal_pts = SKATER["goal_def"] if pos == "def" else SKATER["goal_for"]
            pts = (goal_pts * p["g"] + SKATER["gwg"] * is_gwg + SKATER["shg"] * shg[key]
                   + SKATER["assist"] * p["a"] + SKATER["sog"] * p["sog"] + SKATER["blk"] * p["blk"]
                   + SKATER["pim"] * p["pim"] + SKATER["plus_minus"] * p["pm"])
            row["punkte"] = round(pts, 2)
        rows.append(row)
    return rows
