"""Find the trades that look like someone hedging a contract, not betting.

    uv run python -m analysis.anomalies [--db PATH] [--out DIR]

A coach's contract pays bonuses at milestones: a conference title, a playoff
berth, a run to the semifinal, a national championship. Kalshi settles each
contract at $1, so a person who owes those bonuses can buy a number of
contracts equal to the money they owe and be covered. That is the pattern this
module looks for, and it looks for it in the trading record alone -- nothing
here needs a copy of anyone's contract.

Four views come out of it:

  block_trades   Kalshi's own `is_block_trade` flag. Exact, and the exchange's
                 word rather than ours.
  large_trades   Trades clearing both a contract floor and a cost floor, with
                 an in-market percentile where the market has enough trades to
                 have a distribution at all.
  ladder_events  The finding: one school, one side, several rungs of the
                 season ladder inside one short window.
  ladder_leads   The same shape on a single rung. A lead to check, not a
                 finding to publish.
  timeline_daily Daily notional by school, market type and platform, dated in
                 America/Chicago.

On thresholds. A contract floor on its own is a bad detector: the largest
counts in the archive are penny-priced sweeps of markets that had already
been decided -- 400,139 contracts at $0.01 is $4,001, not a whale. So a trade
must clear a floor in contracts *and* in what the taker paid. Roundness is
recorded but is not a filter: 101 of the 266 non-block Kalshi trades over
50,000 contracts are multiples of 2,500, because round order sizes are
ordinary.

On the window. Clusters are found by gap, not by clock bucket, so a cluster
that straddles :15 is still one cluster.
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

# Defaults, and why each one is the number it is. A sweep of the archive at
# floors of 1,000 / 5,000 / 10,000 / 25,000 contracts returned 11 / 1 / 1 / 1
# multi-rung clusters; the one that survives every floor is the LSU hedge of
# 13 Aug 2026. 5,000 is the loosest floor that is not yet noisy.
MIN_CONTRACTS = 5_000
MIN_TAKER_COST = 5_000.0
WINDOW_MINUTES = 15
# A percentile needs a distribution. Below this many trades in a market, the
# largest trade is trivially the 100th percentile and means nothing.
MIN_TRADES_FOR_PERCENTILE = 30


def load_ladder(path: Path) -> tuple[dict[str, str], list[str], dict]:
    """series id -> rung label, rung order, and the bonus tier table."""
    cfg = load_yaml(path)
    series_rung, order = {}, []
    for entry in cfg["ladder"]:
        order.append(entry["rung"])
        for s in entry["series"]:
            series_rung[s] = entry["rung"]
    return series_rung, order, cfg.get("bonus_tiers") or {}


def central_day(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, CENTRAL).strftime("%Y-%m-%d")


def utc_iso(served: str | int | None, ts: int) -> str:
    """A readable UTC timestamp, whatever the platform served.

    Kalshi serves an RFC 3339 string to the microsecond and that string is
    kept verbatim. Polymarket serves a whole-second unix epoch, which the
    store also keeps verbatim, so `executed_iso` holds an integer for those
    rows. Printing one next to the other invites a reader to compare them, so
    the epoch is rendered here and the Kalshi string is passed through
    untouched -- no precision is invented for Polymarket that it did not send.
    """
    if isinstance(served, str) and "T" in served:
        return served
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_round(count: float, step: int = 2500) -> bool:
    return float(count).is_integer() and int(count) % step == 0


def fetch_milestone_trades(db: sqlite3.Connection, series_rung: dict) -> list[dict]:
    """Every Kalshi trade on a ladder series, with its school and rung."""
    marks = ",".join("?" * len(series_rung))
    cur = db.execute(f"""
        SELECT t.source_market_id, m.series_id, m.team, m.title,
               t.executed_ts, t.executed_iso, t.count, t.price_dollars,
               t.taker_side, t.taker_cost_usd, t.is_block_trade, t.raw_path
          FROM trade t
          JOIN market m ON m.source = t.source
                       AND m.source_market_id = t.source_market_id
         WHERE t.source = 'kalshi'
           AND m.series_id IN ({marks})
           AND m.team IS NOT NULL
         ORDER BY m.team, t.taker_side, t.executed_ts
    """, list(series_rung))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["rung"] = series_rung[r["series_id"]]
    return rows


def cluster(rows: list[dict], window_s: int) -> list[list[dict]]:
    """Group consecutive trades by (school, side) where each gap is short.

    Sessionised on the gap rather than bucketed on the clock: the LSU trades
    span 72 seconds but a fixed 15-minute bucket would split any cluster that
    happened to straddle a boundary.
    """
    out: list[list[dict]] = []
    cur: list[dict] = []
    for r in rows:
        if cur and (r["team"], r["taker_side"]) == (cur[-1]["team"], cur[-1]["taker_side"]) \
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

    The LSU counts are exactly 837,500, not approximately, and a story that
    says a trade matches a bonus should be able to say which kind of match it
    was. Returns two semicolon-joined strings, both empty when no document has
    been entered for the school.
    """
    entry = tiers.get(school)
    if not entry:
        return "", ""
    table = entry.get("tiers") or {}
    exact, near = [], []
    for t in group:
        want = table.get(t["rung"])
        if want is None:
            continue
        got = t["count"]
        if got == want:
            exact.append(f"{t['rung']}={want:,.0f}")
        elif abs(got - want) <= 0.01 * want:
            near.append(f"{t['rung']}~{got:,.0f}/{want:,.0f}")
    return "; ".join(exact), "; ".join(near)


def summarise(group: list[dict], tiers: dict) -> dict:
    school = group[0]["team"]
    exact, near = match_tiers(school, group, tiers)
    return {
        "school": school,
        "taker_side": group[0]["taker_side"],
        "rungs": len({t["rung"] for t in group}),
        "rung_list": ", ".join(sorted({t["rung"] for t in group})),
        "trades": len(group),
        "first_utc": group[0]["executed_iso"],
        "last_utc": group[-1]["executed_iso"],
        "span_seconds": group[-1]["executed_ts"] - group[0]["executed_ts"],
        "day_central": central_day(group[0]["executed_ts"]),
        "contracts": sum(t["count"] for t in group),
        "taker_cost_usd": round(sum(t["taker_cost_usd"] or 0 for t in group), 2),
        "payout_if_all_settle_usd": round(sum(t["count"] for t in group), 2),
        "block_trades": sum(1 for t in group if t["is_block_trade"]),
        "all_counts_round_2500": all(is_round(t["count"]) for t in group),
        "tier_match_exact": exact,
        "tier_match_within_1pct": near,
        "markets": " | ".join(t["source_market_id"] for t in group),
        "raw_paths": " | ".join(sorted({t["raw_path"] for t in group})),
    }


def find_ladder_events(db, series_rung, tiers, window_s, min_contracts, min_cost):
    """Multi-rung clusters are findings; single-rung ones are leads."""
    rows = [r for r in fetch_milestone_trades(db, series_rung)
            if r["count"] >= min_contracts and (r["taker_cost_usd"] or 0) >= min_cost]
    events, leads = [], []
    for g in cluster(rows, window_s):
        (events if len({t["rung"] for t in g}) >= 2 else leads).append(summarise(g, tiers))
    events.sort(key=lambda e: -e["contracts"])
    leads.sort(key=lambda e: -e["contracts"])
    return events, leads


def block_trades(db) -> list[dict]:
    cur = db.execute("""
        SELECT t.source, t.source_market_id, m.series_id, m.team, m.title,
               t.executed_ts, t.executed_iso, t.count, t.price_dollars,
               t.taker_side, t.taker_cost_usd, t.raw_path
          FROM trade t
          JOIN market m ON m.source = t.source
                       AND m.source_market_id = t.source_market_id
         WHERE t.is_block_trade = 1
         ORDER BY t.executed_ts
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
    timestamp is not a key: one market in this archive has 24 distinct trades
    stamped to the same microsecond, because a single taker order swept a
    book at settlement, so joining a trade to its own market on the timestamp
    multiplies rows and attributes one trade's rank to another.
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
         ORDER BY r.taker_cost_usd DESC
    """, (min_contracts, min_cost))
    cols = [d[0] for d in cur.description]
    out = []
    for row in cur.fetchall():
        d = dict(zip(cols, row))
        d["executed_utc"] = utc_iso(d.pop("executed_iso"), d["executed_ts"])
        d["day_central"] = central_day(d.pop("executed_ts"))
        d["count_is_round_2500"] = is_round(d["count"])
        if d["market_trades"] >= MIN_TRADES_FOR_PERCENTILE:
            d["pct_of_market"] = round(
                100.0 * (d["market_trades"] - d["rank_in_market"] + 1) / d["market_trades"], 2)
        else:
            # Say so rather than print a number that only reflects thinness.
            d["pct_of_market"] = "market too thin"
        out.append(d)
    return out


def timeline_daily(db) -> list[dict]:
    """Daily notional by school, type and platform, dated in Central time.

    A 6:30pm Central kickoff is 23:30 UTC, so a UTC date cuts a game in half
    and puts the second half on the following day. The UTC date is carried
    alongside so a figure can be checked against a UTC-stamped source.
    """
    cur = db.execute("""
        WITH involved AS (
            SELECT source, source_market_id, market_type, team AS school
              FROM market WHERE team IS NOT NULL
            UNION
            SELECT source, source_market_id, market_type, away_team
              FROM market WHERE away_team IS NOT NULL
            UNION
            SELECT source, source_market_id, market_type, home_team
              FROM market WHERE home_team IS NOT NULL
        )
        SELECT i.school, i.market_type, t.source, t.executed_ts,
               t.count, COALESCE(t.taker_cost_usd, 0)
          FROM involved i
          JOIN trade t ON t.source = i.source
                      AND t.source_market_id = i.source_market_id
    """)
    agg: dict[tuple, dict] = {}
    for school, mtype, source, ts, count, cost in cur:
        key = (central_day(ts), school, mtype, source)
        a = agg.setdefault(key, {
            "day_central": key[0], "school": school, "market_type": mtype,
            "source": source, "trades": 0, "units": 0.0, "taker_cost_usd": 0.0,
            "utc_days": set(),
        })
        a["trades"] += 1
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
    return rows


def write_csv(rows: list[dict], out: Path, name: str, header: list[str] | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    cols = header or (list(rows[0]) if rows else [])
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
    ap.add_argument("--window-minutes", type=int, default=WINDOW_MINUTES)
    ap.add_argument("--min-contracts", type=int, default=MIN_CONTRACTS)
    ap.add_argument("--min-taker-cost", type=float, default=MIN_TAKER_COST)
    args = ap.parse_args(argv)

    if not args.db.exists():
        print(f"no database at {args.db}; run `python -m normalize.load` first",
              file=sys.stderr)
        return 1

    series_rung, _order, tiers = load_ladder(args.milestones)
    db = sqlite3.connect(args.db)

    blocks = block_trades(db)
    larges = large_trades(db, args.min_contracts, args.min_taker_cost)
    events, leads = find_ladder_events(
        db, series_rung, tiers, args.window_minutes * 60,
        args.min_contracts, args.min_taker_cost)
    daily = timeline_daily(db)

    write_csv(blocks, args.out, "block_trades")
    write_csv(larges, args.out, "large_trades")
    write_csv(events, args.out, "ladder_events")
    write_csv(leads, args.out, "ladder_leads")
    write_csv(daily, args.out, "timeline_daily")

    print("BLOCK TRADES (the exchange's own flag)")
    if not blocks:
        print("  none")
    for b in blocks:
        print("  %-22s %-28s %11s @ $%.2f  %-3s  %s" % (
            b["team"] or "-", b["source_market_id"][:28], f"{b['count']:,.0f}",
            b["price_dollars"], b["taker_side"], b["executed_utc"]))

    print("\nLADDER EVENTS (one school, one side, several rungs, one window)")
    if not events:
        print("  none at %d contracts / $%s / %d minutes"
              % (args.min_contracts, f"{args.min_taker_cost:,.0f}", args.window_minutes))
    for e in events:
        print("  %-6s %-4s %d rungs over %3ds: %11s contracts, taker paid %12s, "
              "settles for %12s" % (
                  e["school"], e["taker_side"], e["rungs"], e["span_seconds"],
                  f"{e['contracts']:,.0f}", f"${e['taker_cost_usd']:,.0f}",
                  f"${e['payout_if_all_settle_usd']:,.0f}"))
        print("         %s  (%s, %d flagged block)"
              % (e["rung_list"], e["day_central"], e["block_trades"]))
        if e["tier_match_exact"]:
            print("         exact bonus tiers: %s" % e["tier_match_exact"])
        if e["tier_match_within_1pct"]:
            print("         within 1%%: %s" % e["tier_match_within_1pct"])
    if not tiers:
        print("  No coach bonus tiers are on file, so no trade is matched to a"
              " contract figure.\n  config/milestones.yml says what a cited entry"
              " looks like.")

    print("\n%d single-rung leads, %d large trades, %d school-days"
          % (len(leads), len(larges), len(daily)))
    print("Wrote 5 CSVs to %s" % args.out)
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
