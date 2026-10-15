#!/usr/bin/env python3
"""Offline tests for the sports lanes: classification, ledger lane rules,
threshold tuning rules and the CI replay. A local stub stands in for the
Polymarket US gateway (PHIL_US_GATEWAY), so nothing touches the network.

Run: python3 core/test_lanes.py -v
"""
import datetime as dt
import http.server
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "core"))
import lanes  # noqa: E402

MARKETS = {}
BOOKS = {}


def mk(slug, typ, game="g1", bid=0.45, ask=0.47, start="2026-10-20T00:00:00Z"):
    MARKETS[slug] = {"slug": slug, "id": slug, "question": f"Q {slug}", "category": "sports",
                     "sportsMarketType": typ, "gameId": game, "gameStartTime": start,
                     "endDate": "2026-10-21T00:00:00Z", "active": True, "closed": False,
                     "status": "MARKET_STATUS_OPEN",
                     "marketSides": [{"long": True, "description": "Home"},
                                     {"long": False, "description": "Away"}]}
    BOOKS[slug] = (bid, ask)


class Stub(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p.startswith("/v1/market/slug/"):
            slug = p.rsplit("/", 1)[1]
            return self._send(200, {"market": MARKETS[slug]}) if slug in MARKETS else self._send(404, {})
        if p.startswith("/v1/markets/") and p.endswith("/book"):
            slug = p.split("/")[3]
            bid, ask = BOOKS.get(slug, (None, None))
            return self._send(200, {"marketData": {
                "bids": [{"px": {"value": str(bid)}}] if bid else [],
                "offers": [{"px": {"value": str(ask)}}] if ask else []}})
        if p.endswith("/settlement"):
            return self._send(404, {})
        if p == "/v1/markets":
            return self._send(200, {"markets": list(MARKETS.values())})
        return self._send(404, {})


class LaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.gw = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        MARKETS.clear()
        BOOKS.clear()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.root = self.tmp / "repo"
        shutil.copytree(REPO, self.root, ignore=shutil.ignore_patterns(".git", "__pycache__", "work"))
        (self.root / "journal" / "ledger.jsonl").write_text("")
        (self.root / "journal" / "forecasts.jsonl").write_text("")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def py(self, *args):
        env = dict(os.environ, PHIL_US_GATEWAY=self.gw)
        return subprocess.run([sys.executable, *args], cwd=self.root, env=env,
                              capture_output=True, text=True)

    def place(self, slug, est, stake, *extra):
        return self.py("core/ledger.py", "place", "--market-id", slug, "--outcome", "Home",
                       "--est-prob", str(est), "--stake", str(stake), "--category", "t",
                       "--edge-class", "other", "--rationale", "test", *extra)

    def ledger(self):
        return [json.loads(x) for x in (self.root / "journal" / "ledger.jsonl").read_text().splitlines() if x]

    def write_settled(self, lane, league, n, est=0.6, mkt=0.5, won_every=1, start="2026-10-01T00:00:00Z"):
        t0 = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
        rows = self.ledger()
        for i in range(n):
            ts = (t0 + dt.timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            won = (i % won_every == 0)
            rows.append({"id": f"r{len(rows)}", "ts": ts, "market_id": f"tec-{league}-x-{i}",
                         "slug": f"tec-{league}-x-{i}", "question": "q", "outcome": "Home",
                         "token_id": "us:x:long", "entry_price": mkt, "est_prob": est,
                         "market_prob_at_entry": mkt, "stake_usd": 1.0, "shares": round(1 / mkt, 4),
                         "status": "won" if won else "lost", "settled_ts": ts,
                         "pnl_usd": round(1 / mkt - 1, 4) if won else -1.0,
                         "lane": lane, "league": league, "tier": 1})
        (self.root / "journal" / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    # ------------------------------------------------------------ classify

    def test_classify_types_seen_live(self):
        cases = {
            ("tec-nfl-a", "football_team_full_game_winner"): ("football", 1),
            ("tec-nfl-a", "football_team_full_game_spread"): ("football", 1),
            ("tec-nfl-a", "football_team_full_game_total"): ("football", 1),
            ("tec-nfl-a", "football_team_points_full_game_total"): ("football", 2),
            ("tec-nfl-a", "football_team_first_half_spread"): ("football", 2),
            ("tec-nfl-a", "football_game_first_touchdown"): ("football", 3),
            ("tec-nfl-a", "football_game_exact_margin"): ("football", 3),
            ("tec-cfb-a", "futures"): ("football", "future"),
            ("tec-uel-a", "soccer_team_full_time_winner"): ("soccer", 1),
            ("tec-uel-a", "soccer_game_exact_score"): ("soccer", 3),
            ("tec-uel-a", "soccer_game_total_corners"): ("soccer", 3),
            ("tec-uel-a", "soccer_team_second_half_winner"): ("soccer", 2),
            ("tec-nba-a", "basketball_team_full_game_winner"): ("basketball", 1),
            ("aachc-ufc-a", "ufc_fight_winner"): ("combat", 1),
            ("tec-boxing-a", "moneyline"): ("combat", 1),
            ("tec-cs2-a", "esports_series_map_handicap"): ("esports", 2),
            ("tec-r6-a", "esports_map_winner_2"): ("esports", 2),
            ("tec-cs2-a", "esports_map_rounds_handicap_2"): ("esports", 3),
            ("tec-f1-a", "futures"): (None, "future"),
            ("tec-zzz-a", "hockey_team_full_game_winner"): ("hockey", 1),
        }
        for (slug, typ), (lane, tier) in cases.items():
            c = lanes.classify(slug, typ)
            self.assertEqual((c["lane"], c["tier"]), (lane, tier), (slug, typ))

    # ------------------------------------------------------------ ledger

    def test_tier1_bet_is_placed_and_tagged(self):
        mk("tec-nba-w-1", "basketball_team_full_game_winner")
        r = self.place("tec-nba-w-1", 0.55, 5)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        row = self.ledger()[-1]
        self.assertEqual((row["lane"], row["league"], row["tier"]), ("basketball", "nba", 1))
        self.assertEqual(row["game_key"], "nba:game:g1")

    def test_rejections(self):
        mk("tec-nba-w-1", "basketball_team_full_game_winner")
        self.assertIn("min_edge", self.place("tec-nba-w-1", 0.49, 5).stdout + self.place("tec-nba-w-1", 0.49, 5).stderr)
        self.assertIn("stake_usd", self.place("tec-nba-w-1", 0.55, 6).stderr)
        mk("tec-nba-h-1", "basketball_team_first_half_winner")
        self.assertIn("tier 2", self.place("tec-nba-h-1", 0.6, 5).stderr)
        mk("tec-nba-f-1", "futures")
        self.assertIn("forecast only", self.place("tec-nba-f-1", 0.6, 5).stderr)
        mk("tec-f1-f-1", "futures")
        self.assertIn("forecast-only", self.place("tec-f1-f-1", 0.6, 5).stderr)
        mk("tec-nba-wide", "basketball_team_full_game_winner", bid=0.40, ask=0.47)
        self.assertIn("spread", self.place("tec-nba-wide", 0.6, 5).stderr)

    def test_lane_switch_off(self):
        cfg = json.loads((self.root / "config" / "lanes.json").read_text())
        cfg["lanes"]["basketball"]["enabled"] = False
        (self.root / "config" / "lanes.json").write_text(json.dumps(cfg))
        mk("tec-nba-w-1", "basketball_team_full_game_winner")
        self.assertIn("switched off", self.place("tec-nba-w-1", 0.6, 5).stderr)

    def test_no_benchmark_league_capped(self):
        mk("tec-vtb-w-1", "basketball_team_full_game_winner")
        self.assertIn("no outside benchmark", self.place("tec-vtb-w-1", 0.6, 5).stderr)
        self.assertEqual(self.place("tec-vtb-w-1", 0.6, 1).returncode, 0)

    def test_per_game_cap(self):
        mk("tec-nba-w-1", "basketball_team_full_game_winner", game="G")
        mk("tec-nba-s-1", "basketball_team_full_game_spread", game="G")
        mk("tec-nba-t-1", "basketball_team_full_game_total", game="G")
        self.assertEqual(self.place("tec-nba-w-1", 0.6, 5).returncode, 0)
        self.assertEqual(self.place("tec-nba-s-1", 0.6, 5).returncode, 0)
        self.assertIn("per game", self.place("tec-nba-t-1", 0.6, 5).stderr)

    def test_lane_bankroll_is_separate(self):
        self.write_settled("basketball", "nba", 4)
        st = json.loads(self.py("core/ledger.py", "status").stdout)
        self.assertEqual(st["lanes"]["football"]["cash"], 1000.0)
        self.assertNotEqual(st["lanes"]["basketball"]["cash"], 1000.0)

    def test_test_bets_share_and_band(self):
        for i in range(4):
            mk(f"tec-nba-w-{i}", "basketball_team_full_game_winner", game=f"g{i}")
        mk("tec-nba-w-9", "basketball_team_full_game_winner", game="g9")
        # no regular bets yet: a test bet would be 100% of the lane
        self.assertIn("test bets would exceed", self.place("tec-nba-w-9", 0.50, 5, "--test-bet").stderr)
        self.write_settled("basketball", "nba", 4)
        # edge 0.03 is in [0.02, 0.04): allowed as a test bet now (5th of 5)
        r = self.place("tec-nba-w-9", 0.50, 5, "--test-bet")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.ledger()[-1]["test_bet"])
        # too far under min_edge
        self.assertIn("test bet needs edge", self.place("tec-nba-w-0", 0.48, 5, "--test-bet").stderr)

    # ------------------------------------------------------------ params

    def setp(self, *a):
        return self.py("core/params.py", "set", "--lane", "basketball", *a)

    def test_params_rules_and_ci_replay(self):
        r = self.setp("--scope", "nba", "--field", "min_edge", "--value", "0.03",
                      "--reason", "r", "--expect", "e")
        self.assertIn("settled bets", r.stderr)
        self.write_settled("basketball", "nba", 10, start="2026-01-01T00:00:00Z")
        r = self.setp("--scope", "nba", "--field", "min_edge", "--value", "0.01",
                      "--reason", "r", "--expect", "e")
        self.assertIn("max_step", r.stderr)
        r = self.setp("--scope", "nba", "--field", "min_edge", "--value", "0.03",
                      "--reason", "r", "--expect", "e")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.py("core/validate.py").returncode, 0, self.py("core/validate.py").stdout)
        # a hand edit is caught by the CI replay
        p = self.root / "strategy" / "params" / "basketball.json"
        prm = json.loads(p.read_text())
        prm["defaults"]["stake_usd"] = 9.0
        p.write_text(json.dumps(prm))
        v = self.py("core/validate.py")
        self.assertNotEqual(v.returncode, 0)
        self.assertIn("change log", v.stdout)
        prm["defaults"]["stake_usd"] = 5.0
        p.write_text(json.dumps(prm))
        # second change in the same scope needs fresh evidence
        r = self.setp("--scope", "nba", "--field", "max_spread", "--value", "0.05",
                      "--reason", "r", "--expect", "e")
        self.assertIn("since its last", r.stderr)
        # a revert skips evidence and restores the old value
        cid = json.loads(p.read_text())["changes"][0]["id"]
        r = self.setp("--revert", cid, "--reason", "worse")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(p.read_text())["leagues"]["nba"]["min_edge"], 0.04)
        self.assertEqual(self.py("core/validate.py").returncode, 0)
        # a revert cannot itself be reverted (that would dodge the evidence rule)
        rid = json.loads(p.read_text())["changes"][-1]["id"]
        self.assertIn("itself a revert", self.setp("--revert", rid, "--reason", "x").stderr)
        self.assertIn("whole number", self.setp("--scope", "nba", "--field", "max_bet_tier",
                                                "--value", "1.5", "--reason", "r", "--expect", "e").stderr)

    def test_lanestats_runs_and_flags_revert(self):
        self.write_settled("basketball", "nba", 10, est=0.5, mkt=0.5, start="2026-01-01T00:00:00Z")
        r = self.setp("--scope", "nba", "--field", "min_edge", "--value", "0.03",
                      "--reason", "r", "--expect", "e")
        self.assertEqual(r.returncode, 0, r.stderr)
        # after the change: confident and wrong -> worse than before
        self.write_settled("basketball", "nba", 10, est=0.9, mkt=0.5, won_every=10,
                           start="2099-01-01T00:00:00Z")
        rep = json.loads(self.py("core/lanestats.py").stdout)
        self.assertEqual(len(rep["lanes"]["basketball"]["revert_due"]), 1)
        self.assertEqual(rep["total"]["start"], 8000.0)
        c = self.py("core/lanestats.py", "--compact")
        self.assertIn("REVERT DUE", c.stdout)

    # ------------------------------------------------------------ shadow bets

    def write_forecasts(self, league, n, est, ask, won, bid=None, start="2026-02-01T00:00:00Z"):
        t0 = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
        path = self.root / "journal" / "forecasts.jsonl"
        rows = [json.loads(x) for x in path.read_text().splitlines() if x]
        for i in range(n):
            ts = (t0 + dt.timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            rows.append({"id": f"f{len(rows)}", "ts": ts, "market_id": f"aec-{league}-g{i}",
                         "slug": f"aec-{league}-g{i}", "question": "q", "outcome": "Home",
                         "token_id": "us:x:long", "est_prob": est,
                         "best_bid_at_record": bid if bid is not None else round(ask - 0.01, 4),
                         "best_ask_at_record": ask, "market_prob_at_record": ask - 0.005,
                         "category": league, "skip_reason": "no-edge",
                         "status": "won" if won else "lost", "settled_ts": ts,
                         "lane": "basketball", "league": league, "tier": 1})
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_shadow_bets_unlock_loosening(self):
        # edge 0.03 (in [0.02, 0.04)), outcome happened: agent closer than the ask
        self.write_forecasts("nba", 15, est=0.53, ask=0.50, won=True)
        rep = json.loads(self.py("core/lanestats.py", "--lane", "basketball").stdout)
        sh = rep["lanes"]["basketball"]["leagues"]["nba"]["shadow_next_step"]
        self.assertEqual((sh["n"], sh["can_loosen_min_edge"]), (15, True))
        # raising is never allowed on shadow evidence
        self.assertIn("settled bets", self.setp("--scope", "nba", "--field", "min_edge", "--value", "0.05",
                                                "--reason", "r", "--expect", "e").stderr)
        r = self.setp("--scope", "nba", "--field", "min_edge", "--value", "0.02",
                      "--reason", "shadow n=15 beat market", "--expect", "more bets, same accuracy")
        self.assertEqual(r.returncode, 0, r.stderr)
        ch = json.loads((self.root / "strategy" / "params" / "basketball.json").read_text())["changes"][-1]
        self.assertEqual((ch["evidence"], ch["shadow"]["n"]), ("shadow", 15))
        v = self.py("core/validate.py")
        self.assertEqual(v.returncode, 0, v.stdout)
        # the same change written by hand without enough forecasts fails CI
        (self.root / "journal" / "forecasts.jsonl").write_text("")
        self.assertIn("shadow", self.py("core/validate.py").stdout)

    def test_shadow_rejects_losing_band_and_wide_books(self):
        self.write_forecasts("nba", 15, est=0.53, ask=0.50, won=False)
        self.assertIn("do not qualify", self.setp("--scope", "nba", "--field", "min_edge", "--value", "0.02",
                                                  "--reason", "r", "--expect", "e").stderr)
        (self.root / "journal" / "forecasts.jsonl").write_text("")
        self.write_forecasts("nba", 15, est=0.53, ask=0.50, won=True, bid=0.10)  # placeholder book
        rep = json.loads(self.py("core/lanestats.py", "--lane", "basketball").stdout)
        self.assertEqual(rep["lanes"]["basketball"]["leagues"]["nba"]["shadow_next_step"]["n"], 0)


if __name__ == "__main__":
    unittest.main()
