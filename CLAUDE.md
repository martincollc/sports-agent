# Sports agent (self-improving sports trader)

A sibling of Phil (github.com/martincollc/phil) that trades only sports on
Polymarket US, paper only. One shared engine runs eight sport lanes. The
agent (Claude Code, headless) runs `CYCLE.md` repeatedly: settle → score per
lane → retro → tune thresholds → screen → research → forecast → bet.

## Layout

- `core/` + `config/` — PROTECTED engine and limits. The agent must never
  edit these; `loop.sh` reverts any such change and CI fails the push.
  - `core/lanes.py` — slug → league → lane, market type → tier.
  - `core/ledger.py` — the only writer of `journal/ledger.jsonl`; enforces
    per-lane bankroll ($1,000 each), lane switches, league thresholds,
    tier unlocks, $10 per game, test-bet share.
  - `core/params.py` — the only way `strategy/params/*.json` change.
  - `core/lanestats.py` — per-lane, per-league scorecard and tuning status.
  - `config/lanes.json` — the operator's on/off switch per lane.
  - `config/params-initial.json` — starting thresholds CI replays from.
- `strategy/` — the agent's playbook, discovery queries, screener brief,
  tools, and (through core/params.py only) its thresholds.
- `journal/` — ledger, forecasts, retros, cycle log.
- CI runs `core/validate.py` (caps, ledger shape, params change-log replay,
  paper-only), the offline tests, a lint pass, and the operator boundary:
  commits that touch protected paths must start with `operator:`.

## Purpose

Find which sports, leagues and market types fast research can beat the
market on, and how tight each lane's thresholds should be. The measure is
accuracy against the market price (Brier delta), per lane and league; the
dashboard shows it daily so lanes that are not working can be rethought or
switched off.
