"""Liest Sperren aus der DEL-Strafbank (penny-del.org/fair-play/strafbank).

Fuer jede Meldung der laufenden Saison wird die Detailseite gelesen. Darin
werden Spielernamen aus data/fantasy_options.json gesucht und die Zahl der
gesperrten Spiele aus dem Satz um den Namen bestimmt ("drei Spiele Sperre",
"fuer zwei Spiele gesperrt", "automatische Sperre" = 1 Spiel).
Die Sperre gilt fuer die naechsten N Spiele des Teams ab dem Meldedatum.

Ergebnis: data/sperren.json
  {"stand": ..., "sperren": [{spieler_id, name, team_id, spiele, gemeldet,
    gesperrte_spiele: [game-Datum...], titel, url}]}
"""
import json
import os
import re
import sys
import time
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from del_fantasy import config  # noqa: E402

LISTE = config.BASE_URL + "/fair-play/strafbank"
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "sperren.json")
SAISONSTART = date(2026, 8, 1)

ZAHLEN = {"ein": 1, "einem": 1, "einen": 1, "eins": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5,
          "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12}
RE_ANZAHL = re.compile(
    r"(\d+|" + "|".join(ZAHLEN) + r")[\s-]+(?:weitere[n]?\s+)?(?:Pflicht)?spiel(?:e|en)?\b[^.]{0,40}?(?:Sperre|gesperrt)"
    r"|(?:Sperre|gesperrt)[^.]{0,30}?(\d+|" + "|".join(ZAHLEN) + r")\s+(?:Pflicht)?spiel",
    re.IGNORECASE)

session = requests.Session()
session.headers.update(config.HEADERS)


def hole(url):
    for versuch in range(3):
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            if versuch == 2:
                raise
            print(f"  Wiederhole {url}: {e}")
            time.sleep(3)


def zahl(s):
    s = s.lower()
    return int(s) if s.isdigit() else ZAHLEN.get(s)


def meldungen():
    soup = BeautifulSoup(hole(LISTE), "lxml")
    gesehen, out = set(), []
    for a in soup.find_all("a", href=re.compile(r"/fair-play/strafbank/detail/")):
        href = a["href"].split("?")[0]
        if href in gesehen:
            continue
        # Datum im umgebenden Eintrag suchen
        el, datum = a, None
        for _ in range(6):
            el = el.parent
            if el is None:
                break
            m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", el.get_text(" "))
            if m:
                datum = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                break
        titel = a.get_text(" ", strip=True) or a.get("title", "")
        if not titel:
            continue
        gesehen.add(href)
        out.append({"titel": titel, "url": config.BASE_URL + href if href.startswith("/") else href,
                    "gemeldet": datum})
    return out


def main():
    with open(os.path.join(DATA, "fantasy_options.json"), encoding="utf-8") as f:
        options = json.load(f)["options"]
    import csv
    with open(os.path.join(DATA, "spielplan.csv"), encoding="utf-8", newline="") as f:
        plan = list(csv.DictReader(f))
    teamnamen = {}
    for g in plan:
        teamnamen[g["heim_id"]] = g["heim"]
        teamnamen[g["gast_id"]] = g["gast"]

    sperren = []
    for m in meldungen():
        if m["gemeldet"] and m["gemeldet"] < SAISONSTART:
            continue
        if not re.search(r"sperr", m["titel"], re.IGNORECASE):
            continue
        html = hole(m["url"])
        soup = BeautifulSoup(html, "lxml")
        main_el = soup.find("article") or soup.find("main") or soup.body
        text = re.sub(r"\s+", " ", main_el.get_text(" "))
        if m["gemeldet"] is None:
            d = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", text)
            m["gemeldet"] = date(int(d.group(3)), int(d.group(2)), int(d.group(1))) if d else None
        if m["gemeldet"] is None or m["gemeldet"] < SAISONSTART:
            continue
        saetze = re.split(r"(?<=[.!?])\s+", text) + [m["titel"]]  # Detailtext vor Titel
        # Gesperrt sind die im Titel genannten Spieler. Nennt der Titel nur das Team
        # ("Sperre für Spieler des ERC Ingolstadt"), kommen Spieler dieses Teams aus dem Text infrage.
        im_titel = [o for o in options if o["name"] in m["titel"]]
        if im_titel:
            kandidaten = im_titel
        else:
            teams_im_titel = {tid for tid, tname in teamnamen.items()
                              if tname in m["titel"] or tname.split()[-1] in m["titel"]}
            kandidaten = [o for o in options if o["team"] in teams_im_titel and o["name"] in text]
            if not kandidaten:
                print(f"  Kein gesperrter Spieler erkannt: {m['titel']}")
        for o in kandidaten:
            name = o["name"]
            anzahl = None
            # Satz mit Namen (oder Nachname) bevorzugen, sonst Titel
            nachname = name.split()[-1]
            for s in saetze:
                if nachname in s:
                    # Jede Fundstelle dem naechstgelegenen Spieler im Satz zuordnen,
                    # damit z. B. eine reine Geldstrafe nicht die Sperre des Nebenmanns erbt
                    namen_pos = {k["name"].split()[-1]: [x.start() for x in re.finditer(re.escape(k["name"].split()[-1]), s)]
                                 for k in kandidaten if k["name"].split()[-1] in s}
                    def abstand(t, n, s=s):
                        # "N Spiele Sperre fuer X": Name steht danach; sonst ("X wurde fuer N Spiele
                        # gesperrt") steht er davor.
                        danach = s[t.end():t.end() + 6].lstrip().startswith("für")
                        werte = [(p - t.end()) if danach else (t.start() - p) for p in namen_pos[n]]
                        werte = [w for w in werte if w >= 0]
                        return min(werte) if werte else 10 ** 6
                    eigene = [t for t in RE_ANZAHL.finditer(s)
                              if min(namen_pos, key=lambda n: abstand(t, n)) == nachname]
                    if eigene:
                        t = min(eigene, key=lambda t: abstand(t, nachname))
                        anzahl = zahl(t.group(1) or t.group(2))
                        break
                    if re.search(r"automatische Sperre", s, re.I):
                        anzahl = 1
                        break
            if not anzahl and nachname in m["titel"]:
                t = RE_ANZAHL.search(m["titel"])
                anzahl = zahl(t.group(1) or t.group(2)) if t else None
            if not anzahl:
                print(f"  {name}: Sperre erwähnt, Anzahl Spiele nicht erkannt ({m['titel']})")
                continue
            team = o["team"]
            spiele = sorted((g for g in plan if team in (g["heim_id"], g["gast_id"])
                             and g["datum"] > m["gemeldet"].isoformat()),
                            key=lambda g: (g["datum"], g["uhrzeit"]))
            gesperrt = [g["datum"] for g in spiele[:anzahl]]
            sperren.append({"spieler_id": o["id"], "name": name, "team_id": team, "spiele": anzahl,
                            "gemeldet": m["gemeldet"].isoformat(), "gesperrte_spiele": gesperrt,
                            "titel": m["titel"], "url": m["url"]})
            print(f"  {name}: {anzahl} Spiel(e), gesperrt an {', '.join(gesperrt)}")
        time.sleep(1)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"stand": datetime.now().strftime("%Y-%m-%d %H:%M"), "sperren": sperren},
                  f, ensure_ascii=False, indent=1)
    print(f"{len(sperren)} Sperren gespeichert.")


if __name__ == "__main__":
    main()
