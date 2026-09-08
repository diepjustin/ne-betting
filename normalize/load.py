"""Build the normalized SQLite store from the raw archive.

    uv run python -m normalize.load [--data-dir DIR] [--db PATH]

Raw is never modified. The database is derived and can be deleted and rebuilt
at any time. Re-running is idempotent: rows key on (source, id), so the same
trade seen in two overlapping fetches lands once.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from collectors.common import PROJECT_ROOT, parse_rfc3339
from .resolve import resolve_kalshi, resolve_polymarket

SCHEMA = Path(__file__).with_name("schema.sql")


SKIPPED: Counter = Counter()


def envelopes(root: Path, source: str, prefix: str):
    """Yield archived responses, skipping any file that cannot be read.

    A collector writing into the same archive right now has one file
    half-written at any moment. Skipping it is correct: the run that wrote it
    is still going and the next load picks it up. Skips are counted and
    reported rather than swallowed.
    """
    for p in sorted((root / source).rglob(f"{prefix}_*.json.gz")):
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                env = json.load(f)
            body = json.loads(env["body"])
        except (OSError, EOFError, json.JSONDecodeError, KeyError):
            SKIPPED[source] += 1
            continue
        yield p, env, body


def _ts(iso: str) -> int:
    return int(parse_rfc3339(iso).timestamp())


def load_markets_from_discovery(db, root: Path, stats: Counter,
                                sources=("kalshi", "polymarket")) -> None:
    """Markets as they appear in the archived discovery pages.

    A market that has never traded gets no per-market fetch, so the listing
    page is the only record that it existed. Since the whole point of the
    player-prop finding is markets that are listed and not traded, they have to
    come from here. Per-market files load afterwards and win on the fields
    they refresh.
    """
    for p, env, body in (envelopes(root, "kalshi", "events") if "kalshi" in sources else ()):
        fetched = _ts(env["fetched_at"])
        rel = str(p.relative_to(PROJECT_ROOT))
        for e in (body.get("events") or []):
            for m in (e.get("markets") or []):
                r = resolve_kalshi(m)
                db.execute("""INSERT OR IGNORE INTO market VALUES
                              (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                           ("kalshi", m["ticker"], m.get("title"), m.get("yes_sub_title"),
                            m["ticker"].split("-")[0], e.get("event_ticker"),
                            _ts(m["open_time"]) if m.get("open_time") else None,
                            _ts(m["close_time"]) if m.get("close_time") else None,
                            m.get("status"), m.get("result"),
                            r.game_id, r.market_type, r.team, r.player, r.line,
                            fetched, rel))
                stats["markets_from_discovery"] += 1

    for p, env, body in (envelopes(root, "polymarket", "gamma_events")
                         if "polymarket" in sources else ()):
        fetched = _ts(env["fetched_at"])
        rel = str(p.relative_to(PROJECT_ROOT))
        events = body if isinstance(body, list) else (body.get("events") or [])
        for e in events:
            slug = e.get("slug") or ""
            for m in (e.get("markets") or []):
                cid = m.get("conditionId")
                if not cid:
                    continue
                r = resolve_polymarket(m, slug)
                db.execute("""INSERT OR IGNORE INTO market VALUES
                              (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                           ("polymarket", cid, m.get("question"), m.get("groupItemTitle"),
                            m.get("sportsMarketType"), str(e.get("id") or ""),
                            None, None,
                            "closed" if m.get("closed") else "open", None,
                            r.game_id, r.market_type, r.team, r.player, r.line,
                            fetched, rel))
                stats["markets_from_discovery"] += 1


def load_kalshi(db, root: Path, stats: Counter) -> None:
    for prefix, hist in (("market", 0), ("historical_market", 1)):
        for p, env, body in envelopes(root, "kalshi", prefix):
            m = body.get("market") if isinstance(body, dict) else None
            if not m:
                continue
            r = resolve_kalshi(m)
            stats[f"kalshi_market_{r.market_type}"] += 1
            if r.reason:
                db.execute("INSERT INTO unresolved VALUES (?,?,?,?,?)",
                           ("kalshi", m["ticker"], m.get("title"), r.reason, _ts(env["fetched_at"])))
                stats["unresolved"] += 1
            db.execute("""INSERT INTO market VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                          ON CONFLICT(source, source_market_id) DO UPDATE SET
                            status=excluded.status, settled_outcome=excluded.settled_outcome,
                            close_ts=excluded.close_ts""",
                       ("kalshi", m["ticker"], m.get("title"), m.get("yes_sub_title"),
                        m["ticker"].split("-")[0], m.get("event_ticker"),
                        _ts(m["open_time"]) if m.get("open_time") else None,
                        _ts(m["close_time"]) if m.get("close_time") else None,
                        m.get("status"), m.get("result"),
                        r.game_id, r.market_type, r.team, r.player, r.line,
                        _ts(env["fetched_at"]), str(p.relative_to(PROJECT_ROOT))))
            stats["markets"] += 1

    for prefix in ("trades", "historical_trades"):
        for p, env, body in envelopes(root, "kalshi", prefix):
            fetched = _ts(env["fetched_at"])
            rel = str(p.relative_to(PROJECT_ROOT))
            for t in (body.get("trades") or []):
                count = float(t["count_fp"])
                price = float(t["yes_price_dollars"])
                db.execute("""INSERT OR IGNORE INTO trade VALUES
                              (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                           ("kalshi", t["trade_id"], t["ticker"],
                            _ts(t["created_time"]), t["created_time"],
                            price, count, t.get("taker_side"), None,
                            price * count, count, int(bool(t.get("is_block_trade"))),
                            0, fetched, rel))
                stats["trades"] += 1


def _pm_trade_id(t: dict, seen: Counter) -> tuple[str, int]:
    """Polymarket serves no trade id and transaction hashes are not unique
    (one carried 18 fills), so the key is a hash of the row. Exact repeats get
    an ordinal; two identical fills are genuinely indistinguishable here."""
    canon = json.dumps([t.get(k) for k in
                        ("transactionHash", "conditionId", "asset", "side",
                         "size", "price", "timestamp", "proxyWallet", "outcomeIndex")],
                       sort_keys=True)
    h = hashlib.sha1(canon.encode()).hexdigest()[:20]
    seen[h] += 1
    n = seen[h]
    return (h if n == 1 else f"{h}#{n}"), 1


def polymarket_event_slugs(root: Path) -> dict[str, str]:
    """condition id -> parent event slug.

    A gamma /markets response carries no `events` key, and a spread market's
    own slug is not the game's, so the game a market belongs to is only
    recoverable from the event listings already in the archive.
    """
    out: dict[str, str] = {}
    for _p, _env, body in envelopes(root, "polymarket", "gamma_events"):
        events = body if isinstance(body, list) else (body.get("events") or [])
        for e in events:
            slug = e.get("slug") or ""
            for m in (e.get("markets") or []):
                cid = m.get("conditionId")
                if cid and slug:
                    out.setdefault(cid, slug)
    return out


def load_polymarket(db, root: Path, stats: Counter) -> None:
    slug_of = polymarket_event_slugs(root)
    for p, env, body in envelopes(root, "polymarket", "gamma_market"):
        if not isinstance(body, dict) or not body.get("conditionId"):
            continue
        cid = body["conditionId"]
        ev = (body.get("events") or [{}])
        event_slug = slug_of.get(cid) or body.get("slug") or ""
        r = resolve_polymarket(body, event_slug)
        stats[f"polymarket_market_{r.market_type}"] += 1
        if r.reason:
            db.execute("INSERT INTO unresolved VALUES (?,?,?,?,?)",
                       ("polymarket", cid, body.get("question"), r.reason, _ts(env["fetched_at"])))
            stats["unresolved"] += 1
        db.execute("""INSERT INTO market VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(source, source_market_id) DO UPDATE SET
                        status=excluded.status, close_ts=excluded.close_ts""",
                   ("polymarket", cid, body.get("question"), body.get("groupItemTitle"),
                    body.get("sportsMarketType"), str((ev[0].get("id") if isinstance(ev, list) and ev else "") or ""),
                    None, None,
                    "closed" if body.get("closed") else "open", body.get("umaResolutionStatus"),
                    r.game_id, r.market_type, r.team, r.player, r.line,
                    _ts(env["fetched_at"]), str(p.relative_to(PROJECT_ROOT))))
        stats["markets"] += 1

    seen: Counter = Counter()
    for p, env, body in envelopes(root, "polymarket", "trades"):
        if not isinstance(body, list):
            continue
        fetched = _ts(env["fetched_at"])
        rel = str(p.relative_to(PROJECT_ROOT))
        for t in body:
            tid, synth = _pm_trade_id(t, seen)
            size, price = float(t["size"]), float(t["price"])
            db.execute("""INSERT OR IGNORE INTO trade VALUES
                          (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       ("polymarket", tid, t.get("conditionId"),
                        int(t["timestamp"]), str(t["timestamp"]),
                        price, size, t.get("side"), t.get("proxyWallet"),
                        price * size, size, 0, synth, fetched, rel))
            stats["trades"] += 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--source", choices=("kalshi", "polymarket", "all"), default="all",
                    help="load one platform only, replacing just its rows. Use this "
                         "when the other platform's collector is still running.")
    args = ap.parse_args(argv)
    db_path = args.db or (args.data_dir / "husker.db")
    raw = args.data_dir / "raw"
    sources = ("kalshi", "polymarket") if args.source == "all" else (args.source,)
    if args.source == "all" and db_path.exists():
        db_path.unlink()          # derived; rebuilt from raw every time
    db = sqlite3.connect(db_path)
    db.executescript(SCHEMA.read_text())
    if args.source != "all":
        # Replace only this platform's rows, so a load can run while the other
        # platform's collector is still going.
        for table in ("market", "trade", "unresolved"):
            db.execute(f"DELETE FROM {table} WHERE source = ?", (args.source,))
    stats: Counter = Counter()
    load_markets_from_discovery(db, raw, stats, sources)
    if "kalshi" in sources:
        load_kalshi(db, raw, stats)
    if "polymarket" in sources:
        load_polymarket(db, raw, stats)
    db.commit()

    # Scoped to the sources just loaded. Counting every row in the table and
    # subtracting one platform's reads produced a negative "duplicates
    # collapsed" figure, which is worse than no figure at all.
    marks = ",".join("?" * len(sources))
    rows = db.execute(f"SELECT COUNT(*) FROM trade WHERE source IN ({marks})",
                      sources).fetchone()[0]
    mkts = db.execute(f"SELECT COUNT(*) FROM market WHERE source IN ({marks})",
                      sources).fetchone()[0]
    unres = db.execute("SELECT COUNT(*) FROM unresolved").fetchone()[0]
    nogame = db.execute(
        "SELECT COUNT(*) FROM market WHERE game_id IS NULL AND market_type IN "
        "('game_winner','spread','total','team_stat')").fetchone()[0]
    print(json.dumps({
        "markets": mkts,
        "trades": rows,
        "market_rows_read": stats["markets"],
        "market_rows_from_discovery_pages": stats["markets_from_discovery"],
        "trade_rows_read": stats["trades"],
        "duplicate_trade_rows_collapsed": stats["trades"] - rows,
        "unresolved_markets": unres,
        "game_markets_without_a_game_id": nogame,
        "sources_loaded": list(sources),
        "unreadable_files_skipped": dict(SKIPPED),
        "database": str(db_path),
    }, indent=1))
    if unres:
        print(f"\n{unres} market(s) could not be mapped. They are in the "
              f"`unresolved` table with their titles, not dropped.", file=sys.stderr)
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
