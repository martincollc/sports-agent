#!/usr/bin/env python3
"""Offline tests for the Polymarket US adapter (core/venue_us.py), the venue
dispatcher (core/venue.py) and the US paths through ledger/resolve/scan.

PROTECTED CORE - the trading agent must not edit files under core/.

All gateway responses are hand-built from the docs.polymarket.us schema and
served by a fake get_json, so nothing touches the network. CI runs it on
every push. The live check is strategy/tools/us_smoke.py (operator machine).

Usage: python3 core/test_venue_us.py
"""
import datetime as dt
import io
import json
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import venue  # noqa: E402
import venue_us  # noqa: E402

SLUG = "aec-mlb-nyy-bos-2026-10-15"


def us_market(**over):
    m = {"id": "4242", "slug": SLUG, "question": "Yankees vs. Red Sox",
         "endDate": "2026-10-16T03:00:00Z", "active": True, "closed": False,
         "status": "OPEN", "category": "sports", "volume24hr": 1200,
         "marketSides": [
             {"id": "1", "long": True, "description": "Yankees", "price": "0.58"},
             {"id": "2", "long": False, "description": "Red Sox", "price": "0.42"},
         ]}
    m.update(over)
    return m


def book(bids, offers, **stats):
    return {"marketData": {
        "marketSlug": SLUG,
        "bids": [{"px": {"value": str(p), "currency": "USD"}, "qty": "100"} for p in bids],
        "offers": [{"px": {"value": str(p), "currency": "USD"}, "qty": "100"} for p in offers],
        "state": "MARKET_STATE_OPEN", "stats": stats}}


class FakeGateway:
    """Routes paths to canned bodies; a missing settlement is a 404."""

    def __init__(self, market=None, bk=None, settlement=None, listing=None):
        self.market = market or us_market()
        self.bk = bk or book([0.55, 0.56], [0.59, 0.60])
        self.settlement = settlement
        self.listing = listing
        self.calls = []

    def __call__(self, path, params=None, retries=3):
        self.calls.append((path, params))
        if path.startswith("/v1/market/slug/"):
            return {"market": self.market}
        if path.endswith("/book"):
            return self.bk
        if path.endswith("/settlement"):
            if self.settlement is None:
                raise venue_us.NotFound(path)
            return {"slug": SLUG, "settlement": self.settlement}
        if path == "/v1/markets":
            return {"markets": self.listing or [self.market]}
        raise AssertionError(f"unexpected path {path}")


class Adapter(unittest.TestCase):
    def test_sides_use_descriptions_long_first(self):
        self.assertEqual(venue_us.sides(us_market()),
                         [("Yankees", True), ("Red Sox", False)])

    def test_sides_fall_back_to_yes_no(self):
        m = us_market(marketSides=[{"long": True}, {"long": False}])
        self.assertEqual(venue_us.sides(m), [("Yes", True), ("No", False)])
        self.assertEqual(venue_us.sides(us_market(marketSides=None)),
                         [("Yes", True), ("No", False)])

    def test_yes_no_aliases(self):
        m = us_market()
        self.assertIs(venue_us.side_of(m, "Yankees"), True)
        self.assertIs(venue_us.side_of(m, "yes"), True)
        self.assertIs(venue_us.side_of(m, "No"), False)
        self.assertIsNone(venue_us.side_of(m, "Mets"))

    def test_gamma_view_shape(self):
        v = venue_us.gamma_view(us_market())
        self.assertEqual(v["id"], SLUG)
        self.assertEqual(json.loads(v["outcomes"]), ["Yankees", "Red Sox"])
        self.assertEqual(json.loads(v["outcomePrices"]), [0.58, 0.42])
        self.assertEqual(json.loads(v["clobTokenIds"]),
                         [f"us:{SLUG}:long", f"us:{SLUG}:short"])
        self.assertFalse(v["closed"])
        self.assertTrue(venue_us.gamma_view(us_market(status="RESOLVED"))["closed"])

    def test_price_fallback_to_quotes(self):
        m = us_market(marketSides=[], bestBidQuote={"value": "0.30"},
                      bestAskQuote={"value": "0.34"})
        self.assertEqual(json.loads(venue_us.gamma_view(m)["outcomePrices"]), [0.32, 0.68])
        self.assertEqual(json.loads(venue_us.gamma_view(us_market(marketSides=[]))["outcomePrices"]), [])

    def test_tokens_round_trip(self):
        self.assertEqual(venue_us.parse_token(f"us:{SLUG}:short"), (SLUG, False))
        with self.assertRaises(ValueError):
            venue_us.parse_token(f"us:{SLUG}:sideways")
        with self.assertRaises(ValueError):
            venue_us.parse_token("12345")

    def test_long_fills_at_offer_short_at_one_minus_bid(self):
        with mock.patch.object(venue_us, "get_json", FakeGateway()):
            self.assertEqual(venue_us.best_prices(f"us:{SLUG}:long"), (0.56, 0.59))
            self.assertEqual(venue_us.best_prices(f"us:{SLUG}:short"), (0.41, 0.44))

    def test_empty_book_sides(self):
        with mock.patch.object(venue_us, "get_json", FakeGateway(bk=book([], [0.6]))):
            self.assertEqual(venue_us.best_prices(f"us:{SLUG}:long"), (None, 0.6))
            self.assertEqual(venue_us.best_prices(f"us:{SLUG}:short"), (0.4, None))

    def test_settlement_404_is_none(self):
        with mock.patch.object(venue_us, "get_json", FakeGateway()):
            self.assertIsNone(venue_us.settlement(SLUG))
        with mock.patch.object(venue_us, "get_json", FakeGateway(settlement="1.00")):
            self.assertEqual(venue_us.settlement(SLUG), 1.0)

    def test_translate_params(self):
        us, dropped = venue_us.translate_params({
            "closed": "false", "order": "endDate", "ascending": "true",
            "end_date_min": "a", "end_date_max": "b", "volume_num_min": 5,
            "tag_id": 9, "categories": ["sports"], "limit": 100, "offset": 0})
        self.assertEqual(us, {"closed": False, "orderBy": "endDate",
                              "orderDirection": "asc", "endDateMin": "a",
                              "endDateMax": "b", "volumeNumMin": 5,
                              "categories": ["sports"], "limit": 100, "offset": 0})
        self.assertEqual(dropped, ["tag_id"])

    def test_encode_explodes_lists_and_bools(self):
        self.assertEqual(venue_us._encode({"categories": ["a", "b"], "closed": False, "x": None}),
                         "categories=a&categories=b&closed=false")


class Dispatch(unittest.TestCase):
    def test_market_tokens_and_outcome_aliases(self):
        m = venue_us.gamma_view(us_market())
        self.assertEqual(venue.market_tokens(m),
                         {"Yankees": f"us:{SLUG}:long", "Red Sox": f"us:{SLUG}:short"})
        self.assertEqual(venue.resolve_outcome(m, "Yes"), "Yankees")
        self.assertEqual(venue.resolve_outcome(m, "No"), "Red Sox")
        self.assertEqual(venue.resolve_outcome(m, "Red Sox"), "Red Sox")
        self.assertIsNone(venue.resolve_outcome(m, "Mets"))

    def test_intl_rows_stay_intl(self):
        self.assertEqual(venue.row_venue({"market_id": "1"}), "intl")
        with mock.patch.object(venue.pmapi, "best_prices", return_value=(0.1, 0.2)) as bp:
            self.assertEqual(venue.best_prices("98765"), (0.1, 0.2))
            bp.assert_called_once_with("98765")

    def test_unknown_venue_rejected(self):
        with self.assertRaises(SystemExit):
            venue.current({"venue": "kalshi"})
        self.assertEqual(venue.current({}), "intl")


class Resolve(unittest.TestCase):
    def settle(self, row, gw):
        import resolve
        with mock.patch.object(venue_us, "get_json", gw):
            m = venue.settlement_market(row["market_id"], "us")
        return resolve.settle_against_market(row, m, dt.datetime(2026, 10, 19, tzinfo=dt.timezone.utc))  # past the 48h void grace

    def row(self, outcome):
        return {"market_id": SLUG, "venue": "us", "outcome": outcome, "shares": 10.0,
                "stake_usd": 5.0, "end_date": "2026-10-16T03:00:00Z", "status": "open"}

    def test_long_wins_on_settlement_one(self):
        gw = FakeGateway(settlement="1.00", bk=book([], [], settlementSetTime="2026-10-16T04:01:00Z"))
        r = self.row("Yankees")
        self.assertTrue(self.settle(r, gw))
        self.assertEqual((r["status"], r["pnl_usd"], r["outcome_won"]), ("won", 5.0, "Yankees"))
        self.assertEqual(r["settled_ts"], "2026-10-16T04:01:00Z")

    def test_short_wins_on_settlement_zero(self):
        r = self.row("Red Sox")
        self.assertTrue(self.settle(r, FakeGateway(settlement="0.00")))
        self.assertEqual((r["status"], r["pnl_usd"]), ("won", 5.0))
        self.assertEqual(r["settled_ts"], "2026-10-16T03:00:00Z")  # falls back to endDate
        r = self.row("Yankees")
        self.settle(r, FakeGateway(settlement="0.00"))
        self.assertEqual((r["status"], r["pnl_usd"]), ("lost", -5.0))

    def test_unsettled_stays_open_even_if_status_closed(self):
        r = self.row("Yankees")
        self.assertFalse(self.settle(r, FakeGateway(market=us_market(status="RESOLVING"))))
        self.assertEqual(r["status"], "open")

    def test_non_decisive_settlement_voids_after_grace(self):
        r = self.row("Yankees")
        self.assertTrue(self.settle(r, FakeGateway(settlement="0.5")))
        self.assertEqual((r["status"], r["pnl_usd"]), ("void", 0.0))

    def test_main_settles_each_row_on_its_own_venue(self):
        import resolve
        with tempfile.TemporaryDirectory() as d:
            ledger = pathlib.Path(d) / "ledger.jsonl"
            intl = {"id": "a", "market_id": "555", "outcome": "Yes", "shares": 10.0,
                    "stake_usd": 5.0, "end_date": "2026-10-16T03:00:00Z", "status": "open",
                    "est_prob": 0.6, "entry_price": 0.5, "category": "x", "question": "intl?"}
            us = dict(self.row("Red Sox"), id="b", est_prob=0.6, entry_price=0.5,
                      category="x", question="us?")
            ledger.write_text(json.dumps(intl) + "\n" + json.dumps(us) + "\n")
            gamma_closed = {"closed": True, "outcomes": '["Yes","No"]',
                            "outcomePrices": '["1","0"]', "closedTime": "2026-10-16T05:00:00Z"}
            seen = []

            def fake_settlement_market(mid, v):
                seen.append((v, mid))
                if v == "intl":
                    return gamma_closed
                with mock.patch.object(venue_us, "get_json", FakeGateway(settlement="1.00")):
                    return venue_us.settled_view(mid)

            with mock.patch.object(resolve, "LEDGER", ledger), \
                    mock.patch.object(resolve, "FORECASTS", pathlib.Path(d) / "f.jsonl"), \
                    mock.patch.object(resolve.venue, "settlement_market", fake_settlement_market), \
                    redirect_stdout(io.StringIO()):
                resolve.main()
            rows = [json.loads(x) for x in ledger.read_text().splitlines()]
        self.assertEqual(sorted(seen), [("intl", "555"), ("us", SLUG)])
        self.assertEqual([r["status"] for r in rows], ["won", "lost"])


class LedgerPlace(unittest.TestCase):
    def place(self, outcome, gw, protected_over=None, existing=()):
        import ledger
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "ledger.jsonl"
            path.write_text("".join(json.dumps(r) + "\n" for r in existing))
            prot = dict(ledger.PROTECTED, venue="us", **(protected_over or {}))
            args = SimpleNamespace(market_id=SLUG, outcome=outcome, est_prob=0.5,
                                   stake=5.0, category="mlb", edge_class="book-devig",
                                   rationale="t", strategy_rev="")
            out = io.StringIO()
            with mock.patch.object(ledger, "LEDGER", path), \
                    mock.patch.object(ledger, "PROTECTED", prot), \
                    mock.patch.object(venue_us, "get_json", gw), \
                    mock.patch.object(ledger, "lane_checks", return_value={}), \
                    redirect_stdout(out):
                # Sports agent: lane rules are covered by core/test_lanes.py;
                # these tests pin the fill mechanics only.
                ledger.cmd_place(args, list(existing))
            return [json.loads(x) for x in path.read_text().splitlines()]

    def test_place_no_side_fills_at_one_minus_bid(self):
        rows = self.place("No", FakeGateway())
        r = rows[-1]
        self.assertEqual((r["venue"], r["market_id"], r["outcome"]), ("us", SLUG, "Red Sox"))
        self.assertEqual((r["token_id"], r["entry_price"]), (f"us:{SLUG}:short", 0.44))
        self.assertEqual(r["shares"], round(5.0 / 0.44, 4))

    def test_place_long_fills_at_offer(self):
        r = self.place("Yankees", FakeGateway())[-1]
        self.assertEqual((r["token_id"], r["entry_price"], r["best_bid_at_entry"]),
                         (f"us:{SLUG}:long", 0.59, 0.56))

    def test_duplicate_via_alias_rejected(self):
        first = self.place("Yes", FakeGateway())[-1]
        with self.assertRaises(SystemExit):
            self.place("Yankees", FakeGateway(), existing=[first])

    def test_btc_updown_slug_banned(self):
        gw = FakeGateway(market=us_market(slug="cpc-btc-updown-15m-2026-10-15-0515z",
                                          question="BTC Up or Down 15m"))
        with self.assertRaises(SystemExit) as cm:
            self.place("Yes", gw)
        self.assertIn("banned_slug_pattern", str(cm.exception))


class Scan(unittest.TestCase):
    def test_keep_handles_us_records_and_slug_ban(self):
        import re
        import scan
        args = SimpleNamespace(min_volume_24h=0)
        rec = scan.keep(venue_us.gamma_view(us_market()), set(), [], args)
        self.assertEqual(rec["market_id"], SLUG)
        self.assertEqual(rec["clob_token_ids"], [f"us:{SLUG}:long", f"us:{SLUG}:short"])
        with mock.patch.object(scan, "SLUG_BANNED", [re.compile("^cpc-btc-updown-")]):
            btc = venue_us.gamma_view(us_market(slug="cpc-btc-updown-1h-2026-10-15-0500z"))
            self.assertIsNone(scan.keep(btc, set(), [], args))


if __name__ == "__main__":
    unittest.main()
