# Proposals to the operator

Append evidence-backed proposals for changes to protected files here.

## 2026-10-15 19:4xZ — screen strata do not fit venue `us` game markets

Evidence: prepare at reports/screener-work/20261015T193544Z (8,377
candidates): `closing_48h` eligible 0, because a US game market's endDate
is ~14 days after kickoff (see strategy/discovery.py docstring); and
`top_liquidity`/`top_volume_24h` rank on fields that are always 0 on `us`,
so 180 of 220 pool slots were filled in effectively arbitrary order. The
pool held no NFL, NBA, NHL or MLB game; 6,515 of the candidates were
football, mostly line-constructed.

Proposal (core/screen.py): on venue `us`, fill `closing_48h` by
gameStartTime instead of endDate, and either treat a two-sided bid/ask
spread as the liquidity proxy or skip the liquidity strata.

## 2026-10-15 19:4xZ — agent cannot run its own strategy tools

In this session's permission set, `python3 core/...` runs but `python3
strategy/tools/*.py`, ad-hoc `python3 -c` and `printenv` all need approval
that a headless run cannot give. So devig.py, quote.py and siblings.py are
unusable mid-cycle, and step 0's PHIL_LEASE / PHIL_PUSH_BY_LOOP checks
cannot be read. Proposal: allow `python3 strategy/tools/*` and `printenv
PHIL_*` in .claude/settings.json.

## 2026-10-15 20:3xZ — placeholder books still take the screener's top slots

The 20:27Z collect escalated 20; the top 19 by divergence were 0.89-0.91 /
0.09-0.11 mids on AHL, DEL, ACB, BSL, CBA and esports games, every one
flagged "placeholder book?" by its screener. `exclude_mids_exactly_half`
catches the 0.500 placeholder but not this one, and screener-filters.json
has no mid-based rule the agent can add. Proposal (core/screen.py): an
`exclude_mids_at_or_beyond` filter (e.g. 0.89) for venue `us`, or drop
markets whose live book is one-sided, so escalation slots go to priced games.

## 2026-10-15 21:2xZ — the screened pool is alphabetical, not ranked

Evidence for the 19:4xZ strata proposal: the 21:19Z pool
(reports/screener-work/20261015T211905Z, 220 of 8,763) held only acb, ahl,
atp, boxing, bsl, cba, cfb, cs2, del, dota2 and one nhl market. Since
liquidity and volume_24h are 0 on every `us` market, the strata keep slug
order, and leagues after "d" never reach the screener: the scan had 14 NBA,
2 MLB LCS, 1 UFC, 15 WTA and ~960 soccer markets that the pool skipped. The
agent can only shrink strata sizes (strategy/screener-strata.json), which
cannot fix order. Proposal (core/screen.py): on venue `us`, rank
top_liquidity by two-sided book spread, or round-robin the pool across
leagues, so each switched-on lane gets slots.
