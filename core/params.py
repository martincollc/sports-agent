#!/usr/bin/env python3
"""Change a lane's thresholds — the ONLY way strategy/params/*.json changes.

PROTECTED CORE — the trading agent must not edit files under core/.

The thresholds in strategy/params/<lane>.json are the agent's to tune, but
only inside the rules in config/protected.json -> lanes.phases.<phase>:

  * bounds          every value stays inside its allowed range
  * max_step        one change moves a value by at most this much
  * evidence        at least min_settled_between_changes settled bets in that
                    scope (one league, or "_lane" for the lane defaults)
                    since the scope's previous change
  * pace            at most max_changes_per_lane_per_day per lane (UTC day)
  * written down    every change carries a reason and the result it expects

A revert (`--revert <change id>`) restores a change's old value. It skips the
evidence and step rules (undoing a change is always allowed) but still has to
land inside the bounds, and it does not count toward the daily pace.

`promote` moves a lane from the fast "paper" phase to the careful "dialed"
phase once core/lanestats.py says it is eligible.

core/validate.py replays every params file's change log in CI, so a hand
edit that skips this tool fails the push.

Usage:
  python3 core/params.py show --lane football
  python3 core/params.py set --lane football --scope nfl --field min_edge \
      --value 0.04 --reason "..." --expect "..."
  python3 core/params.py set --lane football --revert <change-id> --reason "..."
  python3 core/params.py promote --lane football --reason "..."
"""
import argparse
import datetime as dt
import json
import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import lanes  # noqa: E402
import lanestats as ls  # noqa: E402

INT_FIELDS = {"max_bets_per_cycle", "max_bet_tier"}


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def path(lane):
    return ls.PARAMS_DIR / f"{lane}.json"


def save(lane, params):
    path(lane).write_text(json.dumps(params, indent=2) + "\n")


def current_value(params, scope, field):
    if scope == "_lane":
        return (params.get("defaults") or {}).get(field)
    return ls.effective(params, scope).get(field)


def check_value(cfg, field, value):
    lo, hi = cfg["bounds"][field]
    if not lo <= value <= hi:
        sys.exit(f"REJECTED: {field} {value} outside the {cfg.get('_name')} bounds [{lo}, {hi}]")


def apply(params, scope, field, value):
    if scope == "_lane":
        params.setdefault("defaults", {})[field] = value
    else:
        params.setdefault("leagues", {}).setdefault(scope, {})[field] = value


def cmd_set(args):
    lane = args.lane
    params = ls.load_params(lane)
    if params is None:
        sys.exit(f"REJECTED: no strategy/params/{lane}.json")
    phase = params.get("phase", "paper")
    cfg = dict(ls.LCFG["phases"][phase], _name=phase)
    ts = now_iso()
    if not args.reason:
        sys.exit("REJECTED: --reason is required")

    if args.revert:
        target = next((c for c in params.get("changes") or [] if c.get("id") == args.revert), None)
        if target is None:
            sys.exit(f"REJECTED: no change {args.revert!r} in {lane}")
        if target.get("revert_of"):
            sys.exit("REJECTED: that change is itself a revert; re-applying a value is a "
                     "normal change and needs the evidence and step rules")
        if any(c.get("revert_of") == args.revert for c in params["changes"]):
            sys.exit(f"REJECTED: change {args.revert} was already reverted")
        scope, field, value = target["scope"], target["field"], target["from"]
        if field not in ls.PARAM_FIELDS:
            sys.exit("REJECTED: only threshold changes can be reverted")
        check_value(cfg, field, value)
        entry = {"id": uuid.uuid4().hex[:10], "ts": ts, "scope": scope, "field": field,
                 "from": current_value(params, scope, field), "to": value,
                 "reason": args.reason, "expect": args.expect or "restore the earlier accuracy",
                 "revert_of": args.revert, "phase": phase}
    else:
        scope, field = args.scope, args.field
        if not scope or not field or args.value is None:
            sys.exit("REJECTED: set needs --scope, --field and --value (or --revert)")
        if field not in ls.PARAM_FIELDS:
            sys.exit(f"REJECTED: unknown field {field!r}; one of {list(ls.PARAM_FIELDS)}")
        if scope != "_lane" and lanes.LEAGUES.get(scope, (None,))[0] != lane:
            sys.exit(f"REJECTED: league {scope!r} is not in lane {lane!r}")
        if not args.expect:
            sys.exit("REJECTED: --expect is required (the result this change should produce)")
        try:
            fv = float(args.value)
        except ValueError:
            sys.exit(f"REJECTED: --value {args.value!r} is not a number")
        if field in INT_FIELDS:
            if fv != int(fv):
                sys.exit(f"REJECTED: {field} must be a whole number")
            value = int(fv)
        else:
            value = round(fv, 4)
        check_value(cfg, field, value)
        old = current_value(params, scope, field)
        if old is None:
            sys.exit(f"REJECTED: {field} has no current value in scope {scope!r}")
        if abs(value - old) > cfg["max_step"][field] + 1e-9:
            sys.exit(f"REJECTED: step {round(value - old, 4)} exceeds max_step "
                     f"{cfg['max_step'][field]} for {field} in phase {phase}")
        if value == old:
            sys.exit("REJECTED: value unchanged")
        ledger = ls.load_jsonl(ls.LEDGER)
        rows = [r for r in ledger if ls.tag(r)[0] == lane]
        last = ls.last_change(params, scope)
        since = ls.settled_since(ls.scope_rows(rows, scope), last["ts"] if last else "")
        need = cfg["min_settled_between_changes"]
        if len(since) < need:
            sys.exit(f"REJECTED: {len(since)} settled bets in scope {scope!r} since its last "
                     f"change; phase {phase} needs {need}")
        today = ls.changes_today(params, dt.datetime.now(dt.timezone.utc))
        if len(today) >= cfg["max_changes_per_lane_per_day"]:
            sys.exit(f"REJECTED: lane {lane} already made {len(today)} change(s) today "
                     f"(phase {phase} allows {cfg['max_changes_per_lane_per_day']})")
        entry = {"id": uuid.uuid4().hex[:10], "ts": ts, "scope": scope, "field": field,
                 "from": old, "to": value, "reason": args.reason, "expect": args.expect,
                 "settled_since_previous": len(since), "phase": phase}

    apply(params, entry["scope"], entry["field"], entry["to"])
    params.setdefault("changes", []).append(entry)
    save(lane, params)
    print(json.dumps({"changed": entry}, indent=2))


def cmd_promote(args):
    params = ls.load_params(args.lane)
    if params is None:
        sys.exit(f"REJECTED: no strategy/params/{args.lane}.json")
    rows = [r for r in ls.load_jsonl(ls.LEDGER) if ls.tag(r)[0] == args.lane]
    d = ls.dial_in(params, rows, dt.datetime.now(dt.timezone.utc))
    if not d["eligible"]:
        sys.exit(f"REJECTED: lane {args.lane} is not eligible to promote: {d['missing'] or d['phase']}")
    dialed = ls.LCFG["phases"]["dialed"]["bounds"]
    vals = [(s, f, v) for s, block in [("_lane", params.get("defaults") or {})] +
            list((params.get("leagues") or {}).items()) for f, v in block.items() if f in dialed]
    out = [(s, f, v) for s, f, v in vals if not dialed[f][0] <= v <= dialed[f][1]]
    if out:
        sys.exit(f"REJECTED: move these inside the dialed bounds first: {out}")
    params["phase"] = "dialed"
    params.setdefault("changes", []).append({
        "id": uuid.uuid4().hex[:10], "ts": now_iso(), "scope": "_lane", "field": "phase",
        "from": "paper", "to": "dialed", "reason": args.reason or "dial-in rule met",
        "expect": "slower, smaller threshold changes from here"})
    save(args.lane, params)
    print(json.dumps({"promoted": args.lane}, indent=2))


def cmd_show(args):
    params = ls.load_params(args.lane)
    print(json.dumps(params, indent=2))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set")
    s.add_argument("--lane", required=True, choices=list(lanes.LANE_NAMES))
    s.add_argument("--scope", help="a league code in this lane, or _lane for the lane defaults")
    s.add_argument("--field")
    s.add_argument("--value")
    s.add_argument("--revert", help="change id to undo")
    s.add_argument("--reason", required=True)
    s.add_argument("--expect", default="")
    p = sub.add_parser("promote")
    p.add_argument("--lane", required=True, choices=list(lanes.LANE_NAMES))
    p.add_argument("--reason", default="")
    w = sub.add_parser("show")
    w.add_argument("--lane", required=True, choices=list(lanes.LANE_NAMES))
    args = ap.parse_args()
    {"set": cmd_set, "promote": cmd_promote, "show": cmd_show}[args.cmd](args)


if __name__ == "__main__":
    main()
