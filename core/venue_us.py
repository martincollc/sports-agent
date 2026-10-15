"""Polymarket US (gateway.polymarket.us) read-only adapter. Stdlib only.

PROTECTED CORE — the trading agent must not edit files under core/.

Polymarket US is a separate, CFTC-regulated exchange with its own public API.
It differs from international Polymarket (Gamma/CLOB, core/pmapi.py) in ways
that matter to the simulator:

  * Markets are keyed by SLUG. Phil uses the slug as `market_id` for US rows.
  * Each market is ONE instrument. Buying the long side is "Yes"; buying the
    short side is "No" (a synthetic No position). There are no separate No
    tokens and only one order book, which prices the long side.
  * So an honest taker fill is: long side buys at the best OFFER; short side
    buys at 1 - best BID (selling the instrument at the bid).
  * Settlement: GET /v1/markets/{slug}/settlement returns the settlement price
    (1.00 = long/Yes won, 0.00 = short/No won); 404 until it settles.

This module turns US records into the gamma-shaped dicts the rest of core
already understands (id, question, slug, endDate, closed, outcomes,
outcomePrices, clobTokenIds ...), so ledger/forecast/scan/resolve need only
dispatch through core/venue.py rather than learn a second record format.

Token ids are synthetic: "us:<slug>:long" / "us:<slug>:short".

Field names follow docs.polymarket.us (checked 2026-10-14). Anything the docs
leave open (e.g. exact side labels, orderBy values) is handled defensively and
exercised live by strategy/tools/us_smoke.py on the operator machine.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GATEWAY = os.environ.get("PHIL_US_GATEWAY", "https://gateway.polymarket.us")
UA = {"User-Agent": "pearl-explorations-paper-trader/0.1",
      "Accept": "application/json"}
TOKEN_PREFIX = "us:"
VERSION = "us"
CLOSED_STATUSES = {"CLOSED", "RESOLVING", "RESOLVED",
                   "MARKET_STATUS_CLOSED", "MARKET_STATUS_RESOLVING",
                   "MARKET_STATUS_RESOLVED"}


class NotFound(Exception):
    """HTTP 404 from the gateway (unknown slug, or not settled yet)."""


def _encode(params):
    out = []
    for k, v in (params or {}).items():
        if v is None:
            continue
        vals = v if isinstance(v, (list, tuple)) else [v]
        for x in vals:
            if isinstance(x, bool):
                x = "true" if x else "false"
            out.append((k, x))
    return urllib.parse.urlencode(out)


def get_json(path, params=None, retries=3):
    url = GATEWAY + path
    qs = _encode(params)
    if qs:
        url += "?" + qs
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(url) from e
            last_err = e
            if e.code == 429:
                time.sleep(2.0 * (attempt + 1))
                continue
        except Exception as e:  # noqa: BLE001 — retry then surface
            last_err = e
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed after {retries} tries: {last_err}")


# ---------------------------------------------------------------- helpers

def amount(a):
    """Amount object {"value": "0.55", "currency": "USD"}, a bare string or a
    number -> float; None when absent or unparseable."""
    if isinstance(a, dict):
        a = a.get("value")
    if a is None or a == "":
        return None
    try:
        return float(a)
    except (TypeError, ValueError):
        return None


def make_token(slug, long):
    return f"{TOKEN_PREFIX}{slug}:{'long' if long else 'short'}"


def is_token(token):
    return isinstance(token, str) and token.startswith(TOKEN_PREFIX)


def parse_token(token):
    """'us:<slug>:long' -> (slug, True). Raises ValueError otherwise."""
    if not is_token(token):
        raise ValueError(f"not a US token: {token!r}")
    slug, _, side = token[len(TOKEN_PREFIX):].rpartition(":")
    if not slug or side not in ("long", "short"):
        raise ValueError(f"malformed US token: {token!r}")
    return slug, side == "long"


def _side_name(s):
    for key in ("description", "identifier"):
        v = s.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    team = s.get("team")
    if isinstance(team, dict):
        for key in ("name", "alias", "abbreviation"):
            v = team.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def sides(m):
    """[(outcome_name, is_long), ...] long side first. Falls back to
    Yes/No when the record has no usable, distinct side labels."""
    long_name = short_name = None
    for s in m.get("marketSides") or []:
        if not isinstance(s, dict) or s.get("long") is None:
            continue
        if s["long"] and long_name is None:
            long_name = _side_name(s)
        elif not s["long"] and short_name is None:
            short_name = _side_name(s)
    if not long_name or not short_name or long_name == short_name:
        long_name, short_name = "Yes", "No"
    return [(long_name, True), (short_name, False)]


def side_of(m, outcome):
    """True for the long side, False for short, None if the name is unknown.
    'Yes'/'No' are always accepted (long/short) per the US market structure."""
    for name, is_long in sides(m):
        if outcome == name:
            return is_long
    low = (outcome or "").strip().lower()
    if low == "yes":
        return True
    if low == "no":
        return False
    return None


def _long_price(m):
    """Indicative long-side probability for scanning (not for fills)."""
    for s in m.get("marketSides") or []:
        if isinstance(s, dict) and s.get("long"):
            p = amount(s.get("price")) or amount(s.get("quote"))
            if p is not None:
                return p
    bid, ask = amount(m.get("bestBidQuote")), amount(m.get("bestAskQuote"))
    if bid is None:
        bid = amount(m.get("bestBid"))
    if ask is None:
        ask = amount(m.get("bestAsk"))
    if bid is not None and ask is not None:
        return (bid + ask) / 2
    return amount(m.get("lastTradePrice"))


def is_closed(m):
    status = str(m.get("status") or "").upper()
    return bool(m.get("closed")) or status in CLOSED_STATUSES


# ---------------------------------------------------------------- records

def raw_market(slug):
    body = get_json(f"/v1/market/slug/{urllib.parse.quote(str(slug), safe='')}")
    return body.get("market", body) if isinstance(body, dict) else body


def gamma_view(m):
    """Normalise a US market record to the gamma-shaped dict core expects."""
    slug = m.get("slug")
    names = [n for n, _ in sides(m)]
    p = _long_price(m)
    prices = [] if p is None else [round(p, 4), round(1 - p, 4)]
    return {
        "id": slug,
        "slug": slug,
        "us_id": m.get("id"),
        "question": m.get("question") or m.get("title"),
        "description": m.get("description"),
        "category": m.get("category"),
        "sportsMarketType": m.get("sportsMarketType"),
        "gameId": m.get("gameId"),
        "gameStartTime": m.get("gameStartTime"),
        "status": m.get("status"),
        "startDate": m.get("startDate"),
        "endDate": m.get("endDate"),
        "createdAt": m.get("createdAt"),
        "closed": is_closed(m),
        "active": m.get("active"),
        "outcomes": json.dumps(names),
        "outcomePrices": json.dumps(prices),
        "clobTokenIds": json.dumps([make_token(slug, True), make_token(slug, False)]),
        "volume24hr": m.get("volume24hr"),
        "liquidityNum": None,  # not exposed by the US gateway
        "events": [],
        "version": VERSION,
        "venue": "us",
    }


def market(slug):
    """Gamma-shaped view of one US market."""
    return gamma_view(raw_market(slug))


# gamma query keys (as strategy/discovery.py writes them) -> US gateway keys
_PARAM_MAP = {
    "end_date_min": "endDateMin", "end_date_max": "endDateMax",
    "start_date_min": "startDateMin", "start_date_max": "startDateMax",
    "volume_num_min": "volumeNumMin", "volume_num_max": "volumeNumMax",
}
_US_KEYS = {
    "limit", "offset", "orderBy", "orderDirection", "id", "slug", "archived",
    "active", "closed", "volumeNumMin", "volumeNumMax", "startDateMin",
    "startDateMax", "endDateMin", "endDateMax", "relatedTags", "gameId",
    "sportsMarketTypes", "includeTag", "categories", "marketTypes",
    "includeHidden", "tagIds",
}


def translate_params(params):
    """Map a gamma-style query onto US gateway parameters.
    Returns (us_params, dropped_keys)."""
    out, dropped = {}, []
    for k, v in params.items():
        if k == "order":
            out["orderBy"] = v
        elif k == "ascending":
            asc = v if isinstance(v, bool) else str(v).lower() == "true"
            out["orderDirection"] = "asc" if asc else "desc"
        elif k in _PARAM_MAP:
            out[_PARAM_MAP[k]] = v
        elif k in _US_KEYS:
            out[k] = v
        else:
            dropped.append(k)
    for k in ("closed", "active", "archived", "includeHidden"):
        if isinstance(out.get(k), str):
            out[k] = out[k].lower() == "true"
    return out, dropped


def markets(**params):
    """List markets (gamma-shaped). Gamma-only keys are dropped."""
    us, dropped = translate_params(params)
    if dropped:
        print(f"venue_us: ignoring gamma-only query keys {sorted(dropped)}",
              file=sys.stderr)
    body = get_json("/v1/markets", us)
    rows = body.get("markets", []) if isinstance(body, dict) else (body or [])
    return [gamma_view(m) for m in rows if isinstance(m, dict) and m.get("slug")]


# ---------------------------------------------------------------- prices

def book(slug):
    body = get_json(f"/v1/markets/{urllib.parse.quote(str(slug), safe='')}/book")
    return body.get("marketData", body) if isinstance(body, dict) else {}


def long_bid_ask(slug):
    """(best_bid, best_offer) on the single instrument, None if a side is empty."""
    md = book(slug)
    bids = [p for p in (amount(b.get("px")) for b in md.get("bids") or []) if p is not None]
    offers = [p for p in (amount(o.get("px")) for o in md.get("offers") or []) if p is not None]
    return (max(bids) if bids else None, min(offers) if offers else None)


def best_prices(token):
    """(best_bid, best_ask) for buying the side named by a US token.
    Short (No) side mirrors the long book: ask = 1 - bid, bid = 1 - offer."""
    slug, is_long = parse_token(token)
    bid, ask = long_bid_ask(slug)
    if is_long:
        return bid, ask
    return (None if ask is None else round(1 - ask, 4),
            None if bid is None else round(1 - bid, 4))


def settlement(slug):
    """Settlement price (float) or None while unsettled (404)."""
    try:
        body = get_json(f"/v1/markets/{urllib.parse.quote(str(slug), safe='')}/settlement")
    except NotFound:
        return None
    return amount(body.get("settlement")) if isinstance(body, dict) else None


def settlement_time(slug):
    """Exchange-reported settlement time from book stats, if exposed."""
    try:
        stats = book(slug).get("stats") or {}
    except (NotFound, RuntimeError):
        return None
    return stats.get("settlementSetTime")


def settled_view(slug):
    """Gamma-shaped view for core/resolve.py: closed + decisive outcomePrices
    once /settlement answers, plus closedTime for a deterministic settled_ts."""
    m = market(slug)
    s = settlement(slug)
    if s is None:
        m["closed"] = False  # not settled yet, whatever the status says
        return m
    m["closed"] = True
    m["outcomePrices"] = json.dumps([round(s, 4), round(1 - s, 4)])
    m["closedTime"] = settlement_time(slug) or m.get("endDate")
    return m
