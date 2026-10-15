"""Baut die Aufstellungsseite (HTML) aus dem Ergebnis von optimize.py.

Aufruf:
  python scripts/optimize.py --input infos.json --out aufstellung.json
  python scripts/seite.py --aufstellung aufstellung.json --infos infos.json --out aufstellung.html

Zusaetzliche Felder in infos.json (alle optional):
  "risiken":  [{"text": "...", "schwer": true}]      Hinweise vor dem Bully (fett: erster Satz)
  "notizen":  {"SPIELER_ID": "Start bestaetigt"}      kurze Kennzeichnung am Spieler
  "quellen":  [{"titel": "...", "url": "https://..."}] Quellen der Recherche
  "odds_quellen": {"HEIM_ID-GAST_ID": "bet365 3-Weg 1,45/5,00/5,90"}  Herkunft manueller Quoten

Die komplette infos.json wird unsichtbar in die Seite eingebettet (<script id="recherche">),
damit der naechste automatische Lauf die Recherche (z. B. Langzeitverletzte) uebernehmen kann.
"""
import argparse
import csv
import html
import json
import os
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
E = html.escape
WTAG = {"Mon": "Mo", "Tue": "Di", "Wed": "Mi", "Thu": "Do", "Fri": "Fr", "Sat": "Sa", "Sun": "So"}
POS = {"goal": "T", "def": "V", "for": "S"}
POSNAME = {"goal": "Torhüter", "def": "Verteidiger", "for": "Stürmer"}


def num(x, n=1):
    return f"{x:.{n}f}".replace(".", ",")


def load(path, default=None):
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


CSS = open(os.path.join(ROOT, "scripts", "seite.css"), encoding="utf-8").read() \
    if os.path.exists(os.path.join(ROOT, "scripts", "seite.css")) else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aufstellung", required=True)
    ap.add_argument("--infos")
    ap.add_argument("--data", default=os.path.join(ROOT, "data"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    d = load(a.aufstellung)
    infos = load(a.infos, {}) or {}
    quoten = load(os.path.join(a.data, "quoten.json"), {}) or {}
    with open(os.path.join(a.data, "spielplan.csv"), encoding="utf-8") as f:
        plan = [g for g in csv.DictReader(f) if g["spieltag"] == str(d["spieltag"])]
    keys = {f"{g['heim']} - {g['gast']}": f"{g['heim_id']}-{g['gast_id']}" for g in plan}
    starts = {}
    for g in plan:
        starts[g["heim"]] = starts[g["gast"]] = g["uhrzeit"]
    notizen = {str(k): v for k, v in (infos.get("notizen") or {}).items()}

    wd, rest = d["deadline"].split(" ", 1)
    dl_date, dl_time = rest.rsplit(" ", 1)
    dl_dt = datetime.strptime(rest, "%d.%m.%Y %H:%M")
    first_games = [g for g in plan if datetime.strptime(f"{g['datum']} {g['uhrzeit']}", "%Y-%m-%d %H:%M") == dl_dt]
    first_teams = ", ".join(sorted({g["heim"].split()[-1] if len(g["heim"].split()) > 1 else g["heim"] for g in first_games}))

    def tags(p):
        out = []
        late = starts.get(p["team"], "") > dl_time and dl_date == datetime.strptime(
            next(g["datum"] for g in plan if p["team"] in (g["heim"], g["gast"])), "%Y-%m-%d").strftime("%d.%m.%Y")
        if p["pos"] == "goal":
            ps = p.get("p_start") or 0
            out.append((f"Start ~{round(ps * 100)} %", ps < 0.8))
        for part in [x.strip() for x in (p.get("hinweis") or "").split(",") if x.strip()]:
            if part in ("Startquote manuell", "Start bestaetigt"):
                continue
            part = part.replace("Einsatzquote", "Einsätze").replace("faellt aus", "fällt aus")
            out.append((part, "sinkt" in part or "Einsätze" in part or "fällt" in part))
        if str(p["spieler_id"]) in notizen:
            out.append((notizen[str(p["spieler_id"])], False))
        return out

    def row(p):
        flag = '<span class="flag" title="Ohne deutschen Pass">A</span>' if not p["dt_pass"] else ""
        loc = "vs" if p["heim"] else "@"
        tg = "".join(f'<span class="tag{" warn" if w else ""}">{E(t)}</span>' for t, w in tags(p))
        return (f'<li class="pl"><span class="pos">{POS[p["pos"]]}</span>'
                f'<span class="who"><span class="nm">{E(p["name"])}{flag}</span>'
                f'<span class="tm">{E(str(p["team"]))} <span class="loc">{loc}</span> {E(str(p["gegner"]))}</span>'
                f'{"<span class=tags>" + tg + "</span>" if tg else ""}</span>'
                f'<span class="pk">{p["preis"]}<small>P</small></span><span class="xp">{num(p["xp"], 2)}</span></li>')

    L = d["aufstellung"]
    G = [p for p in L if p["pos"] == "goal"]
    D = [p for p in L if p["pos"] == "def"]
    F = [p for p in L if p["pos"] == "for"]

    def block(title, ps):
        return (f'<section class="line"><header><h3>{title}</h3><span class="meta">'
                f'{sum(p["preis"] for p in ps)} Pucks · {num(sum(p["xp"] for p in ps))} xP</span></header>'
                f'<ul>{"".join(row(p) for p in ps)}</ul></section>')
    lines = block("Torhüter", G) + "".join(
        block(f"{n}. Reihe", dd + ff) for n, dd, ff in
        [(1, D[0:2], F[0:3]), (2, D[2:4], F[3:6]), (3, D[4:6], F[6:9]), (4, D[6:7], F[9:12])])

    q_games = quoten.get("spiele", {})
    oq = infos.get("odds_quellen", {})
    rows = ""
    n_quoten = 0
    for g in sorted(d["spiele"], key=lambda g: (g["datum"], g["uhrzeit"], g["spiel"])):
        h, aw = g["spiel"].split(" - ")
        ph, pa = g["p_heim_60"], g["p_gast_60"]
        pot = max(0.0, 1 - ph - pa)
        key = keys.get(g["spiel"], "")
        if key in oq:
            note, ok = oq[key], True
        elif key in q_games:
            note = "Automatisch: " + " + ".join(q_games[key].get("buchmacher", [])) + f", Stand {quoten.get('stand', '')}"
            ok = True
        else:
            note, ok = "Keine Quoten, Saisonmodell", False
        n_quoten += ok
        day = datetime.strptime(g["datum"], "%Y-%m-%d").strftime("%a %d.%m.")
        day = WTAG[day[:3]] + day[3:]
        rows += (f'<tr><td class="t">{g["uhrzeit"]}<span class="d">{day}</span></td><td class="m"><b>{E(h)}</b><span>{E(aw)}</span></td>'
                 f'<td class="xg">{num(g["xg_heim"])} : {num(g["xg_gast"])}</td>'
                 f'<td class="bar"><div class="pb" role="img" aria-label="Heimsieg {round(ph*100)} %, Verlängerung {round(pot*100)} %, Auswärtssieg {round(pa*100)} %">'
                 f'<i class="h" style="width:{ph*100:.1f}%"></i><i class="o" style="width:{pot*100:.1f}%"></i><i class="a" style="width:{pa*100:.1f}%"></i></div>'
                 f'<div class="pl3"><span>{round(ph*100)} %</span><span>{round(pot*100)} %</span><span>{round(pa*100)} %</span></div></td>'
                 f'<td class="q"><span class="chip {"ok" if ok else "mod"}">{"Quoten" if ok else "Modell"}</span><span class="qn">{E(note)}</span></td></tr>')

    # Risiken: automatisch unsichere Torhueter, dazu die Recherche
    risks = []
    for p in G:
        if (p.get("p_start") or 0) < 0.8:
            risks.append({"text": f"{p['name']} ({p['team']}) ist als Starter nicht gesichert. "
                                  f"Geschätzte Startchance {round(p['p_start']*100)} %.", "schwer": False})
    risks += infos.get("risiken", [])
    if n_quoten < len(d["spiele"]):
        risks.append({"text": f"Für {len(d['spiele']) - n_quoten} von {len(d['spiele'])} Spielen liegen keine Quoten vor. "
                              "Dort rechnet das Saisonmodell.", "schwer": False})

    def risk_html(r):
        t = r["text"]
        first, _, more = t.partition(". ")
        body = f"<b>{E(first)}{'.' if more else ''}</b> {E(more)}" if more else f"<b>{E(t)}</b>"
        return f'<li class="{"hard" if r.get("schwer") else ""}">{body}</li>'
    risks_html = "".join(risk_html(r) for r in risks) or '<li class="none"><b>Keine besonderen Risiken bekannt.</b></li>'

    alts = ""
    for pos in ("goal", "def", "for"):
        items = "".join(
            f'<li><span class="nm">{E(p["name"])}{"<span class=flag>A</span>" if not p["dt_pass"] else ""}</span>'
            f'<span class="tm">{E(str(p["team"]))}</span><span class="pk">{p["preis"]}<small>P</small></span>'
            f'<span class="xp">{num(p["xp"], 2)}</span>'
            f'{("<span class=sub>Start ~" + str(round((p.get("p_start") or 0) * 100)) + " %</span>") if pos == "goal" else ""}</li>'
            for p in d["alternativen"][pos])
        alts += f'<div class="alt"><h3>{POSNAME[pos]}</h3><ol>{items}</ol></div>'

    # Neubewertung: Auf- und Abwertungskandidaten (data/bewertung.json aus bewertung.py)
    bew = load(os.path.join(a.data, "bewertung.json"), {}) or {}
    bew_html = ""
    if bew.get("positionen"):
        teamname = {}
        with open(os.path.join(a.data, "spielplan.csv"), encoding="utf-8") as f:
            for g in csv.DictReader(f):
                teamname[g["heim_id"]], teamname[g["gast_id"]] = g["heim"], g["gast"]
        im_kader = {str(p["spieler_id"]) for p in L}

        def kand(p, richtung):
            pfeil = "▲" if richtung == "auf" else "▼"
            kader = '<span class="tag">im Kader</span>' if p["id"] in im_kader else ""
            unsicher = p["sicherheit"] != "deutlich"
            schnitt = num(p["schnitt"], 2) if p["schnitt"] is not None else "–"
            return (f'<li><span class="nm">{E(p["name"])}{"<span class=flag>A</span>" if not p["dt_pass"] else ""}</span>'
                    f'<span class="tm">{E(teamname.get(p["team_id"], ""))} · Ø {schnitt} in {p["spiele"]} Sp.</span>'
                    f'<span class="bw {richtung}">{p["preis"]} {pfeil} {p["fair"]}</span>'
                    f'<span class="tags"><span class="tag{" warn" if unsicher else ""}">{"knapp" if unsicher else "deutlich"}</span>{kader}</span></li>')

        spalten = ""
        for pos in ("goal", "def", "for"):
            v = bew["positionen"][pos]
            auf = "".join(kand(p, "auf") for p in v["aufwertung"][:6]) or "<li>Keine Kandidaten.</li>"
            ab = "".join(kand(p, "ab") for p in v["abwertung"][:6]) or "<li>Keine Kandidaten.</li>"
            spalten += (f'<div class="alt bew"><h3>{POSNAME[pos]}</h3><h4 class="auf">Aufwertung</h4><ol>{auf}</ol>'
                        f'<h4 class="ab">Abwertung</h4><ol>{ab}</ol></div>')
        bew_html = (f'<section class="sec"><h2>Neubewertung nach Spieltag {bew["neubewertung_nach"]}</h2>'
                    f'<p>Schätzung nach Spieltag {bew["stand_spieltag"]}: Rangfolge je Position nach Fantasy-Punkten pro Spiel, '
                    f'hochgerechnet bis Spieltag {bew["neubewertung_nach"]}, Preisstufen so verteilt wie heute. Die DEL veröffentlicht '
                    f'ihre Formel nicht. <b>Aufwertung:</b> Wer den Spieler vorher kauft und hält, hat danach einen höher bewerteten '
                    f'Spieler zum alten Preis. <b>Abwertung:</b> Für Besitzer kostenlos, da der Verkauf den Kaufpreis erstattet; '
                    f'wer neu kauft, wartet besser auf den niedrigeren Preis.</p>'
                    f'<div class="alts">{spalten}</div></section>')

    src = [{"titel": "Spielerstatistiken: offizielle Spielberichte von penny-del.org, täglich ausgewertet",
            "url": "https://github.com/machi239/del-fantasy"}]
    if q_games:
        src.append({"titel": f"Wettquoten automatisch über OddsPapi (Stand {quoten.get('stand')})",
                    "url": "https://oddspapi.io"})
    src += infos.get("quellen", [])
    src_html = "".join(f'<li><a href="{E(s["url"])}" target="_blank" rel="noopener">{E(s["titel"])}</a></li>'
                       for s in src if s.get("url"))

    infos_json = json.dumps(infos, ensure_ascii=False).replace("</", "<\\/")
    page = f'''<title>DEL Fantasy Aufstellung</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Source+Sans+3:wght@400;600;700&display=swap">
<style>{CSS}
td.t .d{{display:block;font-family:var(--body);font-size:.75rem;font-weight:400;color:var(--muted)}}
.risks li.none{{border-left-color:var(--ok)}}
.upd{{font-size:.85rem;color:var(--muted)}}
.bew h4{{font-family:var(--display);text-transform:uppercase;letter-spacing:.08em;font-size:.8rem;margin:12px 0 4px}}
.bew h4.auf{{color:var(--ok)}} .bew h4.ab{{color:var(--red)}}
.bw{{font-family:var(--display);font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap}}
.bw.auf{{color:var(--ok)}} .bw.ab{{color:var(--red)}}
.bew li{{display:grid;grid-template-columns:1fr auto;grid-template-areas:"nm bw" "tm tm" "tg tg";column-gap:10px;row-gap:2px;align-items:baseline}}
.bew li .nm{{grid-area:nm}} .bew li .bw{{grid-area:bw}} .bew li .tm{{grid-area:tm}}
.bew li .tags{{grid-area:tg;display:flex;gap:4px;flex-wrap:wrap;margin-top:2px}}
</style>
<div class="wrap">
<header class="top">
  <span class="eyebrow">PENNY DEL Fantasy · Spieltag {d["spieltag"]}</span>
  <h1>Aufstellung Spieltag {d["spieltag"]}</h1>
  <div class="deadline">Deadline <b>{WTAG.get(wd, wd)} {dl_date[:6]} · {dl_time}</b> <span style="font-weight:500;color:var(--muted);font-size:1rem">erstes Bully{": " + E(first_teams) if first_teams else ""}</span></div>
  <div class="stats">
    <div class="stat"><span class="v">{num(d["xp_gesamt"])}</span><span class="l">erwartete Punkte</span></div>
    <div class="stat"><span class="v">{d["pucks"]} / {int(d["budget"])}</span><span class="l">Pucks</span></div>
    <div class="stat"><span class="v">{d["auslaender"]} / 9</span><span class="l">ohne dt. Pass</span></div>
    <div class="stat"><span class="v">{n_quoten} / {len(d["spiele"])}</span><span class="l">Spiele mit Quoten</span></div>
  </div>
  <p class="upd">Automatisch aktualisiert am {E(d["erstellt"])} Uhr. Die Zahl rechts am Spieler ist der erwartete Punktwert (xP) im Fantasy-Schlüssel.</p>
</header>
<section class="sec"><h2>Kader in Reihen</h2><div class="lines">{lines}</div></section>
<section class="sec"><h2>Risiken vor dem Bully</h2><ul class="risks">{risks_html}</ul></section>
<section class="sec"><h2>Die Spiele</h2>
  <div class="legend"><span style="--c:var(--blue)">Heimsieg nach 60 Min.</span><span style="--c:var(--ot)">Verlängerung / Penalty</span><span style="--c:var(--red)">Auswärtssieg nach 60 Min.</span></div>
  <div class="tablewrap"><table><thead><tr><th>Bully</th><th>Heim / Gast</th><th>Erw. Tore</th><th>Ausgang</th><th>Grundlage</th></tr></thead><tbody>{rows}</tbody></table></div>
</section>
<section class="sec"><h2>Beste Alternativen</h2>
  <p>Die stärksten Spieler je Position, die nicht in der Aufstellung stehen. Beim Tausch auf Pucks und das Ausländerlimit achten.</p>
  <div class="alts">{alts}</div></section>
{bew_html}
<section class="sec"><h2>Quellen</h2><ul class="sources">{src_html}</ul></section>
<script type="application/json" id="recherche">{infos_json}</script>
<footer>Erwartete Punkte sind Schätzungen aus Saisonschnitt, Form, Eiszeit-Trend, Matchup und Einsatzwahrscheinlichkeit. Bei Torhütern fließen Startchance, Siegchance, Gegentore und Saves ein.</footer>
</div>
'''
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Seite geschrieben: {a.out} ({len(page)} Zeichen)")


if __name__ == "__main__":
    main()
