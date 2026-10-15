#!/usr/bin/env python3
"""Paper broker: place simulated positions and track the bankroll.

PROTECTED CORE — the trading agent must not edit files under core/.
The agent CALLS this to bet; it cannot bypass the caps in config/protected.json
because this is the only writer of journal/ledger.jsonl.

Fills are honest: a paper BUY fills at the live best ask for the chosen
outcome, crossing the spread like a real taker order (on Polymarket US the
No side fills at 1 - best bid; see core/venue_us.py). If the book is empty or
the fill violates protected caps, the bet is rejected. The venue comes from
config/protected.json "venue" and is recorded on the row.

Sports agent (2026-10-15): every bet belongs to a LANE (sport) and a league
(core/lanes.py). On top of the global caps, place enforces per lane:
  * the lane is switched on (config/lanes.json)
  * the lane's own paper bankroll (sim_bankroll_usd per lane) and open cap
  * the league's thresholds from strategy/params/<lane>.json: min edge, max
    spread, stake, price band, max bets per cycle, highest tier it may bet
  * tier 2/3 markets only once that league+tier has earned it on forecasts;
    season futures never
  * leagues with no outside benchmark: stake capped until they earn it
  * $10 per game across every market on that game
  * test bets (--test-bet): paper phase only, just under the min edge, at
    most 1 in 5 of the lane's bets
The row records lane, league, tier, market_type and game_key.

Usage:
  place:  python3 core/ledger.py place --market-id <slug> --outcome "<side>" \
            --est-prob 0.62 --stake 5 --category nfl \
            --edge-class book-devig --rationale "..." [--test-bet]
  status: python3 core/ledger.py status
"""
import argparse
import datetime as dt
import json
import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import re  # noqa: E402

import lanes  # noqa: E402
import lanestats as ls  # noqa: E402
import venue  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
LEDGER = ROOT / "journal" / "ledger.jsonl"
PROTECTED = json.loads((ROOT / "config" / "protected.json").read_text())


def read_ledger():
    if not LEDGER.exists():
        return []
    return [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]


def bankroll(entries):
    """Total paper cash across every lane (each lane starts with
    sim_bankroll_usd). Rows outside any lane are not counted."""
    return sum(ls.lane_cash([e for e in entries if ls.tag(e)[0] == lane])
               for lane in lanes.LANE_NAMES)


def cmd_status(entries):
    open_pos = [e for e in entries if e["status"] == "open"]
    settled = [e for e in entries if e["status"] in ("won", "lost")]
    pnl = sum((e["shares"] - e["stake_usd"]) if e["status"] == "won" else -e["stake_usd"]
              for e in settled)
    print(json.dumps({
        "cash": round(bankroll(entries), 2),
        "open_positions": len(open_pos),
        "settled": len(settled),
        "wins": sum(1 for e in settled if e["status"] == "won"),
        "realized_pnl": round(pnl, 2),
        "lanes": {lane: {"cash": ls.lane_cash([e for e in entries if ls.tag(e)[0] == lane]),
                         "open": sum(1 for e in open_pos if ls.tag(e)[0] == lane)}
                  for lane in lanes.LANE_NAMES},
        "open": [{"id": e["id"], "q": e["question"][:70], "outcome": e["outcome"],
                  "entry": e["entry_price"], "est": e["est_prob"], "ends": e["end_date"]}
                 for e in open_pos],
    }, indent=2))


def cmd_place(args, entries):
    open_pos = [e for e in entries if e["status"] == "open"]
    if len(open_pos) >= PROTECTED["max_open_positions"]:
        sys.exit(f"REJECTED: max_open_positions ({PROTECTED['max_open_positions']}) reached")
    if args.stake > PROTECTED["max_stake_usd"]:
        sys.exit(f"REJECTED: stake {args.stake} > max_stake_usd {PROTECTED['max_stake_usd']}")
    if not 0.0 < args.est_prob < 1.0:
        sys.exit("REJECTED: est-prob must be in (0,1)")
    if any(e["market_id"] == args.market_id and e["outcome"] == args.outcome
           for e in open_pos):
        sys.exit("REJECTED: already have an open position on this market+outcome")

    v = venue.current(PROTECTED)
    m = venue.get_market(args.market_id, v)
    if m.get("closed"):
        sys.exit("REJECTED: market is closed")
    banned = [re.compile(p, re.I) for p in PROTECTED.get("banned_question_patterns", [])]
    if any(p.search(m.get("question") or "") for p in banned):
        sys.exit("REJECTED: market matches a protected banned_question_pattern")
    slug_banned = [re.compile(p, re.I) for p in PROTECTED.get("banned_slug_patterns", [])]
    if any(p.search(m.get("slug") or "") for p in slug_banned):
        sys.exit("REJECTED: market slug matches a protected banned_slug_pattern")
    tokens = venue.market_tokens(m)
    if tokens is None:
        sys.exit(f"REJECTED: no usable token ids (venue {v!r}, version "
                 f"{venue.market_version(m)!r}; supported: "
                 f"{sorted(venue.supported_versions(v))})")
    outcome = venue.resolve_outcome(m, args.outcome)
    if outcome is None:
        sys.exit(f"REJECTED: outcome {args.outcome!r} not in {list(tokens)}")
    args.outcome = outcome
    if v == "us":
        args.market_id = m["id"]  # the slug
    if any(e["market_id"] == args.market_id and e["outcome"] == outcome
           for e in open_pos):
        sys.exit("REJECTED: already have an open position on this market+outcome")
    bid, ask = venue.best_prices(tokens[outcome])
    if ask is None:
        sys.exit("REJECTED: no asks in the book (cannot fill)")
    if not PROTECTED["min_entry_price"] <= ask <= PROTECTED["max_entry_price"]:
        sys.exit(f"REJECTED: fill price {ask} outside protected bounds")
    lane_info = lane_checks(args, entries, m, bid, ask)

    entry = {
        "id": uuid.uuid4().hex[:12],
        "ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "market_id": args.market_id,
        "venue": v,
        "question": m.get("question"),
        "slug": m.get("slug"),
        "end_date": m.get("endDate"),
        "outcome": args.outcome,
        "token_id": tokens[args.outcome],
        "entry_price": ask,
        "best_bid_at_entry": bid,
        "market_prob_at_entry": ask,
        "est_prob": args.est_prob,
        "edge": round(args.est_prob - ask, 4),
        "stake_usd": args.stake,
        "shares": round(args.stake / ask, 4),
        "category": args.category,
        "edge_class": args.edge_class,
        "rationale": args.rationale,
        "strategy_rev": args.strategy_rev,
        **lane_info,
        "status": "open",
    }
    with LEDGER.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    print(json.dumps({"placed": entry["id"], "filled_at": ask, "edge": entry["edge"],
                      "shares": entry["shares"], "lane": entry.get("lane"),
                      "league": entry.get("league"), "tier": entry.get("tier"),
                      "question": entry["question"]}, indent=2))


def lane_checks(args, entries, m, bid, ask):
    """Sports-agent rules. Exits with REJECTED on any failure; returns the
    lane fields for the ledger row."""
    lcfg = PROTECTED["lanes"]
    info = lanes.classify_market(m)
    lane, league, tier = info["lane"], info["league"], info["tier"]
    if lane is None:
        sys.exit(f"REJECTED: league {league!r} is forecast-only (no lane)")
    if not lanes.load_switches().get(lane):
        sys.exit(f"REJECTED: lane {lane!r} is switched off (config/lanes.json)")
    if tier == "future" or tier is None:
        sys.exit(f"REJECTED: {info['market_type']!r} is a season future or untyped market: forecast only")
    params = ls.load_params(lane)
    if params is None:
        sys.exit(f"REJECTED: no strategy/params/{lane}.json")
    eff = ls.effective(params, league)
    phase = params.get("phase", "paper")
    pcfg = lcfg["phases"][phase]
    lane_rows = [e for e in entries if ls.tag(e)[0] == lane]
    lane_open = [e for e in lane_rows if e["status"] == "open"]

    if len(lane_open) >= lcfg["max_open_per_lane"]:
        sys.exit(f"REJECTED: lane {lane} has {len(lane_open)} open bets (max {lcfg['max_open_per_lane']})")
    if args.stake > ls.lane_cash(lane_rows):
        sys.exit(f"REJECTED: insufficient sim cash in lane {lane}")
    if tier > eff["max_bet_tier"]:
        sys.exit(f"REJECTED: tier {tier} market; league {league} may bet up to tier {eff['max_bet_tier']}")
    if tier > 1:
        fcs = ls.load_jsonl(ls.FORECASTS)
        ok, b = ls.tier_unlocked(fcs, league, tier)
        if not ok:
            sys.exit(f"REJECTED: tier {tier} in {league} not earned yet: needs "
                     f"{lcfg['tier_unlock']['min_settled_forecasts']} settled forecasts at or "
                     f"better than the market (now {b})")
    if args.stake > eff["stake_usd"] + 1e-9:
        sys.exit(f"REJECTED: stake {args.stake} > league stake_usd {eff['stake_usd']}")
    released, _ = ls.benchmark_released(entries, league)
    if not released and args.stake > lcfg["no_benchmark_stake_cap_usd"] + 1e-9:
        sys.exit(f"REJECTED: {league} has no outside benchmark yet; stake capped at "
                 f"${lcfg['no_benchmark_stake_cap_usd']} until it earns release")
    if not eff["min_price"] <= ask <= eff["max_price"]:
        sys.exit(f"REJECTED: price {ask} outside league band [{eff['min_price']}, {eff['max_price']}]")
    if bid is None:
        sys.exit("REJECTED: no bid on this side, spread unknown")
    spread = round(ask - bid, 4)
    if spread > eff["max_spread"] + 1e-9:
        sys.exit(f"REJECTED: spread {spread} > league max_spread {eff['max_spread']}")
    edge = round(args.est_prob - ask, 4)
    test = bool(args.test_bet)
    if test:
        tb = pcfg["test_bets"]
        if tb["max_share"] <= 0:
            sys.exit(f"REJECTED: test bets are off in phase {phase}")
        if not eff["min_edge"] - tb["max_below_min_edge"] - 1e-9 <= edge < eff["min_edge"]:
            sys.exit(f"REJECTED: a test bet needs edge in [{round(eff['min_edge'] - tb['max_below_min_edge'], 4)}, "
                     f"{eff['min_edge']}); got {edge}")
        n_test = sum(1 for e in lane_rows if e.get("test_bet"))
        if n_test + 1 > tb["max_share"] * (len(lane_rows) + 1) + 1e-9:
            sys.exit(f"REJECTED: test bets would exceed {tb['max_share']:.0%} of lane {lane}'s bets "
                     f"({n_test} of {len(lane_rows)})")
    elif edge < eff["min_edge"] - 1e-9:
        sys.exit(f"REJECTED: edge {edge} < league min_edge {eff['min_edge']}")
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent = [e for e in lane_rows if e.get("league") == league and (e.get("ts") or "") >= cutoff]
    if len(recent) >= eff["max_bets_per_cycle"]:
        sys.exit(f"REJECTED: {league} already placed {len(recent)} bets this cycle "
                 f"(max_bets_per_cycle {eff['max_bets_per_cycle']})")
    recent_all = [e for e in entries if (e.get("ts") or "") >= cutoff]
    if len(recent_all) >= PROTECTED["max_new_positions_per_cycle"]:
        sys.exit("REJECTED: max_new_positions_per_cycle reached")
    gk = lanes.game_key(m, info)
    on_game = sum(e["stake_usd"] for e in entries if e["status"] == "open" and e.get("game_key") == gk)
    if on_game + args.stake > lcfg["max_per_game_usd"] + 1e-9:
        sys.exit(f"REJECTED: ${on_game} already on this game; max ${lcfg['max_per_game_usd']} per game")
    return {"lane": lane, "league": league, "tier": tier, "market_type": info["market_type"],
            "game_key": gk, "spread_at_entry": spread, "test_bet": test, "phase": phase}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("place")
    p.add_argument("--market-id", required=True)
    p.add_argument("--outcome", required=True, help="exact outcome name, e.g. Yes")
    p.add_argument("--est-prob", type=float, required=True,
                   help="agent's probability that this outcome wins")
    p.add_argument("--stake", type=float, required=True)
    p.add_argument("--category", required=True,
                   help="agent-assigned category, e.g. earnings/soccer/esports/news")
    p.add_argument("--edge-class", required=True,
                   choices=["info-race", "cross-market", "book-devig", "other"],
                   help="playbook edge class this bet claims (scored separately)")
    p.add_argument("--rationale", required=True, help="one-line reason (for the retro)")
    p.add_argument("--strategy-rev", default="", help="git rev of strategy/ used")
    p.add_argument("--test-bet", action="store_true",
                   help="paper phase: a deliberate bet just under the league min_edge "
                        "to test whether the threshold is set too tight (max 1 in 5)")
    sub.add_parser("status")
    args = ap.parse_args()

    entries = read_ledger()
    if args.cmd == "status":
        cmd_status(entries)
    else:
        cmd_place(args, entries)


if __name__ == "__main__":
    main()
