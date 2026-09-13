"""Polymarket serves no trade id, so the loader synthesises one.

The key has to do two opposite things: collapse the same fill when it comes
back in two overlapping fetches, and keep two identical fills that arrive in
one response. The first was broken -- the ordinal counter spanned the whole
load, so an overlap repeat was stored under a fresh key -- and had no test.
"""
import gzip
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from normalize import load

FILL = {"transactionHash": "0x796f2e616efb78a4d62ea6ffd18745c54799cd69",
        "conditionId": "0x77dfd142d449298d9969541da0ae69bca326e1c2008c9fb216a5ce83ca6baf39",
        "asset": "1", "side": "BUY", "size": "10", "price": "0.5",
        "timestamp": 1757000000, "proxyWallet": "0x50A0cebecFb81DbCbFA6d38F82040343F1E3D95F",
        "outcomeIndex": 0}


def write(path: Path, body, fetched_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump({"fetched_at": fetched_at, "source": "polymarket",
                   "endpoint": "trades", "key": "k", "url": "u", "status": 200,
                   "body": json.dumps(body)}, f)


def write_kalshi(path: Path, endpoint: str, body, fetched_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump({"fetched_at": fetched_at, "source": "kalshi", "endpoint": endpoint,
                   "key": "k", "url": "u", "status": 200, "body": json.dumps(body)}, f)


def load_polymarket(root: Path, monkeypatch) -> sqlite3.Connection:
    monkeypatch.setattr(load, "PROJECT_ROOT", root)
    db = sqlite3.connect(":memory:")
    db.executescript(load.SCHEMA.read_text())
    load.load_polymarket(db, root, Counter())
    return db


def test_the_same_fill_fetched_twice_lands_once(tmp_path, monkeypatch):
    """The watermark overlap and the inclusive `end=` re-anchor both serve
    the last fill again. It is one fill and must be one row."""
    d = tmp_path / "polymarket" / "2026-09-10"
    write(d / "trades_m1_p1_20260910000100000000Z.json.gz", [FILL],
          "2026-09-10T00:01:00.000000Z")
    write(d / "trades_m1_p1_20260910000200000000Z.json.gz", [FILL],
          "2026-09-10T00:02:00.000000Z")
    db = load_polymarket(tmp_path, monkeypatch)
    ids = [r[0] for r in db.execute("SELECT source_trade_id FROM trade")]
    assert len(ids) == 1, f"one fill in two fetches stored as {ids}"


def test_two_identical_fills_in_one_response_stay_distinct(tmp_path, monkeypatch):
    """One response carrying the same row twice is two fills (one transaction
    carried 18 in the sample), indistinguishable by design and both kept."""
    d = tmp_path / "polymarket" / "2026-09-10"
    write(d / "trades_m1_p1_20260910000100000000Z.json.gz", [FILL, dict(FILL)],
          "2026-09-10T00:01:00.000000Z")
    db = load_polymarket(tmp_path, monkeypatch)
    rows = db.execute("SELECT source_trade_id, id_is_synthetic FROM trade "
                      "ORDER BY source_trade_id").fetchall()
    assert len(rows) == 2
    assert rows[1][0] == rows[0][0] + "#2"
    assert all(synthetic == 1 for _, synthetic in rows)


KALSHI_MARKET = {
    "ticker": "KXNCAAFGAME-26SEP05OHIONEB-NEB", "title": "Ohio vs Nebraska",
    "yes_sub_title": "Nebraska", "event_ticker": "KXNCAAFGAME-26SEP05OHIONEB",
    "open_time": "2026-09-01T00:00:00Z", "close_time": "2026-09-05T23:00:00Z",
    "status": "active", "result": "",
}
KALSHI_TRADE = {
    "trade_id": "t1", "ticker": "KXNCAAFGAME-26SEP05OHIONEB-NEB",
    "created_time": "2026-09-05T20:00:00.123456Z", "yes_price_dollars": "0.60",
    "no_price_dollars": "0.40", "count_fp": "100", "taker_side": "yes",
    "is_block_trade": False,
}


def test_kalshi_end_to_end_market_and_trades_load(tmp_path, monkeypatch):
    """normalize.load's Kalshi path has the same envelope-to-row contract as
    the Polymarket path above but had no end-to-end test of its own."""
    monkeypatch.setattr(load, "PROJECT_ROOT", tmp_path)
    d = tmp_path / "kalshi" / "2026-09-05"
    write_kalshi(d / "market_KXNCAAFGAME-26SEP05OHIONEB-NEB_20260905200000000000Z.json.gz",
                "market", {"market": KALSHI_MARKET}, "2026-09-05T20:00:00.000000Z")
    write_kalshi(d / "trades_KXNCAAFGAME-26SEP05OHIONEB-NEB_p1_20260905200100000000Z.json.gz",
                "trades", {"trades": [KALSHI_TRADE]}, "2026-09-05T20:01:00.000000Z")

    db = sqlite3.connect(":memory:")
    db.executescript(load.SCHEMA.read_text())
    load.load_kalshi(db, tmp_path, Counter())

    m = db.execute("SELECT title, status, market_type FROM market "
                   "WHERE source='kalshi' AND source_market_id=?",
                   (KALSHI_MARKET["ticker"],)).fetchone()
    assert m == ("Ohio vs Nebraska", "active", "game_winner")

    t = db.execute("SELECT count, price_dollars, taker_side, cost_usd, taker_cost_usd, "
                   "payout_usd FROM trade WHERE source='kalshi' AND source_trade_id='t1'"
                   ).fetchone()
    # Yes taker at $0.60 for 100 contracts: cost and taker_cost agree (Yes
    # side paid what it's charged); payout is contracts x $1 face value.
    assert t == (100.0, 0.60, "yes", 60.0, 60.0, 100.0)


def test_kalshi_no_taker_side_uses_the_yes_price_for_cost(tmp_path, monkeypatch):
    """A No taker's cost is the No price, not the Yes price -- the exact
    13% understatement METHODOLOGY.md's 8 Sep 2026 note describes."""
    monkeypatch.setattr(load, "PROJECT_ROOT", tmp_path)
    d = tmp_path / "kalshi" / "2026-09-05"
    write_kalshi(d / "market_KXNCAAFGAME-26SEP05OHIONEB-NEB_20260905200000000000Z.json.gz",
                "market", {"market": KALSHI_MARKET}, "2026-09-05T20:00:00.000000Z")
    no_trade = dict(KALSHI_TRADE, trade_id="t2", taker_side="no")
    write_kalshi(d / "trades_KXNCAAFGAME-26SEP05OHIONEB-NEB_p1_20260905200100000000Z.json.gz",
                "trades", {"trades": [no_trade]}, "2026-09-05T20:01:00.000000Z")

    db = sqlite3.connect(":memory:")
    db.executescript(load.SCHEMA.read_text())
    load.load_kalshi(db, tmp_path, Counter())

    cost_usd, taker_cost_usd = db.execute(
        "SELECT cost_usd, taker_cost_usd FROM trade WHERE source_trade_id='t2'").fetchone()
    assert cost_usd == 60.0        # Yes price x count, regardless of side
    assert taker_cost_usd == 40.0  # what the No taker actually paid
