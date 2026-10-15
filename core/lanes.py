#!/usr/bin/env python3
"""Sport lanes: which lane, league and tier a Polymarket US market belongs to.

PROTECTED CORE — the trading agent must not edit files under core/.

A lane is a sport (football, basketball, ...). Every bet and forecast is
tagged with its lane (the dashboard calls it the "model"), its league (the
second part of the market slug, e.g. `tec-nfl-...` -> nfl) and its market
tier:

  tier 1  full-game winner / spread / total, moneyline, fight winner
  tier 2  halves, quarters, periods, team totals, map/series lines
  tier 3  props: exact margin, first scorer, corners, exact score, stats ...
  future  season-long markets (champion, MVP, ...): forecast only, never bet

Lane switches live in config/lanes.json (operator-owned). A lane that is
switched off places no new bets; its open bets still settle.

Usage:
  python3 core/lanes.py classify --slug tec-nfl-... [--type football_team_full_game_winner]
  python3 core/lanes.py list
"""
import argparse
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
LANES_FILE = ROOT / "config" / "lanes.json"

# slug league code -> (lane, display name). Codes marked "guess" were read
# off the 2026-10-15 gateway count and not yet confirmed against a market
# title; a wrong guess only mislabels the league name, never the lane.
LEAGUES = {
    # football
    "nfl": ("football", "NFL"),
    "cfb": ("football", "College football"),
    # basketball
    "nba": ("basketball", "NBA"),
    "cbb": ("basketball", "College basketball"),
    "wnba": ("basketball", "WNBA"),
    "vtb": ("basketball", "VTB United League"),
    "acb": ("basketball", "Liga ACB"),
    # baseball
    "mlb": ("baseball", "MLB"),
    "kbo": ("baseball", "KBO"),
    "npb": ("baseball", "NPB"),
    "wbc": ("baseball", "World Baseball Classic"),
    # soccer
    "mls": ("soccer", "MLS"),
    "epl": ("soccer", "Premier League"),
    "lal": ("soccer", "La Liga"),
    "bun": ("soccer", "Bundesliga"),
    "sea": ("soccer", "Serie A"),  # guess
    "lg1": ("soccer", "Ligue 1"),  # guess
    "ucl": ("soccer", "Champions League"),
    "uel": ("soccer", "Europa League"),
    "uecl": ("soccer", "Conference League"),
    "uefa": ("soccer", "UEFA (other)"),
    "eflch": ("soccer", "EFL Championship"),
    "dfb": ("soccer", "DFB-Pokal"),
    "cdb": ("soccer", "Copa del Rey"),  # guess
    "lib": ("soccer", "Copa Libertadores"),  # guess
    "lmx": ("soccer", "Liga MX"),
    "bra": ("soccer", "Brasileirao"),
    "arg2": ("soccer", "Argentina second division"),  # guess
    "irlp": ("soccer", "League of Ireland Premier"),  # guess
    "lng": ("soccer", "lng (unconfirmed)"),  # guess
    "ncaaws": ("soccer", "NCAA women's soccer"),
    "football": ("soccer", "Soccer (other)"),
    # hockey
    "nhl": ("hockey", "NHL"),
    # combat
    "ufc": ("combat", "UFC"),
    "boxing": ("combat", "Boxing"),
    # tennis
    "atp": ("tennis", "ATP"),
    "wta": ("tennis", "WTA"),
    # esports
    "cs2": ("esports", "Counter-Strike 2"),
    "lol": ("esports", "League of Legends"),
    "valorant": ("esports", "Valorant"),
    "r6": ("esports", "Rainbow Six"),
    # added 2026-10-15 from the gateway's /v1/sports tag list
    "wcbb": ("basketball", "NCAA women's basketball"),
    "eurolg": ("basketball", "EuroLeague"),
    "eurocup": ("basketball", "EuroCup"),
    "lba": ("basketball", "Lega Basket Serie A"),
    "bsl": ("basketball", "Turkish Basketball Super League"),
    "nbl": ("basketball", "NBL (Australia)"),
    "cba": ("basketball", "CBA (China)"),
    "kbl": ("basketball", "KBL (Korea)"),
    "ahl": ("hockey", "AHL"),
    "khl": ("hockey", "KHL"),
    "shl": ("hockey", "SHL (Sweden)"),
    "liiga": ("hockey", "Liiga (Finland)"),
    "del": ("hockey", "DEL (Germany)"),
    "efl1": ("soccer", "EFL League One"),
    "efl2": ("soccer", "EFL League Two"),
    "bun2": ("soccer", "2. Bundesliga"),
    "lal2": ("soccer", "La Liga 2"),
    "ita2": ("soccer", "Serie B"),
    "fra2": ("soccer", "Ligue 2 (fra2)"),
    "lig2": ("soccer", "Ligue 2 (lig2)"),
    "ere": ("soccer", "Eredivisie"),
    "ligpor": ("soccer", "Primeira Liga"),
    "j1": ("soccer", "J1 League"),
    "j2": ("soccer", "J2 League"),
    "kl1": ("soccer", "K League 1"),
    "csl": ("soccer", "Chinese Super League"),
    "spl": ("soccer", "Saudi Pro League"),
    "tsl": ("soccer", "Turkish Super Lig"),
    "bel1": ("soccer", "Belgian Pro League"),
    "par1": ("soccer", "par1 (unconfirmed)"),
    "sud": ("soccer", "Copa Sudamericana"),
    "nwsl": ("soccer", "NWSL"),
    "uslc": ("soccer", "USL Championship"),
    "pfl": ("combat", "PFL"),
    "one": ("combat", "ONE Championship"),
    "ncaams": ("soccer", "NCAA men's soccer"),
    "dota2": ("esports", "Dota 2"),
    "cod": ("esports", "Call of Duty"),
}

# Listed on the venue but outside every lane: forecast only.
FORECAST_ONLY = {"f1", "nascar", "motogp", "pga", "pdc", "fide"}

LANE_NAMES = {
    "football": "Football", "basketball": "Basketball", "baseball": "Baseball",
    "soccer": "Soccer", "hockey": "Hockey", "combat": "Combat sports",
    "tennis": "Tennis", "esports": "Esports",
}

# sportsMarketType prefix -> lane, for league codes not in LEAGUES yet.
TYPE_PREFIX_LANE = [
    ("football_", "football"), ("basketball_", "basketball"),
    ("baseball_", "baseball"), ("soccer_", "soccer"), ("hockey_", "hockey"),
    ("ufc_", "combat"), ("boxing_", "combat"), ("tennis_", "tennis"),
    ("esports_", "esports"),
]

TIER1 = re.compile(r"(full_game_(winner|spread|total)|full_time_winner|"
                   r"^moneyline|fight_winner|match_winner|series_winner|"
                   r"game_winner)$")
TIER1_EXCLUDE = re.compile(r"team_points_full_game_total$")
TIER2 = re.compile(r"((half|quarter|period|inning)_(winner|spread|total)|"
                   r"team_points_full_game_total|series_map_handicap|"
                   r"series_total_(maps|games)|map_winner(_\d+)?|double_result)$")


def league_code(slug):
    parts = (slug or "").split("-")
    return parts[1].lower() if len(parts) > 1 else None


def tier(market_type):
    t = (market_type or "").strip().lower()
    if not t:
        return None
    if t in ("futures", "future") or t.endswith("_future"):
        return "future"
    if TIER1.search(t) and not TIER1_EXCLUDE.search(t):
        return 1
    if TIER2.search(t):
        return 2
    return 3


def classify(slug, market_type=None):
    """{'lane', 'league', 'league_name', 'tier', 'market_type'}; lane is None
    for forecast-only or unknown markets."""
    code = league_code(slug)
    lane, name = LEAGUES.get(code, (None, None))
    if lane is None and code not in FORECAST_ONLY:
        t = (market_type or "").lower()
        for prefix, ln in TYPE_PREFIX_LANE:
            if t.startswith(prefix):
                lane, name = ln, f"{code} (unlisted)"
                break
    return {"lane": lane, "league": code, "league_name": name or code,
            "tier": tier(market_type), "market_type": market_type}


def classify_market(m):
    """Classify a gamma-shaped market dict (core/venue_us.py gamma_view)."""
    return classify(m.get("slug"), m.get("sportsMarketType"))


def game_key(m, info=None):
    """Groups every market on the same game, for the per-game stake cap."""
    info = info or classify_market(m)
    gid = m.get("gameId")
    if gid:
        return f"{info['league']}:game:{gid}"
    start = m.get("gameStartTime")
    if start:
        # same league + same start time: one game, or a few games that kick
        # off together and share the cap (errs toward stricter)
        return f"{info['league']}:start:{start}"
    return f"slug:{m.get('slug')}"


def load_switches():
    """{lane: bool}. Missing file or lane -> off (fail closed)."""
    try:
        cfg = json.loads(LANES_FILE.read_text())
    except (OSError, ValueError):
        return {lane: False for lane in LANE_NAMES}
    lanes = cfg.get("lanes") or {}
    return {lane: bool((lanes.get(lane) or {}).get("enabled")) for lane in LANE_NAMES}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("classify")
    c.add_argument("--slug", required=True)
    c.add_argument("--type", default=None, help="sportsMarketType")
    sub.add_parser("list")
    args = ap.parse_args()
    if args.cmd == "classify":
        print(json.dumps(classify(args.slug, args.type), indent=2))
    else:
        sw = load_switches()
        out = {lane: {"name": LANE_NAMES[lane], "enabled": sw[lane],
                      "leagues": sorted(c for c, (ln, _) in LEAGUES.items() if ln == lane)}
               for lane in LANE_NAMES}
        out["_forecast_only"] = sorted(FORECAST_ONLY)
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
