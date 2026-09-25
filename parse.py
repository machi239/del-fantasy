"""Liest die HTML-Seiten der DEL-Webseite aus.

Drei Seitentypen:
  - Spielplan (/spiele bzw. /spiele/monat/<monat>)
  - Spielerstats eines Spiels (/statistik/spieldetails/<slug>/boxscore)
  - Uebersicht eines Spiels (/statistik/spieldetails/<slug>) mit Anzeigetafel und Ereignissen
"""
import re
from bs4 import BeautifulSoup

RE_TEAM_IMG = re.compile(r"team_(\d+)")
RE_PLAYER_LINK = re.compile(r"-(\d+)/details")
RE_PLAYER_IMG = re.compile(r"csm_(\d+)_")
RE_GAME_ID = re.compile(r"_(\d+)/?$")
RE_GOAL = re.compile(r"^Tor(?:\s*\(([^)]*)\))?\s+von\s+(.+?)\s+\(#(\d+)\)")
RE_ASSIST = re.compile(r"(.+?)\s+\(#(\d+)\)")
RE_PENALTY = re.compile(r"(\d+)\s*Min\.\s+Strafe\s+gegen\s+(.+?)\s+\(#(\d+)\)")


def _soup(html):
    return BeautifulSoup(html, "lxml")


def _clean(text):
    return re.sub(r"\s+", " ", (text or "").replace("\ufeff", "")).strip()


def _team_id(tag):
    img = tag.find("img", src=RE_TEAM_IMG) if tag else None
    if not img:
        return None
    return int(RE_TEAM_IMG.search(img["src"]).group(1))


def mmss_to_seconds(text):
    text = _clean(text)
    if not text or ":" not in text:
        return 0
    m, s = text.split(":")[:2]
    return int(m) * 60 + int(s)


def _int(text):
    text = _clean(text)
    try:
        return int(text)
    except ValueError:
        return 0


# --------------------------------------------------------------------------- Spielplan

def parse_month_urls(html):
    """Adressen aller Monatsseiten aus dem Auswahlfeld des Spielplans."""
    soup = _soup(html)
    sel = soup.find("select", id="select-month")
    if not sel:
        return []
    return [o["value"] for o in sel.find_all("option") if o.get("value")]


def parse_schedule(html):
    """Alle Spiele einer Spielplanseite, gespielt und ungespielt."""
    soup = _soup(html)
    games = []
    for row in soup.select("table tbody tr"):
        date_td = row.find("td", class_="team-schedule__date")
        if not date_td:
            continue
        versus = row.find_all("td", class_="team-schedule__versus")
        if len(versus) < 2:
            continue
        date_txt = _clean(date_td.get_text())            # "Freitag, 18.09.2026"
        d = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", date_txt)
        time_txt = _clean(row.find("td", class_="team-schedule__time").get_text())
        spieltag = _int(row.find("td", class_="team-schedule__compet").get_text())
        game = {
            "datum": f"{d.group(3)}-{d.group(2)}-{d.group(1)}" if d else "",
            "uhrzeit": time_txt,
            "spieltag": spieltag,
            "heim": _clean(versus[0].find(class_="team-meta__name").get_text()),
            "gast": _clean(versus[1].find(class_="team-meta__name").get_text()),
            "heim_id": _team_id(versus[0]),
            "gast_id": _team_id(versus[1]),
            "game_id": None, "url": "", "tore_heim": None, "tore_gast": None,
            "entscheidung": "", "status": "offen",
        }
        link = row.find("a", href=re.compile(r"/statistik/spieldetails/"))
        if link:
            game["url"] = link["href"]
            gid = RE_GAME_ID.search(link["href"])
            game["game_id"] = int(gid.group(1)) if gid else None
            txt = _clean(link.get_text(" "))
            sc = re.search(r"(\d+)\s*:\s*(\d+)", txt)
            if sc:
                game["tore_heim"], game["tore_gast"] = int(sc.group(1)), int(sc.group(2))
                game["status"] = "beendet"
                game["entscheidung"] = "SO" if "(SO)" in txt else "OT" if "(OT)" in txt else "REG"
        games.append(game)
    return games


# --------------------------------------------------------------------------- Spielerstats

def _player_id(td_name, td_img):
    a = td_name.find("a", href=RE_PLAYER_LINK)
    if a:
        return int(RE_PLAYER_LINK.search(a["href"]).group(1))
    img = (td_img or td_name).find("img", src=RE_PLAYER_IMG)
    if img:
        return int(RE_PLAYER_IMG.search(img["src"]).group(1))
    return None


def parse_boxscore(html):
    """Alle eingesetzten bzw. gemeldeten Spieler beider Teams mit ihren Einzelwerten."""
    soup = _soup(html)
    content = soup.find(id="gamedetail-content")
    players = []
    for card in content.find_all("div", class_="card--has-table"):
        team_id = _team_id(card.find(class_="card__header"))
        table = card.find("table")
        section = None
        for child in table.find_all(["thead", "tbody"], recursive=False):
            if child.name == "thead":
                label = _clean(child.find("th").get_text())
                section = {"Stürmer": "for", "Verteidiger": "def", "Torhüter": "goal"}.get(label, section)
                continue
            for tr in child.find_all("tr", recursive=False):
                tds = tr.find_all("td", recursive=False)
                if len(tds) < 7:
                    continue
                name_div = tds[2].find(class_="alc-player-info__name")
                raw_name = _clean(name_div.get_text(" ") if name_div else tds[2].get_text(" "))
                p = {
                    "team_id": team_id,
                    "spieler_id": _player_id(tds[2], tds[1]),
                    "nr": _int(tds[0].get_text()),
                    "name": _clean(raw_name.replace("(C)", "").replace("(A)", "").replace("*", "")),
                    "kapitaen": "(C)" in raw_name,
                    "starter": "*" in raw_name,
                    "pos_box": section,
                }
                if section == "goal":
                    min_s = mmss_to_seconds(tds[6].get_text())
                    p.update({
                        "gt": _int(tds[3].get_text()), "sv": _int(tds[4].get_text()),
                        "min_s": min_s, "gespielt": min_s > 0,
                    })
                else:
                    p.update({
                        "g": _int(tds[3].get_text()), "a": _int(tds[4].get_text()),
                        "pm": _int(tds[6].get_text()), "pim": _int(tds[7].get_text()),
                        "sog": _int(tds[8].get_text()), "blk": _int(tds[9].get_text()),
                        "fow": _int(tds[10].get_text()), "fol": _int(tds[11].get_text()),
                        "shifts": _int(tds[13].get_text()),
                        "toi_s": mmss_to_seconds(tds[14].get_text()),
                        "pp_s": mmss_to_seconds(tds[15].get_text()),
                        "sh_s": mmss_to_seconds(tds[16].get_text()),
                        "gespielt": True,
                    })
                players.append(p)
    return players


# --------------------------------------------------------------------------- Uebersicht

def parse_overview(html):
    """Anzeigetafel (inkl. OT/SO) und alle Ereignisse chronologisch."""
    soup = _soup(html)
    header = soup.find(id="gamedetail-header")
    board = header.find("table", class_="alc-event-box-score__scoreboard-table")
    cols = [_clean(th.get_text()) for th in board.find("thead").find_all("th")]
    rows = board.find("tbody").find_all("tr")
    teams = []
    for tr in rows:
        vals = [_clean(td.get_text()) for td in tr.find_all("td")]
        teams.append(dict(zip(cols, vals)))
    so = "SO" in cols and any(t.get("SO") not in ("", "0", None) for t in teams)
    ot = "OT" in cols
    decision = "SO" if so else "OT" if ot else "REG"

    events = []
    content = soup.find(id="gamedetail-content")
    table = content.find("table", class_="alc-table-stats__play-by-play")
    for tr in table.find_all("tr", class_="event"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 3:
            continue
        time_s = mmss_to_seconds(tds[0].get_text())
        team_id = _team_id(tds[1])
        name_div = tds[2].find(class_="alc-player-info__name")
        if name_div:
            light = name_div.find("span", class_="light")
            detail = _clean(light.get_text(" ")) if light else ""
            if light:
                light.extract()
            main = _clean(name_div.get_text(" "))
        else:
            desc = tds[2].find(class_="alc-desc")
            light = desc.find("span", class_="light") if desc else None
            detail = _clean(light.get_text(" ")) if light else ""
            if light:
                light.extract()
            main = _clean(desc.get_text(" ") if desc else tds[2].get_text(" "))

        g = RE_GOAL.match(main)
        if g:
            label = (g.group(1) or "").upper()
            assists = []
            if detail.startswith("auf Vorlage von"):
                body = detail[len("auf Vorlage von"):]
                assists = [int(n) for _, n in RE_ASSIST.findall(body)]
            events.append({
                "typ": "tor", "zeit_s": time_s, "team_id": team_id, "label": label,
                "nr": int(g.group(3)), "name": g.group(2), "assists": assists,
            })
            continue
        pen = RE_PENALTY.search(main)
        if pen:
            events.append({
                "typ": "strafe", "zeit_s": time_s, "team_id": team_id,
                "minuten": int(pen.group(1)), "nr": int(pen.group(3)), "name": pen.group(2),
            })
    events.sort(key=lambda e: e["zeit_s"])
    return {"entscheidung": decision, "anzeigetafel": teams, "ereignisse": events}
