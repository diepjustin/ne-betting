"""The threshold sweep, on data whose answer is known by construction."""
import pytest

from analysis import calibrate as c
from analysis import anomalies as a
from collectors.common import PROJECT_ROOT
from tests.test_anomalies import ts


@pytest.fixture
def ladder():
    return c.load_ladder(PROJECT_ROOT / "config" / "milestones.yml")


def order(team, rung_series, iso, contracts, price, side="yes", block=0):
    return {
        "source_market_id": f"{rung_series}-26-{team}", "series_id": rung_series,
        "team": team, "title": "", "executed_ts": ts(iso), "executed_iso": iso,
        "taker_side": side, "fills": 1, "contracts": contracts,
        "taker_cost_usd": contracts * price, "has_block": block,
        "raw_path": "x", "rung": None, "taker_price": price,
    }


def with_rungs(orders, series_rung):
    for o in orders:
        o["rung"] = series_rung[o["series_id"]]
    return orders


def test_a_floor_above_the_reference_stops_finding_it(ladder):
    """The sweep's only real job: say which settings still find the case."""
    series_rung, labels, tiers = ladder
    # A big rung and a small one. A floor above the small rung leaves a single
    # rung, which is a lead and not an event, so the whole ladder disappears
    # from the findings without anything looking like it failed.
    orders = with_rungs([
        order("LSU", "KXNCAAFPLAYOFF", "2026-08-13T19:05:35.120042Z", 837_500, 0.41),
        order("LSU", "KXNCAAFSF", "2026-08-13T19:06:05.418402Z", 3_000, 0.18),
    ], series_rung)
    grid = c.sweep(orders, tiers, labels, ("LSU", "2026-08-13"))
    by = {(r["window_minutes"], r["min_contracts"], r["min_taker_cost"]): r for r in grid}
    assert by[(15, 1_000, 0.0)]["finds_reference"] is True
    assert by[(15, 5_000, 0.0)]["finds_reference"] is False
    assert by[(15, 5_000, 0.0)]["leads"] == 1


def test_the_sweep_counts_what_else_a_setting_drags_in(ladder):
    """A setting that finds the case and forty other things is worse."""
    series_rung, labels, tiers = ladder
    orders = with_rungs([
        order("LSU", "KXNCAAFPLAYOFF", "2026-08-13T19:05:35.120042Z", 837_500, 0.41),
        order("LSU", "KXNCAAFSF", "2026-08-13T19:06:05.418402Z", 470_000, 0.18),
        order("ORE", "KXNCAAFPLAYOFF", "2026-08-20T12:00:00.000000Z", 3_000, 0.40),
        order("ORE", "KXNCAAFQF", "2026-08-20T12:01:00.000000Z", 3_000, 0.40),
    ], series_rung)
    grid = c.sweep(orders, tiers, labels, ("LSU", "2026-08-13"))
    by = {(r["window_minutes"], r["min_contracts"], r["min_taker_cost"]): r for r in grid}
    loose = by[(15, 1_000, 0.0)]
    assert loose["events"] == 2 and loose["other_schools"] == "ORE"
    tight = by[(15, 5_000, 0.0)]
    assert tight["events"] == 1 and tight["other_schools"] == ""


def test_the_price_profile_separates_the_cash_parkers(ladder):
    series_rung, _labels, _tiers = ladder
    orders = with_rungs([
        order("LSU", "KXNCAAFPLAYOFF", "2026-08-13T19:05:35.120042Z", 837_500, 0.41),
        order("MICH", "KXNCAAFQF", "2026-09-06T02:31:54.285215Z", 100_000, 0.99, side="no"),
    ], series_rung)
    rows = c.price_profile(orders)
    dear = [r for r in rows if r["taker_price_band"].startswith("$0.95")][0]
    assert dear["orders"] == 1 and dear["no_orders"] == 1
    mid = [r for r in rows if r["taker_price_band"].startswith("$0.25")][0]
    assert mid["orders"] == 1 and mid["yes_orders"] == 1


def test_coverage_names_a_rung_nothing_can_be_tuned_on(ladder):
    """A rung with no traded market is one the floors were not tuned on."""
    import sqlite3
    series_rung, labels, _ = ladder
    db = sqlite3.connect(":memory:")
    db.executescript((PROJECT_ROOT / "normalize" / "schema.sql").read_text())
    db.execute("INSERT INTO market (source, source_market_id, series_id, team,"
               " first_seen_at, raw_path) VALUES ('kalshi', 'KXNCAAF-27-LSU',"
               " 'KXNCAAF', 'LSU', 0, 'x')")
    db.commit()
    rows = {r["rung"]: r for r in c.coverage(db, series_rung, labels)}
    assert rows["national_title"]["markets"] == 1
    assert rows["national_title"]["markets_with_trades"] == 0
    assert rows["title_game"]["markets"] == 0
    db.close()


def test_the_sweep_never_claims_an_optimum(ladder):
    """One confirmed case cannot support a false-negative rate.

    The module says so in prose; this keeps the words there, because the
    temptation to read the table as a calibration is the whole risk.
    """
    doc = c.__doc__
    assert "sensitivity analysis, not a calibration" in doc
    assert "One true positive cannot support" in doc
