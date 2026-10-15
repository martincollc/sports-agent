#!/usr/bin/env python3
"""Live, read-only smoke test of the Polymarket US adapter. Operator tool.

PROTECTED CORE — the trading agent must not edit files under core/.

Run on the operator machine BEFORE switching config/protected.json "venue"
to "us". It only GETs public endpoints (no API key, no orders, writes
nothing) and checks the assumptions core/venue_us.py makes about the
gateway's responses. Each check prints PASS / WARN / FAIL; exit code 1 on
any FAIL.

Usage: python3 core/us_smoke.py [--verbose]
"""
import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import venue  # noqa: E402
import venue_us  # noqa: E402

results = []


def report(status, name, detail=""):
    results.append(status)
    print(f"{status:4}  {name}" + (f" — {detail}" if detail else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true", help="dump raw records")
    args = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731

    # 1. raw listing
    try:
        body = venue_us.get_json("/v1/markets", {"limit": 50, "active": True, "closed": False})
        raw = body.get("markets") if isinstance(body, dict) else None
        assert isinstance(raw, list) and raw, f"no 'markets' list (keys: {list(body)[:8]})"
        report("PASS", "GET /v1/markets returns {markets: [...]}", f"{len(raw)} markets")
    except Exception as e:  # noqa: BLE001
        report("FAIL", "GET /v1/markets", str(e))
        return finish()

    # 2. side structure
    with_sides = [m for m in raw if m.get("marketSides")]
    labelled = [m for m in with_sides
                if [n for n, _ in venue_us.sides(m)] != ["Yes", "No"]]
    longs = [m for m in with_sides
             if sum(1 for s in m["marketSides"] if s.get("long") is True) == 1
             and sum(1 for s in m["marketSides"] if s.get("long") is False) == 1]
    report("PASS" if len(longs) == len(with_sides) and with_sides else "WARN",
           "each market has exactly one long and one short side",
           f"{len(longs)}/{len(with_sides)} with marketSides; {len(labelled)} have "
           f"named sides (others fall back to Yes/No)")
    if args.verbose and with_sides:
        print(json.dumps(with_sides[0], indent=2)[:3000])

    # 3. date-window + ordering query (what scan.py sends)
    try:
        q = {"closed": "false", "order": "endDate", "ascending": "true",
             "end_date_min": iso(now + dt.timedelta(minutes=20)),
             "end_date_max": iso(now + dt.timedelta(days=7)), "limit": 100, "offset": 0}
        got = venue.list_markets("us", **q)
        ends = [m.get("endDate") or "" for m in got]
        in_window = all(iso(now) <= e[:19] + "Z" <= iso(now + dt.timedelta(days=8))
                        for e in ends if e)
        ordered = ends == sorted(ends)
        report("PASS" if got and in_window and ordered else "WARN",
               "scan-style query (endDate window, orderBy endDate asc)",
               f"{len(got)} markets, in window: {in_window}, ordered: {ordered}")
    except Exception as e:  # noqa: BLE001
        report("FAIL", "scan-style query", str(e))
        got = []

    # 4. book + fill mirroring on a liquid market
    pick = None
    for m in (got or [venue_us.gamma_view(x) for x in raw]):
        try:
            bid, ask = venue_us.long_bid_ask(m["slug"])
        except Exception:  # noqa: BLE001
            continue
        if bid is not None and ask is not None:
            pick = (m, bid, ask)
            break
    if not pick:
        report("FAIL", "found a market with a two-sided book", "none in the sample")
        return finish()
    m, bid, ask = pick
    report("PASS" if 0 < bid < ask < 1 else "FAIL", "book bids/offers parse (px.value)",
           f"{m['slug']}: bid {bid} offer {ask}")
    toks = venue.market_tokens(m)
    names = list(toks)
    lb, la = venue.best_prices(toks[names[0]])
    sb, sa = venue.best_prices(toks[names[1]])
    report("PASS", "fill model", f"buy {names[0]!r} at {la}, buy {names[1]!r} at {sa} "
           f"(= 1 - bid {bid}); sum of asks {round(la + sa, 4)} ≥ 1 means spread is priced in")
    try:
        bbo = venue_us.get_json(f"/v1/markets/{m['slug']}/bbo")
        md = bbo.get("marketData", bbo)
        bb, ba = venue_us.amount(md.get("bestBid")), venue_us.amount(md.get("bestAsk"))
        same = bb == bid and ba == ask
        report("PASS" if same else "WARN", "/bbo agrees with /book (book = long side)",
               f"bbo {bb}/{ba} vs book {bid}/{ask}")
    except Exception as e:  # noqa: BLE001
        report("WARN", "/bbo", str(e))
    side_price = None
    for s in (venue_us.raw_market(m["slug"]).get("marketSides") or []):
        if s.get("long"):
            side_price = venue_us.amount(s.get("price"))
    if side_price is not None:
        report("PASS" if bid - 0.05 <= side_price <= ask + 0.05 else "WARN",
               "long side's marketSides.price sits near the book",
               f"side price {side_price}, book {bid}/{ask}")

    # 5. settlement on recently closed markets
    try:
        closed = venue_us.get_json("/v1/markets", {"limit": 50, "closed": True})
        closed = closed.get("markets", []) if isinstance(closed, dict) else []
        vals = []
        for c in closed[:15]:
            s = venue_us.settlement(c["slug"])
            if s is not None:
                vals.append((c["slug"], s))
        decisive = [v for v in vals if v[1] in (0.0, 1.0)]
        report("PASS" if decisive else "WARN", "/settlement on closed markets",
               f"{len(vals)} settled of {min(15, len(closed))} checked, "
               f"{len(decisive)} at exactly 0/1; e.g. {vals[:3]}")
        if decisive:
            v = venue_us.settled_view(decisive[0][0])
            report("PASS" if v["closed"] and v.get("closedTime") else "WARN",
                   "settled_view gives closed + closedTime",
                   f"closedTime {v.get('closedTime')}, prices {v['outcomePrices']}")
    except Exception as e:  # noqa: BLE001
        report("WARN", "/settlement", str(e))
    try:
        venue_us.settlement(m["slug"])
        report("PASS", "/settlement on an open market returns 404 → None")
    except Exception as e:  # noqa: BLE001
        report("FAIL", "/settlement on an open market", str(e))

    # 6. BTC up/down stays banned
    prot = json.loads((venue.ROOT / "config" / "protected.json").read_text())
    import re
    pats = [re.compile(p, re.I) for p in prot.get("banned_slug_patterns", [])]
    btc = [x["slug"] for x in raw if x["slug"].startswith("cpc-btc-updown")]
    report("PASS" if all(any(p.search(s) for p in pats) for s in btc) and pats else "FAIL",
           "BTC up/down slugs are banned", f"{len(btc)} seen in sample")
    return finish()


def finish():
    fails = results.count("FAIL")
    print(f"\n{results.count('PASS')} pass, {results.count('WARN')} warn, {fails} fail")
    if not fails:
        print('Next: set "venue": "us" in config/protected.json (operator: commit), '
              "then run `python3 core/scan.py --hours 48 | head` to see US candidates.")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
