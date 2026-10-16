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

## Polymarket US books (first cycle, 2026-10-15 19:4xZ)

- The scan/screener "mid" can be one side of an empty book. All 12 AHL
  games screened read 0.85-0.90 on the first-listed team; the live books on
  three were 0.10/0.90, 0.12/0.89 and 0.11/0.89 against BetRivers lines near
  50/50 (forecasts 55c22b3b5fb5, dd83e0ed7be5, f3a5e46aabe2). A screener
  divergence on a placeholder book is not an edge: there is nothing to fill.
- `core/forecast.py record` prints the live bid/ask, so the forecast is also
  the quote. `strategy/tools/quote.py` reads the international CLOB, not US.
- FCS winner books are tight (spread 0.005-0.03) and sat within 0.03 of the
  FanDuel/BetRivers devig on 10 of 10 checked (largest: Harvard, Dartmouth,
  Alabama A&M at +0.02-0.03). The book-devig edge is not there on FCS
  winners. Spend research on news the line has not absorbed, close to kickoff.
- Same on FBS and NHL (20:3xZ cycle): 7 FBS winner books (spread 0.005)
  within 0.02 of the 9-book devig, 13 NHL books (spread 0.01) within 0.012.
  PM US h2h on leagues with deep outside books is a book mirror; one odds
  call per league is enough to confirm it, then spend the slot elsewhere
  (pre-lineup news, tier-2 forecasts, leagues the books price thinly).
- Same again on ATP Shanghai (3), WTA Wuhan (4), MLB LCS (2) and NBA
  preseason (2) at 21:2xZ: all 11 PM US mids within 0.01 of a 7-9 book
  devig, spreads 0.005-0.01. Four more lanes confirm the mirror; the
  remaining edge is timing (news before the books move), not price.
- Soccer too (23:0xZ): 5 Bundesliga 3-way books (spread 0.01) within 0.01 of
  the LowVig power devig. Soccer markets are one Yes/No market per outcome
  (`atc-<lg>-<home>-<away>-<date>-<team|draw>`), with `-fh-`/`-sh-` half
  variants (tier 2) and `tsc-`/`asc-` totals/spreads. Tier-2 FH books exist
  and are tight; my FH heuristic (FH win ~0.70x full-game fav) read Bayern
  FH 0.56 vs book 0.625 — pull an FH line before trusting the heuristic.
- EPL (23:5xZ): 5 winner books (spread 0.01) within 0.01 of LowVig devig.
  EPL `-sh-` (second half) books are empty: 0.02-0.05 bid / 0.94 ask on
  every outcome, so every 2H market tops the screener and none can fill.
- Cycle 00:4xZ 10-16: NDSU (now FBS/MWC) vs Nevada book 0.855/0.86 vs
  FD/DK/BOL devig ~0.864 — mirror again. BSL (Bandirma–Turk Telekom 0.10/0.90)
  and ACB (Burgos–Obradoiro 0.34/0.82) books are empty, and neither league
  has an odds-API key or a findable web line: forecast-only, skip research.
- Cycle 01:3xZ 10-16: NPB Climax (2), WNBA Finals (1) and boxing (Jalolov,
  Dubois-Wardley) books all within 0.02 of a 4-9 book power devig, spreads
  0.01 (boxing Jalolov 0.06). Ten leagues now confirm the mirror. KBO and CBA
  have no odds-API key. NPB ties settle at 0.50.
- The screened pool is alphabetical by league slug (strata rank on fields
  that are always 0 on `us`), so mlb, nba, nfl, nhl, soccer, ufc and wta
  never reach the screener. Until core changes, grep the saved scan output
  (`python3 core/scan.py ... > /tmp/scan.json`; ~9 MB, never commit it)
  for those leagues and research them directly.

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
