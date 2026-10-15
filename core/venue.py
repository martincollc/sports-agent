"""Venue dispatch: international Polymarket (core/pmapi.py) or Polymarket US
(core/venue_us.py). Stdlib only.

PROTECTED CORE — the trading agent must not edit files under core/.

  * NEW rows (scan, ledger place, forecast record) use the venue named in
    config/protected.json -> "venue" ("intl" default, or "us").
  * EXISTING rows are always read and settled on the venue they were placed
    on: row["venue"], absent on pre-port rows, which means "intl". So flipping
    the switch never strands open positions.
  * Prices dispatch on the token id itself: US tokens are "us:<slug>:<side>".

Every function returns gamma-shaped market dicts either way.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import pmapi  # noqa: E402
import venue_us  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENUES = ("intl", "us")


def current(protected=None):
    if protected is None:
        protected = json.loads((ROOT / "config" / "protected.json").read_text())
    v = protected.get("venue", "intl")
    if v not in VENUES:
        raise SystemExit(f"config/protected.json: unknown venue {v!r} (expected one of {VENUES})")
    return v


def row_venue(row):
    return row.get("venue") or "intl"


def get_market(market_id, venue):
    if venue == "us":
        return venue_us.market(market_id)
    return pmapi.gamma_market(market_id)


def settlement_market(market_id, venue):
    """Market record for core/resolve.py (US: merged with /settlement)."""
    if venue == "us":
        return venue_us.settled_view(market_id)
    return pmapi.gamma_market(market_id)


def list_markets(venue, **params):
    if venue == "us":
        return venue_us.markets(**params)
    return pmapi.gamma_markets(**params)


def supported_versions(venue):
    return {venue_us.VERSION} if venue == "us" else set(pmapi.SUPPORTED_VERSIONS)


def market_version(m):
    return m.get("version") or "v1"


def token_ids(m):
    if m.get("venue") == "us":
        try:
            ids = json.loads(m.get("clobTokenIds") or "null")
        except (ValueError, TypeError):
            return None
        return ids if isinstance(ids, list) and len(ids) == 2 else None
    return pmapi.token_ids(m)


def market_tokens(m):
    """outcome name -> token id. For US markets 'Yes'/'No' are also accepted
    as names for the long/short side (see venue_us.side_of)."""
    if m.get("venue") == "us":
        ids = token_ids(m)
        if ids is None:
            return None
        names = json.loads(m["outcomes"])
        return dict(zip(names, ids))
    return pmapi.market_tokens(m)


def resolve_outcome(m, outcome):
    """Canonical outcome name for `outcome` on this market, or None."""
    tokens = market_tokens(m) or {}
    if outcome in tokens:
        return outcome
    if m.get("venue") == "us":
        is_long = venue_us.side_of({"marketSides": []}, outcome)
        if is_long is not None:
            names = json.loads(m["outcomes"])
            return names[0] if is_long else names[1]
    return None


def best_prices(token):
    if venue_us.is_token(token):
        return venue_us.best_prices(token)
    return pmapi.best_prices(token)
