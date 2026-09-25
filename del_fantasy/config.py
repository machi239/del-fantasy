"""Zentrale Einstellungen: Adressen, Punkteschema, Tabellennamen."""

BASE_URL = "https://www.penny-del.org"
SCHEDULE_URL = BASE_URL + "/spiele"
HEADERS = {
    "User-Agent": "del-fantasy-stats/1.0 (private, non-commercial; daily)",
    "Accept-Language": "de-DE,de;q=0.9",
}
REQUEST_PAUSE_S = 1.5  # Pause zwischen zwei Seitenabrufen, um die DEL-Seite zu schonen

# Punkteschema laut Fantasy-Regeln
SKATER = {
    "goal_for": 2.0,
    "goal_def": 3.0,
    "gwg": 1.0,          # Game Winning Goal, auch entscheidender Penalty (GWS)
    "shg": 1.0,          # Zusatzpunkt fuer Unterzahltor
    "assist": 1.0,
    "sog": 0.1,
    "blk": 0.7,
    "pim": -0.5,
    "plus_minus": 1.0,
}
GOALIE = {
    "win_reg": 3.0,
    "win_ot": 2.0,       # Sieg nach Verlaengerung oder Penaltyschiessen
    "loss_ot": 1.0,      # Niederlage nach Verlaengerung oder Penaltyschiessen
    "ga": -2.0,
    "save": 0.2,
    "shutout": 5.0,
    "assist": 1.0,
    "goal": 10.0,
    "pim": -0.5,
}

# Anzeige in der Matrix, wenn ein Spieler in einem Spiel nicht eingesetzt wurde
NOT_PLAYED = "–"

# Namen der Tabellenblaetter im Google Sheet
TAB_SCHEDULE = "Spielplan"
TAB_GAMES = "Spielerspiele"
TAB_MATRIX = "Matrix"
TAB_MASTER = "Stammdaten"
TAB_CHECK = "Abgleich"
