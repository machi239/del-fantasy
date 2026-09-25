"""Spiegelt die Tabellen in ein Google Sheet (Zugang ueber Service-Account)."""
import json
import os

import gspread


def _num(v):
    if v is None:
        return ""
    if isinstance(v, (int, float, bool)):
        return v
    s = str(v)
    try:
        f = float(s)
        return int(f) if f.is_integer() and "." not in s else f
    except ValueError:
        return s


def push_tables(tables, stamp=""):
    creds = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    gc = gspread.service_account_from_dict(creds)
    sh = gc.open_by_key(os.environ["SHEET_ID"])
    existing = {ws.title: ws for ws in sh.worksheets()}
    for title, cols, rows in tables:
        values = [cols] + [[_num(r.get(c)) for c in cols] for r in rows]
        n_rows, n_cols = max(len(values), 2), max(len(cols), 1)
        ws = existing.get(title)
        if ws is None:
            ws = sh.add_worksheet(title=title, rows=n_rows, cols=n_cols)
        else:
            ws.clear()
            ws.resize(rows=n_rows, cols=n_cols)
        ws.update(range_name="A1", values=values, value_input_option="RAW")
        ws.freeze(rows=1, cols=2 if title == "Matrix" else 0)
    info = existing.get("Info") or sh.add_worksheet(title="Info", rows=5, cols=2)
    info.update(range_name="A1", values=[["Letzte Aktualisierung", stamp]])
    # Standard-Blatt "Tabellenblatt1" entfernen, falls noch vorhanden und leer
    for name in ("Tabellenblatt1", "Sheet1"):
        ws = existing.get(name)
        if ws and len(sh.worksheets()) > 1 and not any(ws.get_all_values()):
            sh.del_worksheet(ws)
