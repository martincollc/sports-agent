"""Market discovery — AGENT-EDITABLE.

`core/scan.py` calls `queries()` and applies the protected admissibility
filters (banned classes, resolution floor, price bounds) to whatever comes
back. I decide WHAT TO LOOK FOR; core decides what is allowed.

Operator seed, revised 2026-10-15 after live probes of the Polymarket US
gateway from the operator Mac:
  * `sportsMarketTypes` does not filter game markets (detailed names -> 400,
    SPORTS_MARKET_TYPE_* names -> 0 rows), and sort orders are ignored.
  * `tagIds` (one per league, GET /v1/sports) filters reliably. Tag ids live
    in core/venue_us.py LEAGUE_TAGS; lane membership in core/lanes.py.
  * A game market's endDate is ~14 days after kickoff, so the window must
    reach past the horizon. `_us_games` handles that: core/venue_us.py
    pages every market of the listed league tags and keeps those whose
    gameStartTime falls in the next `hours` and whose type matches `types`.

One query per lane, so a slow or failing lane shows up on its own line in
scan's stderr. Tier 1 (winner, spread, total) and tier 2 (halves, quarters,
team totals, map lines) are fetched: tier 1 for bets, tier 2 to build the
forecast record that unlocks it. Props (tier 3) are left out on purpose:
thousands per week, nothing outside to check them against.

Cost: a big week is ~25,000 game markets across these leagues; scan reads
them at 500 per request. If scans run long, shorten `hours` or move a
low-value league out first, and say so in the retro.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "core"))
import lanes  # noqa: E402
import venue_us  # noqa: E402

TYPES = (r"(full_game_(winner|spread|total)|full_time_winner|^moneyline|"
         r"fight_winner|match_winner|series_winner|"
         r"(half|quarter|period|inning)_(winner|spread|total)|"
         r"team_points_full_game_total|series_map_handicap|series_total_(maps|games))$")
HOURS = 48


def queries(now, min_end, horizon, args, protected):
    out = []
    for lane in lanes.LANE_NAMES:
        leagues = sorted(c for c, (ln, _) in lanes.LEAGUES.items()
                         if ln == lane and c in venue_us.LEAGUE_TAGS)
        if leagues:
            out.append({"closed": "false", "_label": f"lane-{lane}",
                        "_us_games": {"leagues": leagues, "hours": HOURS, "types": TYPES}})
    return out
