#!/usr/bin/env python3
"""Live, read-only check of the sports discovery queries on Polymarket US.

PROTECTED CORE. Run on the operator Mac (the cloud sandbox cannot reach the
gateway). Places nothing, writes nothing.

For each query in strategy/discovery.py it reports how many markets come
back with and without the end-date window, which leagues and tiers they
classify to, and whether the market type name was accepted. Then it reads
one order book per lane to confirm prices are there.

Usage: python3 core/lane_smoke.py [--hours 168]
"""
import argparse
import collections
import datetime as dt
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "strategy"))
import discovery  # noqa: E402
import lanes  # noqa: E402
import venue_us  # noqa: E402


def run(q, limit=100):
    q = {k: v for k, v in q.items() if not k.startswith("_")}
    try:
        return venue_us.markets(**q, limit=limit, offset=0), None
    except Exception as e:  # noqa: BLE001
        return [], f"{type(e).__name__}: {str(e)[:120]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=168)
    args = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    qs = discovery.queries(now=now, min_end=now + dt.timedelta(minutes=20),
                           horizon=now + dt.timedelta(hours=args.hours), args={}, protected={})
    per_lane_sample = {}
    totals = collections.Counter()
    print(f"{'query':<46} {'window':>6} {'nowin':>6}  leagues / tiers / note")
    for q in qs:
        label = q.get("_label", "?")
        rows, e1 = run(q)
        nowin = {k: v for k, v in q.items() if k not in ("end_date_min", "end_date_max")}
        rows2, e2 = run(nowin)
        if e1 or e2:
            print(f"{label:<46} {'ERR':>6} {'ERR':>6}  {e1 or e2}")
            continue
        typ = (q.get("sportsMarketTypes") or [None])[0]
        wrong_type = sum(1 for m in rows2 if typ and m.get("sportsMarketType") != typ)
        leagues = collections.Counter()
        tiers = collections.Counter()
        for m in rows2:
            c = lanes.classify_market(m)
            leagues[f"{c['league']}->{c['lane']}"] += 1
            tiers[str(c["tier"])] += 1
            if c["lane"] and not m.get("closed"):
                per_lane_sample.setdefault(c["lane"], m)
        totals["window"] += len(rows)
        totals["nowin"] += len(rows2)
        note = ""
        if wrong_type:
            note = f" FILTER IGNORED? {wrong_type}/{len(rows2)} rows have another type"
        if rows2 and not rows:
            note += " WINDOW DROPS ALL"
        print(f"{label:<46} {len(rows):>6} {len(rows2):>6}  "
              f"{dict(leagues.most_common(6))} tiers {dict(tiers)}{note}")
    print(f"\ntotal rows: {totals['window']} inside the {args.hours:.0f}h window, "
          f"{totals['nowin']} without it (first 100 per query)")
    print("\nbook check, one market per lane:")
    for lane in lanes.LANE_NAMES:
        m = per_lane_sample.get(lane)
        if not m:
            print(f"  {lane:<10} no market found")
            continue
        try:
            bid, ask = venue_us.long_bid_ask(m["slug"])
            print(f"  {lane:<10} {m['slug'][:60]:<60} bid {bid} ask {ask} "
                  f"start {m.get('gameStartTime')} end {m.get('endDate')}")
        except Exception as e:  # noqa: BLE001
            print(f"  {lane:<10} {m['slug'][:60]:<60} book error {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
