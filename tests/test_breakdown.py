"""daily_volume must bucket by the same Central day analysis/anomalies.py
uses, not by a UTC date -- a UTC bucket cuts a late-Saturday-night trade
across two dates."""
import datetime as dt
import sqlite3
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))
from analysis.breakdown import daily_volume
from normalize.load import SCHEMA

CENTRAL = ZoneInfo("America/Chicago")


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA.read_text())
    return db


def _insert(db, trade_id: str, source: str, ts: int, count: float, cost: float) -> None:
    db.execute("""INSERT INTO trade VALUES
        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (source, trade_id, "m1", ts, str(ts), 0.5, None, count, "yes", None,
         cost, cost, count, 0, 0, ts, "raw/x"))


def test_a_late_utc_trade_lands_on_the_previous_central_day():
    """2026-09-06T02:30:00Z is Saturday night, 6 Sep 2026 21:30 Central --
    still the 5th in Central time. A UTC bucket would call this the 6th."""
    ts = int(dt.datetime(2026, 9, 6, 2, 30, tzinfo=dt.timezone.utc).timestamp())
    assert dt.datetime.fromtimestamp(ts, CENTRAL).strftime("%Y-%m-%d") == "2026-09-05"
    db = _db()
    _insert(db, "t1", "kalshi", ts, 100, 50.0)
    rows = daily_volume(db)
    assert rows == [("2026-09-05", "kalshi", 1, 100.0, 50.0)]


def test_two_platforms_on_the_same_day_are_separate_rows():
    ts = int(dt.datetime(2026, 9, 6, 18, 0, tzinfo=dt.timezone.utc).timestamp())
    db = _db()
    _insert(db, "t1", "kalshi", ts, 10, 5.0)
    _insert(db, "t2", "polymarket", ts, 20, 12.0)
    rows = daily_volume(db)
    assert ("2026-09-06", "kalshi", 1, 10.0, 5.0) in rows
    assert ("2026-09-06", "polymarket", 1, 20.0, 12.0) in rows
    assert len(rows) == 2


def test_trades_on_one_day_sum_into_one_row():
    d = dt.datetime(2026, 9, 6, 18, 0, tzinfo=dt.timezone.utc)
    db = _db()
    _insert(db, "t1", "kalshi", int(d.timestamp()), 10, 5.0)
    _insert(db, "t2", "kalshi", int((d + dt.timedelta(minutes=5)).timestamp()), 15, 7.5)
    rows = daily_volume(db)
    assert rows == [("2026-09-06", "kalshi", 2, 25.0, 12.5)]
