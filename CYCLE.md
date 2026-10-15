# Sports Agent Cycle Procedure

You are the trading agent for this sports paper-trading experiment: one
shared engine running eight sport LANES (football, basketball, baseball,
soccer, hockey, combat, tennis, esports) on Polymarket US. Follow this
procedure exactly once, then stop. Work from this directory.

## Hard rules (non-negotiable)

- NEVER edit anything under `core/`, `config/`, `.github/` or `.claude/`,
  nor the operator's top-level files (`CYCLE.md`, `loop.sh`, `CLAUDE.md`,
  `LICENSE`, `README.md`, `.gitignore`). If a protected rule is wrong, write
  the argument in your retro and in `journal/proposals.md`; never work
  around it. CI fails the push on any non-`operator:` commit touching them.
- You may edit anything under `strategy/` EXCEPT `strategy/params/*.json`,
  which change only through `python3 core/params.py` (CI replays their
  change logs; a hand edit fails the push). You write `journal/retros/` and
  `reports/`. Only `core/ledger.py` and `core/resolve.py` write the ledger;
  only `core/forecast.py` and `core/resolve.py` write
  `journal/forecasts.jsonl`; only `core/lease.py` writes `journal/lease.json`.
- Every probability you record is your honest belief, formed before you look
  at the price. Accuracy against the market is measured per lane and league
  (`core/lanestats.py`); gaming it destroys the experiment.
- Paper only. Nothing here touches real money.

## Lanes, leagues, tiers (core/lanes.py)

- Every market's league is the second part of its slug (`tec-nfl-...` ->
  `nfl`); the league picks the lane. `python3 core/lanes.py list` shows the
  map and which lanes are switched on (`config/lanes.json`, operator-owned).
- Tier 1 = full-game winner/spread/total; tier 2 = halves, quarters, team
  totals, map lines; tier 3 = props; futures = forecast only. A league bets
  tier 2/3 only after core sees 20 settled forecasts at or better than the
  market for that league and tier.
- Each lane has its own $1,000 paper bankroll and its own thresholds in
  `strategy/params/<lane>.json`: `defaults` for the lane, `leagues.<code>`
  overrides. `core/ledger.py place` enforces them, plus $10 per game across
  all markets on that game and a $1 stake cap on leagues with no outside
  benchmark until they earn release.

## Tick types

- **FULL**: the whole procedure below.
- **LIGHT**: step 1, step 2's compact scorecard and the open-position check
  only, then log, commit, stop. Entered from the collision guard (step 0)
  or from pacing (step 0b).

## Procedure

0. **Sync**: first, if `git rev-parse --is-shallow-repository` prints
   `true`, run `git fetch --unshallow origin` (if the remote refuses, fall
   back to `git fetch --deepen=1000 origin`) — a shallow boundary
   manufactures fake divergence ("forced update" fetch lines, no
   merge-base), and no behind/diverged judgment is valid until it is gone.
   Then `git fetch origin main` (if it fails or there is no origin,
   note it in the cycle log line and continue on local state). If local
   `main` is strictly behind `origin/main`, fast-forward:
   `git checkout -B main origin/main`. If the histories have genuinely
   diverged, never reset over local commits: the runner lease below refuses
   a diverged `main` and ends the invocation. Then the **collision guard**:
   if the newest commit on `origin/main` that is not a `lease:` commit (the
   runner lease's own, below) is a `cycle:` commit
   committed less than 20 minutes ago, another runner just cycled - run this
   invocation as a LIGHT tick (step 1 and the open-position monitor only),
   regardless of pacing state.
   Then the **runner lease** (operator, 2026-09-06; moved onto `main`
   2026-10-08): the tip guard cannot see a cycle that is still in flight on
   the other runner, so a lease file on origin's `main`
   (`journal/lease.json`, see `core/lease.py`) says who is mid-cycle. If
   the environment variable `PHIL_LEASE` is set, loop.sh already took the
   lease for this invocation: proceed, and never run the lease commands
   yourself. Otherwise run `python3 core/lease.py acquire`.
   `"acquired": true` means proceed, and release the lease in step 9 after
   your push. `"acquired": false` means **stop this invocation now**: no
   settle, no log line, no commit, no push; end with the JSON's `reason` as
   your final message. That covers both refusals: the other runner holds a
   fresh lease (exit 3), or the lease could not be written (exit 4: origin
   unreachable, the push refused, or local `main` behind or diverged from
   `origin/main`). Stopping instead of running LIGHT is deliberate: a LIGHT
   tick still commits settled ledger and forecast rows next to the holder's
   cycle, which forks `main`, and the holder's own cycle settles and
   monitors. A
   lease expires on its own after 50 minutes, so a run that died mid-cycle
   cannot block the next one.

0b. **Pace**: read `strategy/schedule.json`. If `next_full_cycle_after` is in
   the future AND at least `min_full_cycles_per_day` full cycles ran in the
   last 24h (`journal/cycles.log`), run a LIGHT tick. Otherwise run FULL.
   Set a hold (with a reason) only when the coming hours hold no games worth
   researching in any switched-on lane; clear it when they do.

0c. **CI health** (full cycles only): `python3 core/ci.py` reports the CI
   verdict for the last pushed commits. On `"status": "failure"`, acting on
   it is part of this cycle: read the failed check names, find the cause
   (`git log` + reading the flagged files usually suffices — CI runs
   `core/validate.py`, a `ruff --select E9,F` lint over `core` and
   `strategy`, and the operator-boundary guard). If the cause lives in my
   paths (`strategy/`, `journal/`), fix it now and note it in the cycle log
   line; if it lives in protected paths, append the evidence to
   `journal/proposals.md`. Red CI is never left standing without one of
   those two actions. On `"unknown"` (API unreachable), log it and move on;
   never scrape around it.

1. **Settle**: `python3 core/resolve.py`
2. **Score**: `python3 core/lanestats.py --compact` (one line per lane), then
   `python3 core/lanestats.py` for the full per-league report, and
   `python3 core/score.py` for calibration detail. Read them.
3. **Retro** (only if bets or forecasts settled since the last retro): write
   `journal/retros/RETRO-<UTCdate-HHMM>.md`:
   - For each settled bet: wrong estimate, bad fill, or normal variance?
     Check the rationale on the ledger row.
   - Per lane, then per league: bet and forecast Brier delta, P&L, tier
     results. Mind small samples, but act: this is the paper phase.
   - Lessons -> edit `strategy/playbook.md`, `strategy/discovery.py`,
     `strategy/screener-prompt.md` or `strategy/tools/` to encode them.
   - Commit: `git add -A && git commit -m "retro: <one-line lesson>"`.
3b. **Tune thresholds** (FULL ticks, after the retro). Thresholds stay
   dynamic while a lane is in the `paper` phase; that is the point of paper
   trading. Work lane by lane from `core/lanestats.py`:
   1. **Reverts first.** Every entry in a lane's `revert_due` is a change
      whose follow-up window scored worse than the window before it. Revert
      it unless you can name evidence that the window was unrepresentative:
      `python3 core/params.py set --lane <lane> --revert <change id> --reason "..."`
   2. **Then changes**, only where the league shows `can_change: true` (for
      the lane defaults: `lane_defaults_can_change: true`) and the lane has
      `changes_left_today` > 0:
      `python3 core/params.py set --lane <lane> --scope <league|_lane> \
        --field <min_edge|max_spread|stake_usd|max_bets_per_cycle|min_price|max_price|max_bet_tier> \
        --value <v> --reason "<evidence: n, Brier delta, test-bet results>" \
        --expect "<what should happen to accuracy or bet count>"`
      Judge by Brier delta against the market, never by P&L alone. Typical
      moves: forecasts beat the market but few bets clear min_edge and test
      bets did well -> loosen min_edge; bets lose to the market -> tighten
      it; fills keep losing to wide books -> tighten max_spread. Raise
      `max_bet_tier` only where `tier_unlocked` is true.
   3. **Promote** a lane whose `dial_in.eligible` is true:
      `python3 core/params.py promote --lane <lane> --reason "..."`. Dialed
      lanes change slowly (30 settled bets between changes, smaller steps).
   core/params.py refuses anything outside the rules; a refusal is the rule
   working, not an error to route around. Commit:
   `git add -A && git commit -m "tune: <lane> <field> <old>-><new>"`.
4. **Scan & screen**: `python3 core/scan.py --hours 168 --limit 800 |
   python3 core/screen.py prepare` — queries come from `strategy/discovery.py`,
   which is mine to edit. Read scan's stderr: it reports each query's yield, and
   says loudly if my discovery module was unusable and it fell back. If a query
   is returning nothing useful, fix the query rather than lowering my standards.
   Then screen the pool with my own subagents - there is no API path, the
   screening judgment runs on the subscription:
   - Read `prepare`'s JSON header on stdout: `work_dir`, `batches` (one entry
     per batch file), `screened_pool`, `dropped_by_reason`, and
     `subagent_prompt_template`. `strategy/screener-strata.json` tunes which
     markets reach the pool and `strategy/screener-filters.json` drops the
     shapes no model is needed for (line-constructed markets, sub-daily crypto,
     placeholder 0.500 mids); `dropped_by_reason` says what each cut.
   - Spawn ONE Task subagent PER BATCH FILE, all in parallel in a single
     message, each with `subagent_type: "screener"` (the operator-owned
     `.claude/agents/screener.md`: haiku, Read and Write tools only). Give
     each subagent exactly `subagent_prompt_template` with the literal `NN`
     replaced by that batch's `nn`, and nothing else. Each writes
     `<work_dir>/out-NN.json` itself; I do not relay its answers.
   - When every subagent has finished: `python3 core/screen.py collect --dir
     <work_dir>`. It validates the out files, computes divergence, appends
     this month's `journal/screener/YYYY-MM.jsonl` (the screener log is one
     file per month; read it with `screen.log_rows()` or a glob, never one
     file), and prints the top rows plus a stderr summary (collected X/Y,
     escalated N, day batches Q/CAP).
   Research allocation in step 5 STARTS from collect's top divergences - but
   the screener ranks, it does not gate: a game you know has fresh news can
   claim a research slot without a high divergence. If `prepare` reports
   quota exhaustion, the Task tool is unavailable, or `collect` yields
   nothing, fall back to the unscreened scan output and say so in the log.
5. **Select & research**: work through collect's top rows, spreading
   research across switched-on lanes rather than spending every slot on one
   league. Follow `strategy/playbook.md` (benchmark first, outside-view
   veto). For bookmaker lines and match status use `python3 core/odds.py`
   (`sports` lists keys for free, `odds <sport> [--markets h2h,spreads,totals]`
   returns decimal lines for `strategy/tools/devig.py`, `scores <sport>`
   pins status). It enforces a monthly credit budget shared with Phil
   (`quota` shows state); spend it on pre-qualified candidates only. If it
   reports the key missing or the budget exhausted, log it and continue
   with web search.
5b. **Forecast**: for EVERY candidate researched to a concrete (market,
   outcome, probability), including skips:
   `python3 core/forecast.py record --market-id <slug> --outcome "<side>" \
     --est-prob <p> --category <league code> --skip-reason <bet|no-edge|market-agrees|...> \
     [--note "..."] --strategy-rev $(git rev-parse --short HEAD)`
   Forecasts are the cheap teacher: they score tiers and leagues that do not
   bet yet, and they unlock tier 2/3 betting. When research on a game gives
   a view on its tier-2 markets (first half, team totals) or on the
   league's futures, record those forecasts too. Record the `bet` forecast
   BEFORE placing.
6. **Bet**: where the league thresholds allow
   (`python3 core/params.py show --lane <lane>` or the lanestats `params`):
   `python3 core/ledger.py place --market-id <slug> --outcome "<side>" \
     --est-prob <p> --stake <league stake_usd> --category <league code> \
     --edge-class <info-race|cross-market|book-devig|other> \
     --rationale "<the fact, the benchmark, why the price is wrong>" \
     --strategy-rev $(git rev-parse --short HEAD)`
   **Test bets** (paper phase only): when a candidate's edge sits just
   under the league's min_edge (within 0.02) and your forecast record says
   the threshold may be too tight, add `--test-bet`. Core allows at most 1
   in 5 of a lane's bets to be test bets, and lanestats scores them
   separately (`test_bets`: test vs regular, per lane and league), so the
   tuning step can tell whether min_edge is costing good bets.
   Rejections are protected rules, not errors to fix.
7. **Log**: append one line to `journal/cycles.log`:
   `<UTC ISO> cycle done: settled N, placed M, cash $X (FULL cycle, <lanes bet>)`
   with `(LIGHT cycle, ...)` on light ticks. Cash is the total across lanes
   from `python3 core/ledger.py status`. Keep this exact shape: the
   dashboard and loop.sh parse it.
8. **Commit**: `git add -A && git commit -m "cycle: <UTCdate-HHMM> placed M settled N"`
9. **Push** (cloud runs only — skip if no `origin` remote): first, if
   `git symbolic-ref -q HEAD` prints nothing, HEAD is detached — reattach
   with `git checkout -B main HEAD` (from a detached HEAD, `git push origin
   main` pushes the stale branch ref and reports success while your commits
   never leave the container).

   **If the environment variable `PHIL_PUSH_BY_LOOP` is set, stop step 9
   here.** You are running on the operator's machine, where the git
   credential lives in the OS keyring and a push from this session hangs on
   a prompt you cannot answer. `loop.sh` pushes for you after you exit. Do
   the detached-HEAD reattach above (the runner needs `main` pointing at
   your work), then log the cycle as normal — an unpushed commit is expected
   here, not a discrepancy to diagnose.

   Otherwise, `git push origin main`.
   If rejected, `git pull --rebase origin main` and push again. If the rebase
   conflicts on `journal/ledger.jsonl`, abort, re-run step 1, and repeat from
   step 7 — never hand-edit the ledger.
   Finally, verify the push took: `git fetch origin main`, then
   `git rev-parse HEAD` and `git rev-parse origin/main` must print the same
   hash. If they differ, the push did not publish your work — do not log it
   as done; diagnose (detached HEAD again? rejected push?) and repeat this
   step until the hashes match or you have written the discrepancy into the
   cycle log line for the operator.
   Last, if you acquired the runner lease in step 0 (and `PHIL_LEASE` is not
   set), release it: `python3 core/lease.py release`. Release after the push,
   never before, so the other runner's next tip check sees your commit, and
   release even when the push failed: your tick is over, and release commits
   onto `origin/main` itself.
