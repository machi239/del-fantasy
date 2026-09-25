"""Abgleich: berechnete Punkte fuer RBM - MAN (18.09., Penaltyschiessen)
gegen die Fantasy-Punkte (stat_score_sum - stat_prev_score aus dem Export vom 25.09.)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from del_fantasy.parse import parse_schedule, parse_boxscore, parse_overview
from del_fantasy.scoring import score_game

F = os.path.join(os.path.dirname(__file__), "fixtures")
# id: (fantasy_pos, erwartete Punkte Spiel 1)
EXPECTED = {
 487:("for",0.0),500:("goal",0.2),554:("for",2.3),1404:("for",2.0),1525:("for",-1.8),
 1609:("for",0.1),1611:("def",0.6),1856:("for",0.0),1903:("def",0.8),1985:("goal",0.0),
 2125:("for",-1.0),2411:("def",3.1),2489:("def",0.2),2501:("for",-0.9),2504:("for",1.8),
 2754:("def",0.0),2798:("for",-0.9),3428:("def",0.0),3429:("for",-2.0),3562:("for",3.2),
 3563:("def",0.2),3564:("def",0.0),3565:("for",0.0),3567:("for",0.4),4332:("for",1.1),
 4333:("def",4.0),4892:("goal",0.0),
 16:("def",0.0),80:("for",2.5),122:("goal",4.0),139:("for",-0.9),550:("for",6.2),
 551:("def",1.1),1168:("def",0.0),1364:("for",1.2),1753:("for",3.3),2117:("for",1.1),
 2133:("for",0.0),2174:("for",-0.8),2521:("def",0.0),2831:("goal",0.0),3275:("for",0.1),
 3309:("def",3.8),3426:("for",0.2),3446:("for",2.1),3556:("for",-0.2),3557:("def",2.7),
 4142:("def",1.0),4154:("goal",0.0),4325:("for",0.0),4326:("goal",0.0),4327:("def",1.1),
 4328:("def",-0.2),4329:("for",1.0),
}

sched = parse_schedule(open(f"{F}/spiele.html", encoding="utf-8").read())
game = next(g for g in sched if g["game_id"] == 4394)
print("Spiel:", game)
box = parse_boxscore(open(f"{F}/4394_boxscore.html", encoding="utf-8").read())
ov = parse_overview(open(f"{F}/4394_uebersicht.html", encoding="utf-8").read())
print("Entscheidung:", ov["entscheidung"], "| Ereignisse:", len(ov["ereignisse"]), "| Spieler:", len(box))
rows = score_game(game, box, ov, {k: v[0] for k, v in EXPECTED.items()})
seen, bad = set(), 0
for r in sorted(rows, key=lambda r: (r["team_id"], r["pos"])):
    exp = EXPECTED.get(r["spieler_id"], (None, None))[1]
    seen.add(r["spieler_id"])
    ok = exp is not None and abs(exp - r["punkte"]) < 0.05
    bad += not ok
    flag = "ok " if ok else "XX "
    print(f"{flag}{r['spieler_id']:>5} {r['name'][:22]:22} {r['pos']:4} gesp={int(r['gespielt'])} st={int(r['starter'])} "
          f"G{r['g']} A{r['a']} +/-{r['pm']:>2} PIM{r['pim']} SOG{r['sog']} BLK{r['blk']} GWG{r['gwg']} "
          f"GT{r['gt']} SV{r['sv']} {r['entscheidung_tw']:4} -> {r['punkte']:>5}  (Fantasy {exp})")
missing = [k for k, v in EXPECTED.items() if k not in seen and abs(v[1]) > 0.01]
print("\nAbweichungen:", bad, "| Fantasy-Punkte ohne Eintrag im Spielbericht:", missing)
