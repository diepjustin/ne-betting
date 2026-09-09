"""Tidy CSVs and a printed summary from the normalized store.

    uv run python -m analysis.breakdown [--db PATH] [--out DIR]

Writes one CSV per view into data/analysis/ and prints the same figures.

On units, because the two platforms do not measure the same thing: Kalshi
counts contracts, each settling at $1, and both sides of every trade. The
Polymarket figure is dollars paid by the taker of each fill. Columns are kept
separate for that reason and are never summed.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

from collectors.common import PROJECT_ROOT

VIEWS = {
    # what people are trading on
    "by_market_type": """
        SELECT m.source, m.market_type,
               COUNT(*)                AS trades,
               ROUND(SUM(t.count), 2)  AS units,
               ROUND(SUM(t.taker_cost_usd), 2) AS taker_cost_usd,
               MIN(t.executed_iso)     AS first_trade,
               MAX(t.executed_iso)     AS last_trade
        FROM trade t JOIN market m
          ON m.source = t.source AND m.source_market_id = t.source_market_id
        GROUP BY 1, 2 ORDER BY 4 DESC""",
    # per game
    "by_game": """
        SELECT m.game_id, m.source,
               COUNT(*) AS trades,
               ROUND(SUM(t.count), 2) AS units,
               ROUND(SUM(t.taker_cost_usd), 2) AS taker_cost_usd
        FROM trade t JOIN market m
          ON m.source = t.source AND m.source_market_id = t.source_market_id
        WHERE m.game_id IS NOT NULL
        GROUP BY 1, 2 ORDER BY 1""",
    # every market, so a single figure can be chased to its market
    "by_market": """
        SELECT m.source, m.source_market_id, m.market_type, m.game_id, m.player,
               m.title, m.status, m.settled_outcome,
               COUNT(t.source_trade_id) AS trades,
               ROUND(COALESCE(SUM(t.count), 0), 2) AS units,
               ROUND(COALESCE(SUM(t.taker_cost_usd), 0), 2) AS taker_cost_usd,
               m.raw_path
        FROM market m LEFT JOIN trade t
          ON m.source = t.source AND m.source_market_id = t.source_market_id
        GROUP BY 1, 2 ORDER BY 10 DESC""",
    # named players. See the note in the report below before using this.
    "player_props": """
        SELECT m.player, m.source, m.market_type,
               COUNT(DISTINCT m.source_market_id) AS markets_listed,
               COUNT(t.source_trade_id) AS trades,
               ROUND(COALESCE(SUM(t.count), 0), 2) AS units,
               ROUND(COALESCE(SUM(t.taker_cost_usd), 0), 2) AS taker_cost_usd
        FROM market m LEFT JOIN trade t
          ON m.source = t.source AND m.source_market_id = t.source_market_id
        WHERE m.market_type = 'player_prop' AND m.player IS NOT NULL
        GROUP BY 1, 2, 3 ORDER BY 6 DESC, 4 DESC""",
    # Every market involving a school, whether it names one team (a spread) or
    # two (a game). A school's row is the union, counted once per market.
    "by_school": """
        WITH involved AS (
            SELECT m.source, m.source_market_id, m.market_type, m.team AS school
              FROM market m WHERE m.team IS NOT NULL
            UNION
            SELECT m.source, m.source_market_id, m.market_type, m.away_team
              FROM market m WHERE m.away_team IS NOT NULL
            UNION
            SELECT m.source, m.source_market_id, m.market_type, m.home_team
              FROM market m WHERE m.home_team IS NOT NULL
        )
        SELECT i.school, i.source,
               COUNT(DISTINCT i.source_market_id)          AS markets,
               COUNT(t.source_trade_id)                    AS trades,
               ROUND(COALESCE(SUM(t.count), 0), 2)         AS units,
               ROUND(COALESCE(SUM(t.taker_cost_usd), 0), 2) AS taker_cost_usd,
               COUNT(DISTINCT CASE WHEN i.market_type = 'player_prop'
                                   THEN i.source_market_id END) AS athlete_markets
        FROM involved i
        LEFT JOIN trade t
          ON t.source = i.source AND t.source_market_id = i.source_market_id
        GROUP BY 1, 2 ORDER BY 6 DESC""",
    "daily_volume": """
        SELECT DATE(t.executed_ts, 'unixepoch') AS day, t.source,
               COUNT(*) AS trades,
               ROUND(SUM(t.count), 2) AS units,
               ROUND(SUM(t.taker_cost_usd), 2) AS taker_cost_usd
        FROM trade t GROUP BY 1, 2 ORDER BY 1""",
}


def dump(db: sqlite3.Connection, name: str, sql: str, out: Path) -> list:
    cur = db.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    out.mkdir(parents=True, exist_ok=True)
    with open(out / f"{name}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "husker.db")
    ap.add_argument("--out", type=Path, default=PROJECT_ROOT / "data" / "analysis")
    args = ap.parse_args(argv)
    if not args.db.exists():
        print(f"no database at {args.db}; run `python -m normalize.load` first", file=sys.stderr)
        return 1
    db = sqlite3.connect(args.db)
    results = {n: dump(db, n, sql, args.out) for n, sql in VIEWS.items()}

    print("WHAT THE TRADES ARE ON")
    print("  %-11s %-17s %8s %14s %14s" % ("platform", "market type", "trades", "units", "taker cost"))
    for src, mt, n, units, cost, *_ in results["by_market_type"]:
        print("  %-11s %-17s %8d %14s %14s" % (src, mt, n, f"{units:,.0f}", f"${cost:,.0f}"))

    print("\nPLAYER PROPS, BY NAMED PLAYER")
    pp = results["player_props"]
    if not pp:
        print("  none listed")
    traded = [r for r in pp if r[4]]
    print("  %d players have markets listed; %d have any trade at all"
          % (len({r[0] for r in pp}), len({r[0] for r in traded})))
    print("  %-26s %-11s %8s %10s %12s" % ("player", "platform", "markets", "trades", "units"))
    for player, src, _mt, mkts, n, units, cost in pp[:25]:
        print("  %-26s %-11s %8d %10d %12s" % (player[:26], src, mkts, n, f"{units:,.0f}"))
    print("  Aggregate across every named player: %d trades, %s units."
          % (sum(r[4] for r in pp), f"{sum(r[5] for r in pp):,.0f}"))
    print("  Note: plan section 9 keeps published figures aggregate. This view names\n"
          "  players for reporting; it is not for publication without the editor.")

    print("\nWrote %d CSVs to %s" % (len(results), args.out))
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
