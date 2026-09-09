"""Find the trades that look like someone hedging a contract, not betting.

    uv run python -m analysis.anomalies [--db PATH] [--out DIR]

A coach's contract pays bonuses at season milestones: bowl selection, a
conference title, a playoff berth, a run to the semifinal, a national
championship. Kalshi settles each contract at $1, so a person who owes those
bonuses can buy a number of contracts equal to the money owed and be covered
if the team wins. That is the pattern this module looks for, and it looks for
it in the trading record alone -- nothing here needs a copy of anyone's
contract.

Six views come out of it:

  block_trades    Kalshi's own `is_block_trade` flag. The exchange's word.
  large_trades    Orders clearing a size floor, with an in-market percentile
                  where the market has enough trades to have a distribution.
  ladder_events   The finding: one school, several rungs of the season ladder
                  bought in one window at prices that are not a sure thing.
  ladder_leads    Everything ladder-shaped that failed one of those tests,
                  each row carrying the reason it is a lead and not a finding.
  timeline_daily  Daily notional by school, market type and platform, dated
                  in America/Chicago, plus a totals table that is safe to sum.

The three things that separate a hedge from a large trade:

**Direction.** A bonus hedge is a Yes purchase. The person owes money when
the team succeeds, so they buy the success. A No taker on the same market is
making the opposite bet and is not hedging a bonus.

**Price.** 62 of the 73 orders clearing the size floors on ladder markets are
No takers, and 39 of those paid 95 cents or more per contract -- parking cash
for a 1-to-5% return on a team that will not win. That is a yield trade. It
walks several rungs of one school's ladder in minutes and would otherwise be
indistinguishable from a hedge.

**Shape, not size.** Several different rungs in one window is the signal. A
big trade on one rung is a lead.

On fills and orders. Kalshi's API returns fills, not orders, and publishes no
order id: one taker order crossing several resting orders comes back as
several rows sharing a timestamp. One market here has 24 such rows at the
same microsecond. Fills sharing a market, a timestamp and a taker side are
therefore combined into one order before any floor is applied, because a
hedge routed through the book instead of negotiated as a block arrives in
pieces and every piece is under the floor. The grouping is a proxy for an
order, not an order id, because the feed does not carry one.

A trade the exchange itself flagged as a block skips the floors entirely.
South Carolina's flagged 40,000 contracts at $0.11 cost $4,400, and the
exchange's own flag is better evidence than our threshold.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sqlite3
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from collectors.common import PROJECT_ROOT, load_yaml

CENTRAL = ZoneInfo("America/Chicago")

# Defaults, and why each is the number it is. Sweeping the archive at floors of
# 1,000 / 5,000 / 10,000 / 25,000 contracts returned 11 / 1 / 1 / 1 multi-rung
# clusters; the one that survives every floor is the LSU hedge of 13 Aug 2026.
# 5,000 is the loosest floor that is not yet noisy. Both numbers were derived
# on an archive holding six of the ladder's series, so they are provisional
# until the wide pass lands -- see METHODOLOGY.
MIN_CONTRACTS = 5_000
MIN_TAKER_COST = 5_000.0
WINDOW_MINUTES = 15
# A taker paying this much per contract is buying a near-certainty for a few
# points of yield, not insuring against an outcome.
NEAR_CERTAIN_PRICE = 0.95
# A percentile needs a distribution. Below this many trades in a market, the
# largest trade is trivially the highest and means nothing.
MIN_TRADES_FOR_PERCENTILE = 30


def load_ladder(path: Path) -> tuple[dict[str, str], list[str], dict]:
    """series id -> rung label, rung order low to high, and the tier table."""
    cfg = load_yaml(path)
    series_rung, order = {}, []
    for entry in cfg["ladder"]:
        order.append(entry["rung"])
        for s in entry["series"]:
            series_rung[s] = entry["rung"]
    return series_rung, order, cfg.get("bonus_tiers") or {}


def central_day(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, CENTRAL).strftime("%Y-%m-%d")


def utc_iso(served, ts: int) -> str:
    """A readable UTC timestamp, whatever the platform served.

    Kalshi serves RFC 3339 to the microsecond and that string is kept
    verbatim. Polymarket serves a whole-second unix epoch, which the store
    also keeps verbatim, so `executed_iso` holds the digits of an epoch for
    those rows. Printing one next to the other invites a reader to compare
    them, so the epoch is rendered here and the Kalshi string passes through
    untouched -- no precision is invented that Polymarket did not send.
    """
    if isinstance(served, str) and "T" in served:
        return served
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_round(count: float, step: int = 2500) -> bool:
    return float(count).is_integer() and int(count) % step == 0


def fetch_milestone_orders(db: sqlite3.Connection, series_rung: dict) -> tuple[list[dict], int]:
    """Ladder fills combined into taker orders, with the school and rung.

    Also returns the number of ladder markets carrying trades that resolved to
    no school, because a resolver miss is a rung the detector cannot see and
    should be counted out loud rather than dropped by a WHERE clause.
    """
    marks = ",".join("?" * len(series_rung))
    orphans = db.execute(f"""
        SELECT COUNT(DISTINCT m.source_market_id) FROM market m
         WHERE m.source = 'kalshi' AND m.team IS NULL
           AND m.series_id IN ({marks})
           AND EXISTS (SELECT 1 FROM trade t WHERE t.source = 'kalshi'
                        AND t.source_market_id = m.source_market_id)
    """, list(series_rung)).fetchone()[0]

    cur = db.execute(f"""
        SELECT t.source_market_id, m.series_id, m.team, m.title,
               t.executed_ts, t.executed_iso, t.taker_side,
               COUNT(*)                          AS fills,
               SUM(t.count)                      AS contracts,
               SUM(COALESCE(t.taker_cost_usd, 0)) AS taker_cost_usd,
               MAX(t.is_block_trade)             AS has_block,
               MIN(t.raw_path)                   AS raw_path
          FROM trade t
          JOIN market m ON m.source = t.source
                       AND m.source_market_id = t.source_market_id
         WHERE t.source = 'kalshi'
           AND m.series_id IN ({marks})
           AND m.team IS NOT NULL
         GROUP BY t.source_market_id, t.executed_iso, t.taker_side
         ORDER BY m.team, t.executed_ts, t.executed_iso
    """, list(series_rung))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["rung"] = series_rung[r["series_id"]]
        r["taker_price"] = round(r["taker_cost_usd"] / r["contracts"], 4) if r["contracts"] else 0.0
    return rows, orphans


def clears_floor(o: dict, min_contracts: int, min_cost: float) -> bool:
    """Big enough to matter, or flagged by the exchange regardless of size."""
    return bool(o["has_block"]) or (
        o["contracts"] >= min_contracts and o["taker_cost_usd"] >= min_cost)


def cluster(rows: list[dict], window_s: int) -> list[list[dict]]:
    """Group one school's consecutive orders while each gap is short.

    Sessionised on the gap rather than bucketed on the clock: a fixed
    15-minute bucket splits any cluster that straddles a boundary. The window
    is a maximum gap between orders, not a maximum span for the cluster, so a
    long chain of closely spaced orders is one cluster however long it runs --
    `span_seconds` in the output says how long that was.

    Grouped on the school alone and not on the taker side. `taker_side`
    describes whoever crossed the spread, so a hedger who rests a bid and
    waits to be hit is recorded as the maker and their fill carries the
    opposite side. Splitting on it would cut such a ladder in half. The side
    split is reported instead.
    """
    out: list[list[dict]] = []
    cur: list[dict] = []
    for r in rows:
        if cur and r["team"] == cur[-1]["team"] \
                and r["executed_ts"] - cur[-1]["executed_ts"] <= window_s:
            cur.append(r)
        else:
            if cur:
                out.append(cur)
            cur = [r]
    if cur:
        out.append(cur)
    return out


def match_tiers(school: str, group: list[dict], tiers: dict) -> tuple[str, str]:
    """Exact and near tier hits, reported separately and never merged.

    Compared against each rung's total across the cluster, not against
    individual orders: a rung bought in two goes still covers one bonus.

    The LSU counts are exactly 837,500, not approximately, and a story saying
    a trade matches a bonus should be able to say which kind of match it was.
    Both strings are empty when no document has been entered for the school,
    which is every school today.
    """
    entry = tiers.get(school)
    if not entry:
        return "", ""
    table = entry.get("tiers") or {}
    per_rung: dict[str, float] = {}
    for o in group:
        per_rung[o["rung"]] = per_rung.get(o["rung"], 0.0) + o["contracts"]
    exact, near = [], []
    for rung, got in sorted(per_rung.items()):
        want = table.get(rung)
        if want is None:
            continue
        if got == want:
            exact.append(f"{rung}={want:,.0f}")
        elif abs(got - want) <= 0.01 * want:
            near.append(f"{rung}~{got:,.0f}/{want:,.0f}")
    return "; ".join(exact), "; ".join(near)


def grade(s: dict) -> str:
    """Why a ladder-shaped cluster is not a finding, or "" if it is one.

    Reported as a column rather than applied as a filter, so a reader can see
    what was set aside and on what grounds.
    """
    if s["rungs"] < 2:
        return "one rung only"
    if s["no_contracts"] > s["yes_contracts"]:
        return "mostly a No purchase; a bonus hedge buys the outcome that costs money"
    if s["max_taker_price"] >= NEAR_CERTAIN_PRICE:
        return (f"paid up to ${s['max_taker_price']:.2f} a contract; "
                "buying a near-certainty for yield, not insuring an outcome")
    return ""


def summarise(group: list[dict], tiers: dict, order: list[str]) -> dict:
    school = group[0]["team"]
    exact, near = match_tiers(school, group, tiers)
    rungs = {o["rung"] for o in group}
    yes = sum(o["contracts"] for o in group if o["taker_side"] == "yes")
    no = sum(o["contracts"] for o in group if o["taker_side"] == "no")
    s = {
        "school": school,
        "rungs": len(rungs),
        "rung_list": ", ".join(r for r in order if r in rungs),
        "orders": len(group),
        "fills": sum(o["fills"] for o in group),
        "first_utc": group[0]["executed_iso"],
        "last_utc": group[-1]["executed_iso"],
        "span_seconds": group[-1]["executed_ts"] - group[0]["executed_ts"],
        "day_central": central_day(group[0]["executed_ts"]),
        "contracts": round(sum(o["contracts"] for o in group), 2),
        "yes_contracts": round(yes, 2),
        "no_contracts": round(no, 2),
        "mixed_side": bool(yes and no),
        "taker_cost_usd": round(sum(o["taker_cost_usd"] for o in group), 2),
        "max_taker_price": max(o["taker_price"] for o in group),
        # Playoff rungs nest -- a team that wins the title also made the
        # semifinal -- so a top-rung win settles every rung below it too and
        # this is the whole ladder's payout, not one rung's.
        "payout_if_every_rung_hits_usd": round(sum(o["contracts"] for o in group), 2),
        "block_orders": sum(1 for o in group if o["has_block"]),
        "all_counts_round_2500": all(is_round(o["contracts"]) for o in group),
        "tier_match_exact": exact,
        "tier_match_within_1pct": near,
        "markets": " | ".join(
            f"{o['source_market_id']}({o['taker_side']} {o['contracts']:,.0f}"
            f" @ ${o['taker_price']:.2f})" for o in group),
        "raw_paths": " | ".join(sorted({o["raw_path"] for o in group})),
    }
    s["not_a_finding_because"] = grade(s)
    return s


def find_ladder_events(db, series_rung, tiers, order, window_s,
                       min_contracts, min_cost):
    orders, orphans = fetch_milestone_orders(db, series_rung)
    kept = [o for o in orders if clears_floor(o, min_contracts, min_cost)]
    events, leads = [], []
    for g in cluster(kept, window_s):
        s = summarise(g, tiers, order)
        (leads if s["not_a_finding_because"] else events).append(s)
    events.sort(key=lambda e: -e["contracts"])
    leads.sort(key=lambda e: -e["contracts"])
    return events, leads, orphans


def block_trades(db) -> list[dict]:
    cur = db.execute("""
        SELECT t.source, t.source_market_id, m.series_id, m.team, m.title,
               t.executed_ts, t.executed_iso, t.count, t.price_dollars,
               t.taker_side, t.taker_cost_usd, t.raw_path
          FROM trade t
          JOIN market m ON m.source = t.source
                       AND m.source_market_id = t.source_market_id
         WHERE t.is_block_trade = 1
         ORDER BY t.executed_ts, t.executed_iso
    """)
    cols = [d[0] for d in cur.description]
    out = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        d["executed_utc"] = utc_iso(d.pop("executed_iso"), d["executed_ts"])
        d["day_central"] = central_day(d.pop("executed_ts"))
        out.append(d)
    return out


def large_trades(db, min_contracts, min_cost) -> list[dict]:
    """Big by both measures, with a percentile only where one is meaningful.

    Rank and depth come from window functions rather than a self-join. A
    timestamp is not a key: one market here has 24 distinct trades stamped to
    the same microsecond, so joining a trade to its own market on the time it
    executed multiplies rows and hands one trade's rank to another.

    Player and draft markets are excluded. This view exists to chase
    school-level milestone trading, those markets are titled with an
    athlete's name, and plan section 9 keeps a dollar figure away from a named
    20-year-old. `analysis/breakdown.py` has the one view that names players,
    and it carries the editor warning.
    """
    cur = db.execute("""
        WITH ranked AS (
          SELECT t.source, t.source_market_id, t.executed_ts, t.executed_iso,
                 t.count, t.price_dollars, t.taker_side, t.taker_cost_usd,
                 t.is_block_trade, t.raw_path,
                 COUNT(*) OVER w                       AS market_trades,
                 RANK() OVER (PARTITION BY t.source, t.source_market_id
                              ORDER BY t.count DESC)   AS rank_in_market
            FROM trade t
          WINDOW w AS (PARTITION BY t.source, t.source_market_id)
        )
        SELECT r.source, r.source_market_id, m.series_id, m.market_type, m.team,
               m.title, r.executed_ts, r.executed_iso, r.count, r.price_dollars,
               r.taker_side, r.taker_cost_usd, r.is_block_trade,
               r.market_trades, r.rank_in_market, r.raw_path
          FROM ranked r
          LEFT JOIN market m ON m.source = r.source
                            AND m.source_market_id = r.source_market_id
         WHERE r.count >= ? AND COALESCE(r.taker_cost_usd, 0) >= ?
           AND COALESCE(m.market_type, '') NOT IN ('player_prop', 'draft')
         ORDER BY r.taker_cost_usd DESC
    """, (min_contracts, min_cost))
    cols = [d[0] for d in cur.description]
    out = []
    for row in cur.fetchall():
        d = dict(zip(cols, row))
        d["executed_utc"] = utc_iso(d.pop("executed_iso"), d["executed_ts"])
        d["day_central"] = central_day(d.pop("executed_ts"))
        d["count_is_round_2500"] = is_round(d["count"])
        # Kalshi counts contracts and Polymarket counts shares. Sorting one
        # list by `count` puts them side by side, so the row says which it is.
        d["units"] = "contracts" if d["source"] == "kalshi" else "shares"
        thin = d["market_trades"] < MIN_TRADES_FOR_PERCENTILE
        d["market_too_thin_for_percentile"] = thin
        # Left blank rather than filled with a word, so the column stays
        # numeric and a spreadsheet can still sort it.
        d["pct_of_market"] = "" if thin else round(
            100.0 * (d["market_trades"] - d["rank_in_market"] + 1) / d["market_trades"], 2)
        out.append(d)
    return out


def timeline_daily(db) -> tuple[list[dict], list[dict]]:
    """Daily notional by school, type and platform, dated in Central time.

    A 6:30pm Central kickoff is 23:30 UTC, so a UTC date cuts a game in half
    and puts the second half on the following day. The UTC date is carried
    alongside so a figure can be checked against a UTC-stamped source.

    **These rows must not be summed across schools.** A market on a game
    names two schools and is counted under both, because "money traded on
    games involving Ole Miss" is the question a per-school row answers. Add
    those rows together and every game market is counted twice: on 7 Sep 2026
    SMU and Florida State each show about $19.9m, and it is the same $19.9m.
    `shared_trades` says how many of a row's trades come from a market naming
    two schools, and the companion totals table is the figure to quote when a
    total is wanted -- it never joins to a school, so nothing is doubled.
    """
    cur = db.execute("""
        WITH named AS (
            SELECT source, source_market_id, market_type, team AS school,
                   0 AS shared
              FROM market WHERE team IS NOT NULL
            UNION ALL
            SELECT source, source_market_id, market_type, away_team, 1
              FROM market WHERE away_team IS NOT NULL
            UNION ALL
            SELECT source, source_market_id, market_type, home_team, 1
              FROM market WHERE home_team IS NOT NULL
        ),
        -- One row per school per market. A spread market names its school in
        -- `team` and again as one side of the game, so without this collapse a
        -- school is counted twice on its own market and its own total inflates.
        involved AS (
            SELECT source, source_market_id, market_type, school,
                   MAX(shared) AS shared
              FROM named GROUP BY source, source_market_id, market_type, school
        )
        SELECT i.school, i.market_type, i.shared, t.source, t.executed_ts,
               t.count, COALESCE(t.taker_cost_usd, 0)
          FROM involved i
          JOIN trade t ON t.source = i.source
                      AND t.source_market_id = i.source_market_id
    """)
    agg: dict[tuple, dict] = {}
    for school, mtype, shared, source, ts, count, cost in cur:
        key = (central_day(ts), school, mtype, source)
        a = agg.setdefault(key, {
            "day_central": key[0], "school": school, "market_type": mtype,
            "source": source, "trades": 0, "shared_trades": 0, "units": 0.0,
            "taker_cost_usd": 0.0, "utc_days": set(),
        })
        a["trades"] += 1
        a["shared_trades"] += shared
        a["units"] += count
        a["taker_cost_usd"] += cost
        a["utc_days"].add(dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d"))
    rows = []
    for a in agg.values():
        a["utc_days"] = ", ".join(sorted(a.pop("utc_days")))
        a["units"] = round(a["units"], 2)
        a["taker_cost_usd"] = round(a["taker_cost_usd"], 2)
        rows.append(a)
    rows.sort(key=lambda r: (r["day_central"], -r["taker_cost_usd"]))
    return rows, timeline_totals(db)


def timeline_totals(db) -> list[dict]:
    """The same days without the school join, so each trade is counted once.

    This is the table to quote a total from. The per-school table cannot give
    one: a game market belongs to both schools playing.
    """
    cur = db.execute("""
        SELECT source, executed_ts, COUNT(*), SUM(count),
               SUM(COALESCE(taker_cost_usd, 0))
          FROM trade GROUP BY source, executed_ts
    """)
    agg: dict[tuple, dict] = {}
    for source, ts, n, units, cost in cur:
        key = (central_day(ts), source)
        a = agg.setdefault(key, {"day_central": key[0], "source": source,
                                 "trades": 0, "units": 0.0, "taker_cost_usd": 0.0})
        a["trades"] += n
        a["units"] += units or 0
        a["taker_cost_usd"] += cost or 0
    rows = sorted(agg.values(), key=lambda r: (r["day_central"], r["source"]))
    for r in rows:
        r["units"] = round(r["units"], 2)
        r["taker_cost_usd"] = round(r["taker_cost_usd"], 2)
    return rows


# Column order for each output, so a file with no rows still has its header.
# A zero-byte ladder_events.csv cannot be told apart from a run that never
# happened, and "no hedge found today" is a result worth being able to read.
HEADERS = {
    "block_trades": [
        "source", "source_market_id", "series_id", "team", "title",
        "executed_utc", "day_central", "count", "price_dollars", "taker_side",
        "taker_cost_usd", "raw_path"],
    "large_trades": [
        "source", "source_market_id", "series_id", "market_type", "team",
        "title", "executed_utc", "day_central", "count", "units",
        "price_dollars", "taker_side", "taker_cost_usd", "is_block_trade",
        "count_is_round_2500", "market_trades", "rank_in_market",
        "pct_of_market", "market_too_thin_for_percentile", "raw_path"],
    "ladder": [
        "school", "rungs", "rung_list", "orders", "fills", "first_utc",
        "last_utc", "span_seconds", "day_central", "contracts",
        "yes_contracts", "no_contracts", "mixed_side", "taker_cost_usd",
        "max_taker_price", "payout_if_every_rung_hits_usd", "block_orders",
        "all_counts_round_2500", "tier_match_exact", "tier_match_within_1pct",
        "not_a_finding_because", "markets", "raw_paths"],
    "timeline_daily": [
        "day_central", "utc_days", "school", "market_type", "source", "trades",
        "shared_trades", "units", "taker_cost_usd"],
    "timeline_daily_totals": [
        "day_central", "source", "trades", "units", "taker_cost_usd"],
}


def write_csv(rows: list[dict], out: Path, name: str, header: str | None = None) -> None:
    cols = HEADERS[header or name]
    if rows:
        drift = set(rows[0]) ^ set(cols)
        if drift:
            raise KeyError(f"{name}.csv columns drifted from HEADERS: {sorted(drift)}")
    out.mkdir(parents=True, exist_ok=True)
    with open(out / f"{name}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "husker.db")
    ap.add_argument("--out", type=Path, default=PROJECT_ROOT / "data" / "analysis")
    ap.add_argument("--milestones", type=Path,
                    default=PROJECT_ROOT / "config" / "milestones.yml")
    ap.add_argument("--window-minutes", type=int, default=WINDOW_MINUTES,
                    help="maximum gap between orders in one cluster, not a span")
    ap.add_argument("--min-contracts", type=int, default=MIN_CONTRACTS)
    ap.add_argument("--min-taker-cost", type=float, default=MIN_TAKER_COST)
    args = ap.parse_args(argv)

    if not args.db.exists():
        print(f"no database at {args.db}; run `python -m normalize.load` first",
              file=sys.stderr)
        return 1

    series_rung, order, tiers = load_ladder(args.milestones)
    db = sqlite3.connect(args.db)

    blocks = block_trades(db)
    larges = large_trades(db, args.min_contracts, args.min_taker_cost)
    events, leads, orphans = find_ladder_events(
        db, series_rung, tiers, order, args.window_minutes * 60,
        args.min_contracts, args.min_taker_cost)
    daily, totals = timeline_daily(db)

    write_csv(blocks, args.out, "block_trades")
    write_csv(larges, args.out, "large_trades")
    write_csv(events, args.out, "ladder_events", header="ladder")
    write_csv(leads, args.out, "ladder_leads", header="ladder")
    write_csv(daily, args.out, "timeline_daily")
    write_csv(totals, args.out, "timeline_daily_totals")

    print("BLOCK TRADES (the exchange's own flag)")
    if not blocks:
        print("  none")
    for b in blocks:
        print("  %-6s %-28s %11s @ $%.2f  %-3s  %s" % (
            b["team"] or "-", b["source_market_id"][:28], f"{b['count']:,.0f}",
            b["price_dollars"], b["taker_side"], b["executed_utc"]))

    print("\nLADDER EVENTS (one school, several rungs, one window, bought)")
    if not events:
        print("  none at %d contracts / $%s / %d-minute gap"
              % (args.min_contracts, f"{args.min_taker_cost:,.0f}", args.window_minutes))
    for e in events:
        print("  %-6s %d rungs over %3ds: %11s contracts, taker paid %12s, "
              "settles for %12s" % (
                  e["school"], e["rungs"], e["span_seconds"],
                  f"{e['contracts']:,.0f}", f"${e['taker_cost_usd']:,.0f}",
                  f"${e['payout_if_every_rung_hits_usd']:,.0f}"))
        print("         %s  (%s, %d block-flagged, up to $%.2f a contract)"
              % (e["rung_list"], e["day_central"], e["block_orders"],
                 e["max_taker_price"]))
        if e["tier_match_exact"]:
            print("         exact bonus tiers: %s" % e["tier_match_exact"])
        if e["tier_match_within_1pct"]:
            print("         within 1%%: %s" % e["tier_match_within_1pct"])
    if not tiers:
        print("  No coach bonus tiers are on file, so no trade is matched to a"
              " contract figure.\n  config/milestones.yml says what a cited"
              " entry looks like.")

    print("\nSET ASIDE AS LEADS")
    reasons: dict[str, int] = {}
    for l in leads:
        reasons[l["not_a_finding_because"]] = reasons.get(l["not_a_finding_because"], 0) + 1
    for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print("  %4d  %s" % (n, reason))
    if orphans:
        print("  %d ladder markets have trades but no school resolved; the"
              " detector cannot see them." % orphans)

    print("\n%d large trades, %d school-days" % (len(larges), len(daily)))
    shared = sum(1 for r in daily if r["shared_trades"])
    print("  %d of those school-days include a game market, which names two\n"
          "  schools and is counted under both. Do not add school rows together;\n"
          "  timeline_daily_totals.csv is the table to take a total from." % shared)
    print("Wrote 6 CSVs to %s" % args.out)
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
