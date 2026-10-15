"""Market discovery — AGENT-EDITABLE.

`core/scan.py` calls `queries()` and applies the protected admissibility
filters (banned classes, resolution floor, price bounds) to whatever comes
back. I decide WHAT TO LOOK FOR; core decides what is allowed.

Operator seed, 2026-10-15. What the Polymarket US gateway taught us that day
(a full paged count from the operator Mac, 20,000 sports markets):
  * `categories=sports` alone returns season futures first, and the gateway
    ignored orderBy/endDateMin on that query, so a scan never reached a game.
  * `sportsMarketTypes` is the filter that works. Each market's type also
    decides its tier (core/lanes.py): tier 1 = full-game winner/spread/total,
    tier 2 = halves/quarters/team totals, tier 3 = props, futures = forecast
    only.
  * One query per market type, so a type name the gateway rejects fails only
    its own query; scan logs it on stderr and moves on. Types seen live are
    marked; unmarked ones follow the naming pattern for sports whose games
    were not listed that day. Read scan's stderr and prune the dead ones.

Lanes only bet tier 1 until a league earns tier 2 on forecasts, so tier 1
gets the full page budget. Tier 2 is here to build that forecast record.
Props are not queried: there are thousands and no outside line to check
them against. Add a prop type here when a lane has a reason to study it.

Contract: return a list of dicts of query params (gamma-style keys are
translated by core/venue_us.py; `sportsMarketTypes` and `categories` pass
through). Optional `_label` names the query in scan's stderr. Anything
raising here falls back to core's default query, loudly.
"""

TIER1 = (
    # football
    "football_team_full_game_winner",      # seen: NFL, CFB
    "football_team_full_game_spread",      # seen: NFL 574, CFB 41
    "football_team_full_game_total",       # seen: NFL 92, CFB 25
    # basketball
    "basketball_team_full_game_winner",    # seen: NBA, VTB
    "basketball_team_full_game_spread",    # seen: NBA
    "basketball_team_full_game_total",     # seen: NBA
    # soccer
    "soccer_team_full_time_winner",        # seen: UEL, UECL, La Liga, more
    "soccer_team_full_game_spread",        # seen: UEL, UECL, La Liga
    "soccer_team_full_game_total",         # seen: UEL, UECL, La Liga
    # combat
    "ufc_fight_winner",                    # seen: 10
    "moneyline",                           # seen: boxing
    # pattern guesses for sports with no games listed on 2026-10-15
    "baseball_team_full_game_winner",
    "baseball_team_full_game_spread",
    "baseball_team_full_game_total",
    "hockey_team_full_game_winner",
    "hockey_team_full_game_spread",
    "hockey_team_full_game_total",
    "tennis_match_winner",
    "esports_series_winner",
)

TIER2 = (
    "football_team_first_half_spread",     # seen
    "football_team_first_half_total",      # seen
    "football_team_first_half_winner",     # seen
    "soccer_team_first_half_winner",       # seen
    "soccer_team_first_half_total",        # seen
    "esports_series_map_handicap",         # seen
    "esports_series_total_maps",           # seen
)


def queries(now, min_end, horizon, args, protected):
    iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    base = {"closed": "false", "categories": ["sports"],
            "end_date_min": iso(min_end), "end_date_max": iso(horizon)}
    out = [{**base, "_label": f"t1-{t}", "sportsMarketTypes": [t]} for t in TIER1]
    out += [{**base, "_label": f"t2-{t}", "sportsMarketTypes": [t]} for t in TIER2]
    return out
