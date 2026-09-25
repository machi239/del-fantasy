"""Projektion der erwarteten Fantasy-Punkte fuer einen Spieltag.

Bausteine
  Skater:   Punkte pro Spiel aus Saison und Form, auf den Preis als Vorwissen geschrumpft,
            angepasst an die Eiszeit-Entwicklung und das Matchup (erwartete Tore des Teams).
  Torhueter: Startwahrscheinlichkeit x [Sieg-/OT-Punkte + Saves - Gegentore + Shutout],
            gerechnet mit einem Poisson-Modell aus den erwarteten Toren beider Teams.
  Erwartete Tore: aus Wettquoten (falls uebergeben), sonst aus Toren/Gegentoren der Saison.
"""
import math
from collections import defaultdict

from .config import GOALIE

HOME_ADV = 1.05          # Heimvorteil auf die erwarteten Tore
PRIOR_GAMES = 4          # Gewicht des Vorwissens (Preis) in Spielen
TEAM_PRIOR_GAMES = 6     # Schrumpfung der Team-Staerken zum Liga-Schnitt
FORM_WEIGHT = 0.4        # Anteil der letzten 5 Spiele gegenueber dem Saisonschnitt
MATCHUP_EXP = 0.6        # Wie stark das Matchup auf Skater-Punkte durchschlaegt
TOI_CAP = (0.85, 1.20)   # Grenzen fuer den Eiszeit-Faktor
MAX_GOALS = 12


def _poisson(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def outcome_probs(xg_home, xg_away):
    """Wahrscheinlichkeiten nach 60 Minuten (Heimsieg, Unentschieden, Auswaertssieg)."""
    ph = [_poisson(k, xg_home) for k in range(MAX_GOALS)]
    pa = [_poisson(k, xg_away) for k in range(MAX_GOALS)]
    win = sum(ph[i] * pa[j] for i in range(MAX_GOALS) for j in range(MAX_GOALS) if i > j)
    tie = sum(ph[i] * pa[i] for i in range(MAX_GOALS))
    return win, tie, 1 - win - tie


def xg_from_odds(p_home60, p_away60, total):
    """Erwartete Tore beider Teams, passend zu Marktwahrscheinlichkeiten und Torerwartung."""
    target = p_home60 - p_away60
    lo, hi = 0.05, 0.95
    for _ in range(40):
        s = (lo + hi) / 2
        w, t, l = outcome_probs(total * s, total * (1 - s))
        if w - l < target:
            lo = s
        else:
            hi = s
    s = (lo + hi) / 2
    return total * s, total * (1 - s)


def team_ratings(schedule, game_rows):
    """Angriffs-/Abwehrstaerke und Schuesse gegen je Team, geschrumpft zum Liga-Schnitt."""
    gf, ga, n = defaultdict(float), defaultdict(float), defaultdict(int)
    for g in schedule:
        if g.get("status") != "beendet" or g.get("tore_heim") in ("", None):
            continue
        th, tg = int(g["tore_heim"]), int(g["tore_gast"])
        if g.get("entscheidung") == "SO":      # Penalty-Tor zaehlt nicht als echtes Tor
            if th > tg:
                th -= 1
            else:
                tg -= 1
        h, a = int(g["heim_id"]), int(g["gast_id"])
        gf[h] += th; ga[h] += tg; n[h] += 1
        gf[a] += tg; ga[a] += th; n[a] += 1
    games = sum(n.values())
    league = (sum(gf.values()) / games) if games else 3.0
    shots = defaultdict(float)
    shot_games = defaultdict(set)
    for r in game_rows:
        if r["pos"] != "goal":
            shots[int(r["gegner_id"])] += float(r["sog"] or 0)
            shot_games[int(r["gegner_id"])].add(r["game_id"])
    tot_sh = sum(shots.values())
    tot_gm = sum(len(v) for v in shot_games.values())
    league_sa = tot_sh / tot_gm if tot_gm else 28.0
    ratings = {}
    for t in set(n) | set(shots):
        k = TEAM_PRIOR_GAMES
        att = (gf[t] + k * league) / ((n[t] + k) * league)
        dfn = (ga[t] + k * league) / ((n[t] + k) * league)
        sa_n = len(shot_games[t])
        sa = (shots[t] + k * league_sa) / (sa_n + k)
        ratings[t] = {"att": att, "def": dfn, "sa": sa}
    return ratings, league, league_sa


def game_expectations(game, ratings, league, odds=None):
    """Erwartete Tore und Ausgangswahrscheinlichkeiten fuer ein Spiel."""
    h, a = int(game["heim_id"]), int(game["gast_id"])
    rh = ratings.get(h, {"att": 1, "def": 1})
    ra = ratings.get(a, {"att": 1, "def": 1})
    if odds:
        xh, xa = xg_from_odds(odds["p_home60"], odds["p_away60"], odds["total"])
        source = "Quoten"
    else:
        xh = league * rh["att"] * ra["def"] * HOME_ADV
        xa = league * ra["att"] * rh["def"] / HOME_ADV
        source = "Saisonwerte"
    w, t, l = outcome_probs(xh, xa)
    # Verlaengerung/Penalty: leicht zugunsten des staerkeren Teams
    share_h = xh / (xh + xa)
    ot_h = 0.5 + (share_h - 0.5) * 0.5
    return {
        h: {"xgf": xh, "xga": xa, "w60": w, "otw": t * ot_h, "otl": t * (1 - ot_h), "gegner": a, "heim": True},
        a: {"xgf": xa, "xga": xh, "w60": l, "otw": t * (1 - ot_h), "otl": t * ot_h, "gegner": h, "heim": False},
        "quelle": source,
    }


def _price_prior(players_hist, options):
    """Lineares Vorwissen: typische Punkte pro Spiel je Position und Preis."""
    pts = defaultdict(list)
    for pid, rows in players_hist.items():
        o = options.get(pid)
        if not o or o.get("position") == "goal":
            continue
        for r in rows:
            pts[(o["position"], int(o["price"]))].append(float(r["punkte"]))
    prior = {}
    for pos in ("for", "def"):
        xs, ys = [], []
        for (p, price), vals in pts.items():
            if p == pos:
                xs += [price] * len(vals)
                ys += vals
        if len(xs) >= 10:
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            var = sum((x - mx) ** 2 for x in xs) or 1
            b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var
            a = my - b * mx
        else:
            a, b = 0.3, 0.5
        for price in range(1, 6):
            prior[(pos, price)] = max(0.0, a + b * price)
    return prior


def project(round_games, schedule, game_rows, options, odds=None, availability=None,
            goalie_start=None):
    """Erwartete Punkte aller aufstellbaren Spieler fuer die Spiele eines Spieltags.

    round_games   Spiele des Ziel-Spieltags (Eintraege aus spielplan.csv)
    odds          {"HEIM_ID-GAST_ID": {"p_home60":.., "p_away60":.., "total":..}}
    availability  {spieler_id: Einsatzwahrscheinlichkeit 0..1} (0 = faellt aus)
    goalie_start  {spieler_id: Startwahrscheinlichkeit 0..1} (ueberschreibt die Historie)
    """
    odds = odds or {}
    availability = availability or {}
    goalie_start = goalie_start or {}
    ratings, league, league_sa = team_ratings(schedule, game_rows)

    exp = {}
    game_info = []
    for g in round_games:
        key = f"{int(g['heim_id'])}-{int(g['gast_id'])}"
        e = game_expectations(g, ratings, league, odds.get(key))
        exp.update({k: v for k, v in e.items() if k != "quelle"})
        game_info.append({"spiel": f"{g['heim']} - {g['gast']}", "datum": g["datum"],
                          "uhrzeit": g["uhrzeit"], "quelle": e["quelle"],
                          "xg_heim": round(e[int(g['heim_id'])]["xgf"], 2),
                          "xg_gast": round(e[int(g['gast_id'])]["xgf"], 2),
                          "p_heim_60": round(e[int(g['heim_id'])]["w60"], 3),
                          "p_gast_60": round(e[int(g['gast_id'])]["w60"], 3)})

    hist = defaultdict(list)
    for r in sorted(game_rows, key=lambda r: (r["datum"], int(r["game_id"]))):
        if r["spieler_id"] and str(r["gespielt"]) == "True":
            hist[int(r["spieler_id"])].append(r)
    dressed = defaultdict(list)   # alle Meldungen inkl. Backup-Torhueter
    for r in sorted(game_rows, key=lambda r: (r["datum"], int(r["game_id"]))):
        if r["spieler_id"]:
            dressed[int(r["spieler_id"])].append(r)
    team_games = defaultdict(set)
    for r in game_rows:
        team_games[int(r["team_id"])].add(r["game_id"])
    prior = _price_prior(hist, options)

    out = []
    for pid, o in options.items():
        if not o.get("active", True):
            continue
        team = int(o["team"])
        if team not in exp:
            continue
        e = exp[team]
        pos = o["position"]
        rows = hist.get(pid, [])
        n_team = len(team_games.get(team, set()))
        # Einsatzquote: in wie vielen Teamspielen war der Spieler dabei
        played_share = len(rows) / n_team if n_team else 0.0
        avail = availability.get(pid)
        note = []
        if pos == "goal":
            starts = [r for r in dressed.get(pid, []) if str(r["starter"]) == "True"]
            recent = [r for r in game_rows if int(r["team_id"]) == team and r["pos"] == "goal"
                      and str(r["starter"]) == "True"]
            recent_ids = sorted({r["game_id"] for r in recent}, key=int)[-5:]
            share = (sum(1 for r in starts if r["game_id"] in recent_ids) / len(recent_ids)
                     if recent_ids else 0.0)
            p_start = goalie_start.get(pid, share)
            if pid in goalie_start:
                note.append("Start bestaetigt" if goalie_start[pid] >= 0.99 else "Startquote manuell")
            sa = ratings.get(e["gegner"], {}).get("sa", league_sa)
            # Schuesse des Gegners skaliert mit seiner Torerwartung
            sa *= (e["xga"] / league) ** 0.5 if league else 1
            ga = e["xga"]
            sv = max(sa - ga, 0)
            p_so = math.exp(-ga) * 0.9
            core = (GOALIE["win_reg"] * e["w60"] + GOALIE["win_ot"] * e["otw"]
                    + GOALIE["loss_ot"] * e["otl"] + GOALIE["ga"] * ga
                    + GOALIE["save"] * sv + GOALIE["shutout"] * p_so)
            if avail is not None:
                p_start = min(p_start, avail)
            xp = p_start * core
            detail = {"p_start": round(p_start, 2), "p_sieg60": round(e["w60"], 2),
                      "xga": round(ga, 2), "xsv": round(sv, 1)}
        else:
            pts = [float(r["punkte"]) for r in rows]
            n = len(pts)
            base_prior = prior.get((pos, int(o["price"])), 1.0)
            if n:
                season = sum(pts) / n
                l5 = sum(pts[-5:]) / len(pts[-5:])
                obs = season * (1 - FORM_WEIGHT) + l5 * FORM_WEIGHT if n >= 5 else season
            else:
                obs = base_prior
            ppg = (n * obs + PRIOR_GAMES * base_prior) / (n + PRIOR_GAMES)
            toi = [int(r["toi_s"] or 0) for r in rows if int(r["toi_s"] or 0) > 0]
            toi_f = 1.0
            if len(toi) >= 3:
                recent = sum(toi[-3:]) / 3
                season_toi = sum(toi) / len(toi)
                toi_f = min(max(recent / season_toi, TOI_CAP[0]), TOI_CAP[1]) if season_toi else 1
                if toi_f >= 1.08:
                    note.append("Eiszeit steigt")
                elif toi_f <= 0.92:
                    note.append("Eiszeit sinkt")
            match_f = (e["xgf"] / league) ** MATCHUP_EXP if league else 1
            p_play = avail if avail is not None else (1.0 if not n_team else max(played_share, 0.0))
            if avail is None and n_team and played_share < 1:
                note.append(f"Einsatzquote {played_share:.0%}")
            xp = p_play * ppg * toi_f * match_f
            detail = {"ppg": round(ppg, 2), "toi_faktor": round(toi_f, 2),
                      "matchup": round(match_f, 2), "p_einsatz": round(p_play, 2)}
        if avail == 0:
            note.append("faellt aus")
        out.append({
            "spieler_id": pid, "name": o["name"], "team_id": team, "pos": pos,
            "preis": int(o["price"]), "dt_pass": bool(o.get("german_license")),
            "gewaehlt_pct": o.get("stat_perc_picked"), "spiele": len(rows),
            "xp": round(xp, 2), "gegner_id": e["gegner"], "heim": e["heim"],
            "hinweis": ", ".join(note), **detail,
        })
    out.sort(key=lambda r: -r["xp"])
    return out, game_info
