"""The bonus-hedging detector, against the trades it was built to find.

The four trades below are real. They were pulled from Kalshi's public trade
API on 8 Sep 2026 and sit in this project's raw archive; the counts, prices
and timestamps here are copied from those responses to the microsecond. They
are the trades CBS and InGame reported as a hedge of LSU's coaching-contract
bonuses. If a change to this module stops finding them, that is a regression
in the only case we can check against the outside world.
"""
import datetime as dt
import sqlite3

import pytest

from analysis import anomalies as a
from collectors.common import PROJECT_ROOT

# ticker, series, ISO timestamp, contracts, yes price, taker side, block flag
LSU_TRADES = [
    ("KXNCAAFPLAYOFF-26-LSU", "KXNCAAFPLAYOFF", "2026-08-13T19:05:35.120042Z", 837_500, 0.41, "yes", 1),
    ("KXNCAAFSF-27-LSU",      "KXNCAAFSF",      "2026-08-13T19:06:05.418402Z", 470_000, 0.18, "yes", 1),
    ("KXNCAAF-27-LSU",        "KXNCAAF",        "2026-08-13T19:06:28.362687Z", 787_500, 0.08, "yes", 1),
    ("KXNCAAFQF-27-LSU",      "KXNCAAFQF",      "2026-08-13T19:06:47.395669Z", 367_500, 0.29, "yes", 1),
]

# Noise that must not be mistaken for a hedge. Every one of these is a real
# shape from the archive: a penny-priced sweep of a decided market, which is
# huge in contracts and trivial in dollars; a pair of big trades on one rung,
# which is a lead and not a finding; and a big single-game trade, which is a
# bet on a Saturday and not a contract milestone.
NOISE = [
    ("KXNCAAFGAME-26SEP05NWSTLT-NWST", "KXNCAAFGAME", "2026-09-05T23:40:03.611005Z", 400_139, 0.01, "yes", 0, "NWST"),
    ("KXNCAAF-27-ORE",                 "KXNCAAF",     "2026-08-27T21:43:24.727168Z", 218_767, 0.11, "yes", 0, "ORE"),
    ("KXNCAAF-27-ORE",                 "KXNCAAF",     "2026-08-27T21:43:12.964627Z", 200_000, 0.11, "yes", 0, "ORE"),
    ("KXNCAAFSPREAD-26SEP04MIASTAN-MIA25", "KXNCAAFSPREAD", "2026-09-05T01:12:02.667792Z", 500_000, 0.57, "yes", 0, "MIA"),
]


def ts(iso: str) -> int:
    return int(dt.datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
               .replace(tzinfo=dt.timezone.utc).timestamp())


@pytest.fixture
def db():
    con = sqlite3.connect(":memory:")
    con.executescript((PROJECT_ROOT / "normalize" / "schema.sql").read_text())
    rows = [(t[0], t[1], t[2], t[3], t[4], t[5], t[6], "LSU") for t in LSU_TRADES] + NOISE
    for i, (ticker, series, iso, count, price, side, block, team) in enumerate(rows):
        con.execute(
            "INSERT OR IGNORE INTO market (source, source_market_id, title, series_id,"
            " team, first_seen_at, raw_path) VALUES ('kalshi', ?, ?, ?, ?, 0, 'x')",
            (ticker, f"{team} market", series, team))
        con.execute(
            "INSERT INTO trade (source, source_trade_id, source_market_id, executed_ts,"
            " executed_iso, price_dollars, count, taker_side, taker_cost_usd,"
            " is_block_trade, fetched_at, raw_path)"
            " VALUES ('kalshi', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'x')",
            (f"t{i}", ticker, ts(iso), iso, price, count, side,
             count * (price if side == "yes" else 1 - price), block))
    con.commit()
    yield con
    con.close()


@pytest.fixture
def ladder():
    return a.load_ladder(PROJECT_ROOT / "config" / "milestones.yml")


def test_finds_the_lsu_hedge_and_nothing_else(db, ladder):
    series_rung, _order, tiers = ladder
    events, _leads = a.find_ladder_events(
        db, series_rung, tiers, a.WINDOW_MINUTES * 60,
        a.MIN_CONTRACTS, a.MIN_TAKER_COST)
    assert len(events) == 1, [e["school"] for e in events]
    e = events[0]
    assert e["school"] == "LSU"
    assert e["taker_side"] == "yes"
    assert e["rungs"] == 4
    assert e["trades"] == 4
    assert e["first_utc"] == "2026-08-13T19:05:35.120042Z"
    assert e["last_utc"] == "2026-08-13T19:06:47.395669Z"
    assert e["span_seconds"] == 72
    assert e["contracts"] == 2_462_500
    assert e["block_trades"] == 4


def test_the_sum_is_the_four_rungs_we_have_not_the_reported_three_million(db, ladder):
    """$2,462,500, not $3,000,000.

    The reported total includes a fifth rung in KXNCAAFFINALIST, a series that
    was missing from config/targets.yml until 9 Sep 2026 and so was never
    collected. When a pass that includes it is loaded, this figure moves and
    this test should be updated against the new archive -- not before.
    """
    series_rung, _order, tiers = ladder
    events, _ = a.find_ladder_events(db, series_rung, tiers, 900,
                                     a.MIN_CONTRACTS, a.MIN_TAKER_COST)
    assert events[0]["payout_if_all_settle_usd"] == 2_462_500
    assert events[0]["taker_cost_usd"] == 597_550.0
    assert "title_game" not in events[0]["rung_list"]


def test_single_rung_repeat_is_a_lead_not_a_finding(db, ladder):
    """Two big Oregon title trades 12 seconds apart are one rung, so a lead."""
    series_rung, _order, tiers = ladder
    events, leads = a.find_ladder_events(db, series_rung, tiers, 900,
                                         a.MIN_CONTRACTS, a.MIN_TAKER_COST)
    assert all(e["school"] != "ORE" for e in events)
    ore = [l for l in leads if l["school"] == "ORE"]
    assert len(ore) == 1 and ore[0]["trades"] == 2


def test_penny_sweep_is_huge_in_contracts_and_still_excluded(db, ladder):
    """400,139 contracts at a cent is $4,001. A contract floor alone lets it in."""
    series_rung, _order, tiers = ladder
    _, leads = a.find_ladder_events(db, series_rung, tiers, 900,
                                    a.MIN_CONTRACTS, a.MIN_TAKER_COST)
    assert all(l["school"] != "NWST" for l in leads)


def test_game_markets_are_not_ladder_rungs(db, ladder):
    """A Saturday spread is not a contract milestone, however big."""
    series_rung, _order, _ = ladder
    assert "KXNCAAFSPREAD" not in series_rung
    assert "KXNCAAFGAME" not in series_rung


def test_clusters_split_on_a_gap_not_a_clock_bucket():
    """Two trades either side of :15 are one cluster; a long gap is two."""
    def t(sec, series):
        return {"team": "X", "taker_side": "yes", "executed_ts": sec,
                "series_id": series, "rung": series}
    straddling = [t(880, "a"), t(920, "b")]      # 14:40 and 15:20 past the hour
    assert len(a.cluster(straddling, 900)) == 1
    apart = [t(0, "a"), t(1000, "b")]
    assert len(a.cluster(apart, 900)) == 2


def test_ladder_series_are_all_collected():
    """A rung nobody collects is a rung the detector is blind to."""
    from collectors.common import load_yaml
    collected = set(load_yaml(PROJECT_ROOT / "config" / "targets.yml")["kalshi"]["series"])
    series_rung, _, _ = a.load_ladder(PROJECT_ROOT / "config" / "milestones.yml")
    assert not set(series_rung) - collected


def test_no_bonus_tier_is_matched_without_a_cited_document(ladder):
    """Plan section 7. An invented tier is a fabricated figure in a story."""
    _, _, tiers = ladder
    for school, entry in tiers.items():
        assert entry.get("source"), f"{school} has tiers with no cited source"


def test_tier_match_reports_exact_and_near_separately():
    group = [{"rung": "playoff_berth", "count": 837_500},
             {"rung": "semifinal", "count": 469_000}]
    tiers = {"LSU": {"source": "hypothetical, for this test only",
                     "tiers": {"playoff_berth": 837_500, "semifinal": 470_000}}}
    exact, near = a.match_tiers("LSU", group, tiers)
    assert exact == "playoff_berth=837,500"
    assert near.startswith("semifinal~469,000/470,000")


def test_central_time_moves_a_late_kickoff_back_a_day():
    """23:30 UTC on the 6th is 6:30pm Central on the 5th."""
    assert a.central_day(ts("2026-09-06T23:30:00Z")) == "2026-09-06"
    assert a.central_day(ts("2026-09-06T01:30:00Z")) == "2026-09-05"
