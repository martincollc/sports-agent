# Sports playbook — AGENT-EDITABLE

Operator seed, 2026-10-15. v0, unproven. Rewrite every section from settled
evidence (`python3 core/lanestats.py`, `python3 core/score.py`, retros); cite
the evidence in each edit. Lessons carried over from Phil are marked
(Phil) and are hypotheses here until this repo's own record confirms them.

## The job

Find where fast research beats the Polymarket US price on sports, lane by
lane and league by league. Paper money, $1,000 per lane. The score that
matters is accuracy against the market (Brier delta < 0), not P&L: a couple
of longshot wins can hide a bad model, and a lane that loses money while
beating the market is a lane with bad luck, not a bad lane.

## Lanes and what each hunts

| Lane | Leagues | Where the edge should come from |
| --- | --- | --- |
| Football | NFL, college | Injury reports (Wed–Fri practice status), weather, QB changes; college: big lines on mismatched teams move late |
| Basketball | NBA, college, WNBA, VTB | Rest days and late scratches posted ~30–90 min before tip; back-to-backs |
| Baseball | MLB, KBO, NPB, WBC | Confirmed starting pitchers, lineups, wind/weather, bullpen usage the day before |
| Soccer | ~20 leagues and cups | Confirmed lineups ~60 min before kickoff, rotation in cup games, goalkeeper changes |
| Hockey | NHL | Starting goalie confirmation, back-to-backs |
| Combat | UFC, boxing | Weigh-in misses, short-notice replacements, late line moves at sharp books |
| Tennis | ATP, WTA | Withdrawals, injury retirements in previous round, surface switches |
| Esports | CS2, LoL, Valorant, R6 | Roster/stand-in changes, map pool, patch changes |

## Benchmark first (Phil, coverage precondition)

Before spending research on a candidate, spend ONE check establishing that a
sharp benchmark exists for this exact market: `python3 core/odds.py odds
<sport_key>` (the-odds-api, budgeted), a reliable injury/lineup source, or
an official schedule. No benchmark → forecast only, or drop it. Never build
an estimate on aggregator "prediction model" numbers.

## Bookmaker lines (Phil, book-devig)

- Devig with the power method (`strategy/tools/devig.py`), never
  proportional, and only from sharp books' lines.
- Phil's finding: clean bookmaker lines priced within ~0.02 of Polymarket on
  ~40 markets. A devig gap alone is rarely an edge. Use the line as the
  sanity benchmark; look for the news it has not absorbed yet.
- Match the market exactly: same game, same date, same line (−3.5 vs −3),
  same period (full game vs regulation), same settlement rules (overtime
  included?). A mismatch is the most common way a "big edge" is fake.

## Outside-view veto (Phil)

A claimed edge above 0.10 over the price is usually my error, not the
market's. Bet it only with a named, verifiable, market-specific fact the
price plausibly missed, and write that fact in the rationale. Otherwise
record the forecast and skip.

## Tiers

- Tier 1 (winner, spread, total): bet when the league thresholds allow.
- Tier 2 (halves, quarters, team totals, map lines): forecast on them every
  cycle that has spare research; core unlocks betting per league once 20
  settled forecasts are at or better than the market.
- Tier 3 (props): forecast only, sparingly, when one is directly informed
  by research already done (e.g. a confirmed QB change → passing yards).
- Futures: forecast only. Record a forecast when research on a league
  already gives a view; never spend a research slot on a future alone.

## Thresholds are dynamic while paper trading

Lane and league thresholds live in `strategy/params/<lane>.json` and change
only through `python3 core/params.py set` (rules in CYCLE.md step 3b). Use
the paper phase to find the right numbers fast: if a league's forecasts beat
the market but it rarely clears min_edge, the threshold may be too tight
(test bets tell you); if bets lose to the market, tighten it.

## Search traps (Phil)

- Stale or wrong-day results: check the date on every odds or injury page.
- Reverse lines: confirm which side a quoted spread belongs to.
- "Questionable" is not "out". Wait for the official inactive/lineup list
  when the market allows; otherwise price the uncertainty.
- Two related markets on the same game are correlated; core caps $10 per
  game across all of them.

## Abandon / rethink rule

A lane or league that, after 50+ settled bets, is still behind the market
(bet Brier delta > 0) and has not improved over its last two threshold
changes is a rethink: write the case in the retro and a proposal in
`journal/proposals.md` (the operator can switch it off in
`config/lanes.json`).
