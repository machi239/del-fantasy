"""Laedt die Recherche-Datei (infos.json) aus Google Drive.

Die geplante Claude-Aufgabe schreibt ihre Recherche (Ausfaelle, Torhueter,
Risiken, Quellen) als Datei "del_fantasy_infos.json" in einen Drive-Ordner,
der fuer das Dienstkonto freigegeben ist. Dieses Modul sucht die neueste
Datei mit diesem Namen und gibt Inhalt und Aenderungszeit zurueck.
"""
import json
import os

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

DATEINAME = "del_fantasy_infos"
API = "https://www.googleapis.com/drive/v3/files"


def _session():
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.readonly"])
    return AuthorizedSession(creds)


def lade_infos():
    """Gibt (infos_dict, geaendert_iso, dateiname) zurueck oder (None, None, None)."""
    s = _session()
    r = s.get(API, params={
        "q": f"name contains '{DATEINAME}' and trashed = false",
        "fields": "files(id,name,mimeType,modifiedTime)",
        "orderBy": "modifiedTime desc",
        "pageSize": 10,
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    }, timeout=30)
    r.raise_for_status()
    files = r.json().get("files", [])
    if not files:
        return None, None, None
    f = files[0]
    if f["mimeType"].startswith("application/vnd.google-apps."):
        # Als Google-Dokument angelegt: als Text exportieren
        r = s.get(f"{API}/{f['id']}/export", params={"mimeType": "text/plain"}, timeout=30)
    else:
        r = s.get(f"{API}/{f['id']}", params={"alt": "media", "supportsAllDrives": "true"}, timeout=30)
    r.raise_for_status()
    text = r.content.decode("utf-8-sig").strip()
    # Falls die Datei in einem Markdown-Codeblock steht
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    text = text[text.find("{"): text.rfind("}") + 1]
    return json.loads(text), f["modifiedTime"], f["name"]
