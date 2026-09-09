"""Re-derive the hedging detector's thresholds, and show the working.

    uv run python -m analysis.calibrate [--db PATH] [--reference LSU:2026-08-13]

The floors in `analysis/anomalies.py` are settings, not findings. They were
first chosen on an archive holding six of the ladder's series, before the wide
pass, and METHODOLOGY marks them provisional for that reason. This re-derives
them from whatever archive is loaded and prints a table that can be read into
that file, so the numbers in the prose and the numbers in the code come from
the same place.

**This is a sensitivity analysis, not a calibration.** There is exactly one
confirmed hedge in the record -- LSU, 13 Aug 2026, reported by CBS and InGame
and matched trade for trade in our archive. One true positive cannot support a
false-negative rate, an ROC curve, or a claim that a threshold is optimal. All
it supports is: which settings still find the case we can check, and how much
else each setting drags in. A threshold that finds LSU and 400 other things is
worse than one that finds LSU and two, and that is the whole of the reasoning.

Coverage is printed first and on purpose. A floor tuned on an archive with no
SEC or ACC title market is a floor tuned without the commonest coach bonus
rung, and the coverage table is what says whether that is still true.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

from collectors.common import PROJECT_ROOT
from analysis.anomalies import (
    NEAR_CERTAIN_PRICE,
    central_day,
    clears_floor,
    cluster,
    fetch_milestone_orders,
    load_ladder,
    summarise,
)

CONTRACT_FLOORS = (1_000, 2_500, 5_000, 10_000, 25_000, 50_000)
COST_FLOORS = (0.0, 1_000.0, 5_000.0, 25_000.0)
WINDOWS_MIN = (5, 15, 60, 1_440)


def coverage(db: sqlite3.Connection, series_rung: dict, order: list[str]) -> list[dict]:
    """Which rungs the archive can actually see."""
    rows = []
    for rung in order:
        series = sorted(s for s, r in series_rung.items() if r == rung)
        marks = ",".join("?" * len(series))
        mkts, traded = db.execute(f"""
            SELECT COUNT(*),
                   SUM(CASE WHEN EXISTS (SELECT 1 FROM trade t
                        WHERE t.source='kalshi'
                          AND t.source_market_id = m.source_market_id)
                       THEN 1 ELSE 0 END)
              FROM market m
             WHERE m.source='kalshi' AND m.series_id IN ({marks})
        """, series).fetchone()
        present = db.execute(f"""
            SELECT COUNT(DISTINCT series_id) FROM market
             WHERE source='kalshi' AND series_id IN ({marks})
        """, series).fetchone()[0]
        rows.append({
            "rung": rung, "series_configured": len(series),
            "series_with_markets": present, "markets": mkts or 0,
            "markets_with_trades": traded or 0,
        })
    return rows


def sweep(orders, tiers, order_labels, reference) -> list[dict]:
    """One row per (window, contract floor, cost floor) setting."""
    out = []
    for window_min in WINDOWS_MIN:
        for mc in CONTRACT_FLOORS:
            for cost in COST_FLOORS:
                kept = [o for o in orders if clears_floor(o, mc, cost)]
                events, leads = [], []
                for g in cluster(kept, window_min * 60):
                    s = summarise(g, tiers, order_labels)
                    (leads if s["not_a_finding_because"] else events).append(s)
                hit = [e for e in events
                       if (e["school"], e["day_central"]) == reference]
                out.append({
                    "window_minutes": window_min,
                    "min_contracts": mc,
                    "min_taker_cost": cost,
                    "orders_kept": len(kept),
                    "events": len(events),
                    "leads": len(leads),
                    "finds_reference": bool(hit),
                    "other_events": len(events) - len(hit),
                    "other_schools": ", ".join(sorted({
                        e["school"] for e in events
                        if (e["school"], e["day_central"]) != reference})),
                })
    return out


def price_profile(orders) -> list[dict]:
    """What the takers on ladder markets are actually paying.

    The grounds for setting a near-certainty cut, kept as a table rather than
    a remembered number so the cut can be re-argued against the archive.
    """
    bands = [(0.0, 0.05), (0.05, 0.25), (0.25, 0.75), (0.75, 0.95), (0.95, 1.01)]
    rows = []
    for lo, hi in bands:
        sel = [o for o in orders if lo <= o["taker_price"] < hi]
        rows.append({
            "taker_price_band": f"${lo:.2f}-${hi:.2f}",
            "orders": len(sel),
            "yes_orders": sum(1 for o in sel if o["taker_side"] == "yes"),
            "no_orders": sum(1 for o in sel if o["taker_side"] == "no"),
            "contracts": round(sum(o["contracts"] for o in sel), 2),
            "taker_cost_usd": round(sum(o["taker_cost_usd"] for o in sel), 2),
        })
    return rows


def write_csv(rows, out: Path, name: str) -> None:
    if not rows:
        return
    out.mkdir(parents=True, exist_ok=True)
    with open(out / f"{name}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "husker.db")
    ap.add_argument("--out", type=Path, default=PROJECT_ROOT / "data" / "analysis")
    ap.add_argument("--milestones", type=Path,
                    default=PROJECT_ROOT / "config" / "milestones.yml")
    ap.add_argument("--reference", default="LSU:2026-08-13",
                    help="the one confirmed hedge, as SCHOOL:YYYY-MM-DD Central")
    args = ap.parse_args(argv)
    if not args.db.exists():
        print(f"no database at {args.db}", file=sys.stderr)
        return 1

    school, _, day = args.reference.partition(":")
    reference = (school, day)
    series_rung, order_labels, tiers = load_ladder(args.milestones)
    db = sqlite3.connect(args.db)
    orders, orphans = fetch_milestone_orders(db, series_rung)

    cov = coverage(db, series_rung, order_labels)
    prices = price_profile(orders)
    grid = sweep(orders, tiers, order_labels, reference)
    write_csv(cov, args.out, "calibration_coverage")
    write_csv(prices, args.out, "calibration_prices")
    write_csv(grid, args.out, "calibration_sweep")

    print("LADDER COVERAGE -- a rung with no markets is a rung nothing can be tuned on")
    print("  %-20s %10s %10s %10s %10s" % ("rung", "series", "with mkts", "markets", "traded"))
    for c in cov:
        print("  %-20s %10d %10d %10d %10d" % (
            c["rung"], c["series_configured"], c["series_with_markets"],
            c["markets"], c["markets_with_trades"]))
    blind = [c["rung"] for c in cov if not c["markets_with_trades"]]
    if blind:
        print("  No traded market on: %s." % ", ".join(blind))
        print("  Thresholds below are tuned without %s." % ("that rung" if len(blind) == 1
                                                            else "those rungs"))
    if orphans:
        print("  %d ladder markets have trades but no school resolved." % orphans)

    print("\nWHAT LADDER TAKERS PAY  (%d orders)" % len(orders))
    print("  %-16s %8s %8s %8s %16s" % ("price band", "orders", "yes", "no", "taker cost"))
    for p in prices:
        print("  %-16s %8d %8d %8d %16s" % (
            p["taker_price_band"], p["orders"], p["yes_orders"], p["no_orders"],
            f"${p['taker_cost_usd']:,.0f}"))
    dear = [p for p in prices if p["taker_price_band"].startswith("$0.95")][0]
    print("  Orders at or above $%.2f a contract: %d, of which %d are No takers."
          % (NEAR_CERTAIN_PRICE, dear["orders"], dear["no_orders"]))

    print("\nSENSITIVITY  (one confirmed hedge, %s on %s -- see the module docstring"
          "\n              on why this is not a calibration)" % (school, day))
    print("  %7s %10s %10s %8s %8s %8s" % (
        "window", "contracts", "cost", "kept", "events", "finds it"))
    for row in grid:
        if not row["finds_reference"] and row["events"] == 0:
            continue  # a setting that finds nothing at all says nothing
        print("  %5dm %10d %10s %8d %8d %8s" % (
            row["window_minutes"], row["min_contracts"],
            f"${row['min_taker_cost']:,.0f}", row["orders_kept"],
            row["events"], "yes" if row["finds_reference"] else "NO"))

    keeps = [r for r in grid if r["finds_reference"]]
    if not keeps:
        print("\n  NOTHING FINDS THE REFERENCE CASE. Either the archive no longer"
              "\n  holds it or the detector has regressed; do not re-tune on this.")
        db.close()
        return 1
    loosest = max(keeps, key=lambda r: (-r["other_events"], -r["min_contracts"],
                                        -r["min_taker_cost"]))
    print("\n  Quietest setting that still finds it: %d-minute gap, %d contracts,"
          " $%s\n  -- %d event(s), %d of them other than the reference."
          % (loosest["window_minutes"], loosest["min_contracts"],
             f"{loosest['min_taker_cost']:,.0f}", loosest["events"],
             loosest["other_events"]))
    print("  Quietest is not best. A floor this tight also drops any hedge"
          "\n  smaller than the one case we can check. Read the table, then choose.")
    print("\nWrote 3 CSVs to %s" % args.out)
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
