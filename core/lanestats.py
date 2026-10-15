#!/usr/bin/env python3
"""Per-lane, per-league scorecard and tuning status.

PROTECTED CORE — the trading agent must not edit files under core/.

Reads journal/ledger.jsonl, journal/forecasts.jsonl and strategy/params/*.json
and reports, for every lane:
  * paper bankroll (each lane starts with sim_bankroll_usd), realized P&L,
    open stake
  * accuracy vs the market: Brier score of the agent's probability against
    the price it paid (bets) and against the mid (forecasts); delta < 0 means
    the agent beat the market
  * per league: the same, plus forecast accuracy by market tier, whether tier
    2/3 betting is unlocked, whether a no-benchmark league's $1 cap is
    released, and how many settled bets have arrived since its last threshold
    change (can it change again yet?)
  * revert_due: threshold changes whose follow-up window scored worse than
    the window before them
  * dial_in: whether the lane meets the promotion rule (paper -> dialed)

Usage: python3 core/lanestats.py [--lane football] [--compact]
"""
import argparse
import datetime as dt
import json
import pathlib
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import lanes  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
LEDGER = ROOT / "journal" / "ledger.jsonl"
FORECASTS = ROOT / "journal" / "forecasts.jsonl"
PARAMS_DIR = ROOT / "strategy" / "params"
PROTECTED = json.loads((ROOT / "config" / "protected.json").read_text())
LCFG = PROTECTED["lanes"]
PARAM_FIELDS = ("min_edge", "max_spread", "stake_usd", "max_bets_per_cycle",
                "min_price", "max_price", "max_bet_tier")


def load_jsonl(path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def brier(rows, est_key, mkt_key):
    rows = [r for r in rows if r.get("status") in ("won", "lost")
            and num(r.get(est_key)) and num(r.get(mkt_key))]
    if not rows:
        return None
    y = [1 if r["status"] == "won" else 0 for r in rows]
    a = sum((r[est_key] - t) ** 2 for r, t in zip(rows, y)) / len(rows)
    m = sum((r[mkt_key] - t) ** 2 for r, t in zip(rows, y)) / len(rows)
    return {"agent": round(a, 4), "market": round(m, 4), "delta": round(a - m, 4), "n": len(rows)}


def bet_brier(rows):
    return brier(rows, "est_prob", "market_prob_at_entry")


def fc_brier(rows):
    return brier([r for r in rows if not r.get("superseded_by")], "est_prob", "market_prob_at_record")


def settled_key(r):
    return r.get("settled_ts") or r.get("ts") or ""


def settled(rows):
    return sorted((r for r in rows if r.get("status") in ("won", "lost")), key=settled_key)


def tag(row):
    """(lane, league, tier) of a ledger/forecast row; classifies old rows."""
    if row.get("lane"):
        return row["lane"], row.get("league"), row.get("tier")
    c = lanes.classify(row.get("slug"), row.get("market_type"))
    return c["lane"], c["league"], c["tier"]


def lane_cash(rows):
    cash = PROTECTED["sim_bankroll_usd"]
    for e in rows:
        if e["status"] in ("open", "won", "lost", "void"):
            cash -= e["stake_usd"]
        if e["status"] == "won":
            cash += e["shares"]
        elif e["status"] == "void":
            cash += e["stake_usd"]
    return round(cash, 2)


def load_params(lane):
    p = PARAMS_DIR / f"{lane}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def effective(params, league):
    eff = dict(params.get("defaults") or {})
    eff.update((params.get("leagues") or {}).get(league) or {})
    return eff


def scope_rows(rows, scope):
    """scope '_lane' = every row in the lane, else one league."""
    return rows if scope == "_lane" else [r for r in rows if tag(r)[1] == scope]


def last_change(params, scope, field=None):
    for c in reversed(params.get("changes") or []):
        if c.get("scope") == scope and (field is None or c.get("field") == field):
            return c
    return None


def settled_since(rows, ts):
    return [r for r in settled(rows) if settled_key(r) > (ts or "")]


def phase_cfg(params):
    return LCFG["phases"][params.get("phase", "paper")]


def tier_unlocked(fc_rows, league, tier_n):
    rows = [r for r in fc_rows if tag(r)[1] == league and tag(r)[2] == tier_n]
    b = fc_brier(rows)
    need = LCFG["tier_unlock"]
    ok = bool(b and b["n"] >= need["min_settled_forecasts"] and b["delta"] < need["max_brier_delta"])
    return ok, b


def benchmark_released(bet_rows, league):
    if league in LCFG["benchmark_leagues"]:
        return True, None
    b = bet_brier([r for r in bet_rows if tag(r)[1] == league])
    need = LCFG["no_benchmark_release"]
    ok = bool(b and b["n"] >= need["min_settled_bets"] and b["delta"] <= need["max_brier_delta"])
    return ok, b


def revert_due(params, bet_rows):
    """Changes whose follow-up window is complete and scored worse."""
    out = []
    n = phase_cfg(params)["revert_check_settled"]
    changes = params.get("changes") or []
    for i, c in enumerate(changes):
        if c.get("field") not in PARAM_FIELDS or c.get("revert_of"):
            continue
        if any(d.get("revert_of") == c.get("id") for d in changes):
            continue
        later = [d for d in changes[i + 1:] if d.get("scope") == c.get("scope") and d.get("field") == c.get("field")]
        if later:
            continue
        rows = settled(scope_rows(bet_rows, c["scope"]))
        # windows by when the bet was PLACED: a bet placed before the change
        # was not made under it, whenever it settled
        post = [r for r in rows if (r.get("ts") or "") > c["ts"]][:n]
        pre = [r for r in rows if (r.get("ts") or "") <= c["ts"]][-n:]
        if len(post) < n or len(pre) < max(5, n // 2):
            continue
        bpost, bpre = bet_brier(post), bet_brier(pre)
        if bpost and bpre and bpost["delta"] > bpre["delta"]:
            out.append({"change": c["id"], "scope": c["scope"], "field": c["field"],
                        "from": c["from"], "to": c["to"],
                        "before_delta": bpre["delta"], "after_delta": bpost["delta"]})
    return out


def test_bet_split(rows):
    """Test bets (placed just under min_edge) against regular bets, so the
    tuning step can see whether min_edge is costing good bets."""
    out = {}
    for name, sel in (("test", [r for r in rows if r.get("test_bet")]),
                      ("regular", [r for r in rows if not r.get("test_bet")])):
        s = settled(sel)
        out[name] = {"placed": len(sel), "settled": len(s),
                     "pnl": round(sum(r.get("pnl_usd") or 0 for r in s), 2),
                     "brier": bet_brier(sel)}
    return out


def dial_in(params, bet_rows, now):
    need = LCFG["dial_in"]
    s = settled(bet_rows)
    reasons = []
    if len(s) < need["min_settled_bets"]:
        reasons.append(f"{len(s)}/{need['min_settled_bets']} settled bets")
    quiet_since = (now - dt.timedelta(days=need["quiet_days"])).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent_changes = [c for c in params.get("changes") or [] if c.get("ts", "") > quiet_since]
    if recent_changes:
        reasons.append(f"{len(recent_changes)} threshold change(s) in the last {need['quiet_days']} days")
    b = bet_brier(s[-need["recent_window"]:])
    if not b or b["delta"] > need["max_brier_delta"]:
        reasons.append("accuracy over the last %d bets is behind the market (delta %s)"
                       % (need["recent_window"], b["delta"] if b else "n/a"))
    return {"eligible": not reasons and params.get("phase", "paper") == "paper",
            "phase": params.get("phase", "paper"), "missing": reasons}


def changes_today(params, now):
    day = now.strftime("%Y-%m-%d")
    return [c for c in params.get("changes") or [] if c.get("ts", "").startswith(day)
            and not c.get("revert_of") and c.get("field") in PARAM_FIELDS]


def report(lane_filter=None, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    ledger = load_jsonl(LEDGER)
    fcs = load_jsonl(FORECASTS)
    by_lane, fc_by_lane = defaultdict(list), defaultdict(list)
    for r in ledger:
        by_lane[tag(r)[0]].append(r)
    for r in fcs:
        fc_by_lane[tag(r)[0]].append(r)
    switches = lanes.load_switches()
    out = {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "lanes": {}}
    tot = {"start": 0.0, "cash": 0.0, "realized": 0.0, "open_stake": 0.0, "settled": 0, "wins": 0}
    for lane in lanes.LANE_NAMES:
        if lane_filter and lane != lane_filter:
            continue
        rows, frows = by_lane.get(lane, []), fc_by_lane.get(lane, [])
        params = load_params(lane) or {"phase": "paper", "defaults": {}, "leagues": {}, "changes": []}
        cfg = phase_cfg(params)
        s = settled(rows)
        opn = [r for r in rows if r["status"] == "open"]
        leagues_seen = sorted({tag(r)[1] for r in rows + frows if tag(r)[1]} | set((params.get("leagues") or {}).keys()))
        leagues_out = {}
        for lg in leagues_seen:
            lrows = [r for r in rows if tag(r)[1] == lg]
            lfc = [r for r in frows if tag(r)[1] == lg]
            lc = last_change(params, lg)
            since = settled_since(lrows, lc["ts"] if lc else "")
            rel, _ = benchmark_released(rows, lg)
            leagues_out[lg] = {
                "name": lanes.LEAGUES.get(lg, (None, lg))[1],
                "placed": len(lrows), "settled": len(settled(lrows)),
                "wins": sum(1 for r in lrows if r["status"] == "won"),
                "pnl": round(sum(r.get("pnl_usd") or 0 for r in settled(lrows)), 2),
                "bet_brier": bet_brier(lrows),
                "forecast_brier": fc_brier(lfc),
                "forecast_brier_by_tier": {str(t): fc_brier([r for r in lfc if tag(r)[2] == t]) for t in (1, 2, 3, "future")},
                "tier_unlocked": {"2": tier_unlocked(frows, lg, 2)[0], "3": tier_unlocked(frows, lg, 3)[0]},
                "test_bets": test_bet_split(lrows),
                "benchmark": lg in LCFG["benchmark_leagues"],
                "stake_cap_released": rel,
                "params": effective(params, lg),
                "settled_since_last_change": len(since),
                "can_change": len(since) >= cfg["min_settled_between_changes"],
            }
        lane_since = settled_since(rows, (last_change(params, "_lane") or {}).get("ts", ""))
        realized = round(sum(r.get("pnl_usd") or 0 for r in s), 2)
        cash = lane_cash(rows)
        open_stake = round(sum(r["stake_usd"] for r in opn), 2)
        ct = changes_today(params, now)
        out["lanes"][lane] = {
            "name": lanes.LANE_NAMES[lane], "enabled": switches[lane],
            "phase": params.get("phase", "paper"),
            "start": PROTECTED["sim_bankroll_usd"], "cash": cash, "realized": realized,
            "open": len(opn), "open_stake": open_stake,
            "placed": len(rows), "settled": len(s), "wins": sum(1 for r in s if r["status"] == "won"),
            "bet_brier": bet_brier(rows), "forecast_brier": fc_brier(frows),
            "test_bets": test_bet_split(rows),
            "forecasts": {"recorded": len(frows), "settled": len(settled(frows))},
            "defaults": params.get("defaults"),
            "lane_defaults_can_change": len(lane_since) >= cfg["min_settled_between_changes"],
            "changes_today": len(ct), "changes_left_today": max(0, cfg["max_changes_per_lane_per_day"] - len(ct)),
            "revert_due": revert_due(params, rows),
            "dial_in": dial_in(params, rows, now),
            "leagues": leagues_out,
        }
        tot["start"] += PROTECTED["sim_bankroll_usd"]
        tot["cash"] += cash
        tot["realized"] += realized
        tot["open_stake"] += open_stake
        tot["settled"] += len(s)
        tot["wins"] += sum(1 for r in s if r["status"] == "won")
    other = by_lane.get(None, []) + fc_by_lane.get(None, [])
    out["total"] = {k: round(v, 2) if isinstance(v, float) else v for k, v in tot.items()}
    out["total"]["bet_brier"] = bet_brier([r for r in ledger if tag(r)[0]])
    out["total"]["forecast_brier"] = fc_brier([r for r in fcs if tag(r)[0]])
    out["unlaned_rows"] = len(other)
    return out


def compact(rep):
    lines = []
    t = rep["total"]
    lines.append(f"TOTAL cash ${t['cash']:.2f} of ${t['start']:.0f}, realized {t['realized']:+.2f}, "
                 f"settled {t['settled']} ({t['wins']} won), bet brier delta "
                 f"{(t['bet_brier'] or {}).get('delta', 'n/a')}")
    for lane, L in rep["lanes"].items():
        b = L["bet_brier"] or {}
        f = L["forecast_brier"] or {}
        lines.append(f"{lane:<10} {'on ' if L['enabled'] else 'OFF'} {L['phase']:<6} cash ${L['cash']:.2f} "
                     f"pnl {L['realized']:+.2f} open {L['open']} settled {L['settled']} "
                     f"bet d={b.get('delta', 'n/a')} (n={b.get('n', 0)}) fc d={f.get('delta', 'n/a')} "
                     f"(n={f.get('n', 0)}) changes left today {L['changes_left_today']}"
                     + (f" REVERT DUE {[r['change'] for r in L['revert_due']]}" if L["revert_due"] else "")
                     + (" DIAL-IN ELIGIBLE" if L["dial_in"]["eligible"] else ""))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", default=None)
    ap.add_argument("--compact", action="store_true")
    args = ap.parse_args()
    rep = report(args.lane)
    print(compact(rep) if args.compact else json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
