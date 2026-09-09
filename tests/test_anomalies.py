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
    series_rung, order, tiers = ladder
    events, _leads, _orphans = a.find_ladder_events(
        db, series_rung, tiers, order, a.WINDOW_MINUTES * 60,
        a.MIN_CONTRACTS, a.MIN_TAKER_COST)
    assert len(events) == 1, [e["school"] for e in events]
    e = events[0]
    assert e["school"] == "LSU"
    assert e["yes_contracts"] == 2_462_500 and e["no_contracts"] == 0
    assert e["rungs"] == 4
    assert e["orders"] == 4
    assert e["first_utc"] == "2026-08-13T19:05:35.120042Z"
    assert e["last_utc"] == "2026-08-13T19:06:47.395669Z"
    assert e["span_seconds"] == 72
    assert e["contracts"] == 2_462_500
    assert e["block_orders"] == 4
    assert e["not_a_finding_because"] == ""


def test_the_sum_is_the_four_rungs_we_have_not_the_reported_three_million(db, ladder):
    """$2,462,500, not $3,000,000.

    The reported total includes a fifth rung in KXNCAAFFINALIST, a series that
    was missing from config/targets.yml until 9 Sep 2026 and so was never
    collected. When a pass that includes it is loaded, this figure moves and
    this test should be updated against the new archive -- not before.
    """
    series_rung, order, tiers = ladder
    events, _, _ = a.find_ladder_events(db, series_rung, tiers, order, 900,
                                        a.MIN_CONTRACTS, a.MIN_TAKER_COST)
    assert events[0]["payout_if_every_rung_hits_usd"] == 2_462_500
    assert events[0]["taker_cost_usd"] == 597_550.0
    assert "title_game" not in events[0]["rung_list"]


def test_single_rung_repeat_is_a_lead_not_a_finding(db, ladder):
    """Two big Oregon title trades 12 seconds apart are one rung, so a lead."""
    series_rung, order, tiers = ladder
    events, leads, _ = a.find_ladder_events(db, series_rung, tiers, order, 900,
                                            a.MIN_CONTRACTS, a.MIN_TAKER_COST)
    assert all(e["school"] != "ORE" for e in events)
    ore = [l for l in leads if l["school"] == "ORE"]
    assert len(ore) == 1 and ore[0]["orders"] == 2
    assert ore[0]["not_a_finding_because"] == "one rung only"


def test_penny_sweep_is_huge_in_contracts_and_still_excluded(db, ladder):
    """400,139 contracts at a cent is $4,001. A contract floor alone lets it in."""
    series_rung, order, tiers = ladder
    _, leads, _ = a.find_ladder_events(db, series_rung, tiers, order, 900,
                                       a.MIN_CONTRACTS, a.MIN_TAKER_COST)
    assert all(l["school"] != "NWST" for l in leads)


def test_game_markets_are_not_ladder_rungs(db, ladder):
    """A Saturday spread is not a contract milestone, however big."""
    series_rung, _order2, _ = ladder
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
    group = [{"rung": "playoff_berth", "contracts": 400_000},
             {"rung": "playoff_berth", "contracts": 437_500},
             {"rung": "semifinal", "contracts": 469_000}]
    tiers = {"LSU": {"source": "hypothetical, for this test only",
                     "tiers": {"playoff_berth": 837_500, "semifinal": 470_000}}}
    exact, near = a.match_tiers("LSU", group, tiers)
    # One rung bought in two goes still covers one bonus, so the rung's total
    # is what a tier is compared against.
    assert exact == "playoff_berth=837,500"
    assert near.startswith("semifinal~469,000/470,000")


def test_central_time_moves_a_late_kickoff_back_a_day():
    """01:30 UTC on the 6th is 8:30pm Central on the 5th."""
    assert a.central_day(ts("2026-09-06T23:30:00Z")) == "2026-09-06"
    assert a.central_day(ts("2026-09-06T01:30:00Z")) == "2026-09-05"


def test_a_school_is_counted_once_on_its_own_market(db, ladder):
    """A spread names its school in `team` and again as a side of the game.

    Counted from both columns without collapsing, a school's own total nearly
    doubles. This is the same class of error as the attribution bug that once
    made Nebraska look like 87% of all college football money, so it gets a
    test rather than a comment.
    """
    db.execute("UPDATE market SET away_team='MIA', home_team='STAN'"
               " WHERE source_market_id LIKE 'KXNCAAFSPREAD%'")
    db.commit()
    rows, _totals = a.timeline_daily(db)
    mia = [r for r in rows if r["school"] == "MIA"]
    assert len(mia) == 1
    assert mia[0]["trades"] == 1, "the one Miami spread trade is counted once"
    assert mia[0]["shared_trades"] == 1, "and is marked as naming two schools"


def test_totals_table_counts_every_trade_exactly_once(db, ladder):
    """The per-school table cannot give a total; this one can."""
    db.execute("UPDATE market SET away_team='MIA', home_team='STAN'"
               " WHERE source_market_id LIKE 'KXNCAAFSPREAD%'")
    db.commit()
    rows, totals = a.timeline_daily(db)
    assert sum(t["trades"] for t in totals) == 8
    assert sum(r["trades"] for r in rows) > 8, "school rows double-count on purpose"


def test_a_run_that_finds_nothing_still_writes_a_readable_file(tmp_path):
    """"No hedge found" is a result. A zero-byte file is not."""
    a.write_csv([], tmp_path, "ladder_events", header="ladder")
    text = (tmp_path / "ladder_events.csv").read_text()
    assert text.startswith("school,rungs,rung_list")
    assert len(text.splitlines()) == 1


def test_column_drift_fails_loudly(tmp_path):
    """A renamed column silently dropped from a CSV is a number that vanishes."""
    with pytest.raises(KeyError):
        a.write_csv([{"school": "LSU", "surprise": 1}], tmp_path,
                    "ladder_events", header="ladder")


# --- what Fable's review turned up -------------------------------------------

def add(db, ticker, series, iso, count, price, side, team, block=0, tid=None):
    db.execute(
        "INSERT OR IGNORE INTO market (source, source_market_id, title, series_id,"
        " team, first_seen_at, raw_path) VALUES ('kalshi', ?, ?, ?, ?, 0, 'x')",
        (ticker, f"{team} market", series, team))
    db.execute(
        "INSERT INTO trade (source, source_trade_id, source_market_id, executed_ts,"
        " executed_iso, price_dollars, count, taker_side, taker_cost_usd,"
        " is_block_trade, fetched_at, raw_path)"
        " VALUES ('kalshi', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'x')",
        (tid or f"x{id(iso)}{count}{price}{side}", ticker, ts(iso), iso, price,
         count, side, count * (price if side == "yes" else 1 - price), block))


def test_a_cash_parker_walking_the_ladder_is_not_a_finding(db, ladder):
    """The one that would have been published.

    62 of the 73 orders clearing the size floors on ladder markets are No
    takers, and 39 paid 95 cents or more a contract -- parking cash for a few
    points on a team that will not win. Two rungs of that inside a minute has
    the exact shape of a hedge and is the opposite trade.
    """
    series_rung, order, tiers = ladder
    add(db, "KXNCAAFPLAYOFF-26-MICH", "KXNCAAFPLAYOFF", "2026-09-06T02:31:54.285215Z",
        100_000, 0.01, "no", "MICH")
    add(db, "KXNCAAFQF-27-MICH", "KXNCAAFQF", "2026-09-06T02:32:10.100000Z",
        109_267, 0.02, "no", "MICH")
    db.commit()
    events, leads, _ = a.find_ladder_events(db, series_rung, tiers, order, 900,
                                            a.MIN_CONTRACTS, a.MIN_TAKER_COST)
    assert all(e["school"] != "MICH" for e in events)
    mich = [l for l in leads if l["school"] == "MICH"][0]
    assert mich["rungs"] == 2, "it really is ladder-shaped"
    assert "No purchase" in mich["not_a_finding_because"]


def test_a_yes_side_ladder_bought_near_certainty_is_also_set_aside(db, ladder):
    """Buying at 97 cents is yield, whichever side of the book it is on."""
    series_rung, order, tiers = ladder
    add(db, "KXNCAAFPLAYOFF-26-OSU", "KXNCAAFPLAYOFF", "2026-09-06T02:31:54.285215Z",
        100_000, 0.97, "yes", "OSU")
    add(db, "KXNCAAFQF-27-OSU", "KXNCAAFQF", "2026-09-06T02:32:10.100000Z",
        100_000, 0.96, "yes", "OSU")
    db.commit()
    events, leads, _ = a.find_ladder_events(db, series_rung, tiers, order, 900,
                                            a.MIN_CONTRACTS, a.MIN_TAKER_COST)
    assert all(e["school"] != "OSU" for e in events)
    assert "near-certainty" in [l for l in leads if l["school"] == "OSU"][0]["not_a_finding_because"]


def test_an_order_split_across_fills_still_clears_the_floor(db, ladder):
    """Kalshi returns fills, not orders, and publishes no order id.

    A hedge routed through the book arrives in pieces, every piece under the
    floor. Fills sharing a market, a timestamp and a side are one order.
    """
    series_rung, order, tiers = ladder
    for i in range(10):
        add(db, "KXNCAAFPLAYOFF-26-TEX", "KXNCAAFPLAYOFF",
            "2026-08-20T12:00:00.500000Z", 2_000, 0.30, "yes", "TEX", tid=f"tex-a{i}")
        add(db, "KXNCAAFSF-27-TEX", "KXNCAAFSF",
            "2026-08-20T12:00:30.500000Z", 2_000, 0.35, "yes", "TEX", tid=f"tex-b{i}")
    db.commit()
    orders, _ = a.fetch_milestone_orders(db, series_rung)
    tex = [o for o in orders if o["team"] == "TEX"]
    assert len(tex) == 2 and all(o["fills"] == 10 for o in tex)
    events, _, _ = a.find_ladder_events(db, series_rung, tiers, order, 900,
                                        a.MIN_CONTRACTS, a.MIN_TAKER_COST)
    got = [e for e in events if e["school"] == "TEX"]
    assert len(got) == 1 and got[0]["fills"] == 20 and got[0]["orders"] == 2


def test_an_exchange_flagged_block_skips_the_floors(db, ladder):
    """South Carolina's flagged 40,000 contracts at $0.11 cost $4,400.

    Under the cost floor alone the exchange's own flag would be thrown away.
    """
    series_rung, order, tiers = ladder
    add(db, "KXNCAAFPLAYOFF-26-SCAR", "KXNCAAFPLAYOFF", "2026-07-15T21:33:14.680024Z",
        40_000, 0.11, "yes", "SC", block=1)
    add(db, "KXNCAAFQF-27-SCAR", "KXNCAAFQF", "2026-07-15T21:33:44.680024Z",
        30_000, 0.09, "yes", "SC", block=1)
    db.commit()
    orders, _ = a.fetch_milestone_orders(db, series_rung)
    sc = [o for o in orders if o["team"] == "SC"]
    assert all(o["taker_cost_usd"] < a.MIN_TAKER_COST for o in sc), "below the cost floor"
    assert all(a.clears_floor(o, a.MIN_CONTRACTS, a.MIN_TAKER_COST) for o in sc)
    events, _, _ = a.find_ladder_events(db, series_rung, tiers, order, 900,
                                        a.MIN_CONTRACTS, a.MIN_TAKER_COST)
    assert [e["school"] for e in events].count("SC") == 1


def test_a_passive_hedger_is_not_cut_in_half(db, ladder):
    """`taker_side` names whoever crossed the spread.

    A hedger who rests a bid and is hit shows up on the opposite side, so
    clusters group on the school and report the split instead.
    """
    series_rung, order, tiers = ladder
    add(db, "KXNCAAFPLAYOFF-26-BAY", "KXNCAAFPLAYOFF", "2026-08-20T12:00:00Z",
        100_000, 0.30, "yes", "BAY")
    add(db, "KXNCAAFSF-27-BAY", "KXNCAAFSF", "2026-08-20T12:00:30Z",
        40_000, 0.80, "no", "BAY")
    db.commit()
    events, _, _ = a.find_ladder_events(db, series_rung, tiers, order, 900,
                                        a.MIN_CONTRACTS, a.MIN_TAKER_COST)
    bay = [e for e in events if e["school"] == "BAY"]
    assert len(bay) == 1
    assert bay[0]["mixed_side"] is True
    assert bay[0]["yes_contracts"] == 100_000 and bay[0]["no_contracts"] == 40_000


def test_bowl_selection_is_a_rung(ladder):
    """"Selected to play in a bowl game or the College Football Playoff".

    The rung a mid-tier program is likeliest to be paid on, and the one a
    detector scoped to playoff markets would be blind to.
    """
    series_rung, order, _ = ladder
    assert series_rung["KXNCAAFBOWLGAME"] == "bowl_selection"
    assert order[0] == "bowl_selection"


def test_league_level_markets_are_not_a_schools_rung(ladder):
    """Which conference the champion comes from is nobody's bonus."""
    series_rung, _order, _ = ladder
    assert "KXNCAAFCONF" not in series_rung
    assert "KXNCAAFCFPCONF" not in series_rung
    assert "KXNCAAFSEED" not in series_rung


def test_the_example_tier_block_names_no_real_school():
    """An example naming a real school is a fabricated citation in waiting.

    Worse if its figures are copied from the trades: the detector would then
    "confirm" a match against numbers it took from the trades itself.
    """
    text = (PROJECT_ROOT / "config" / "milestones.yml").read_text()
    for real in ("LSU", "Nebraska", "public records"):
        assert real not in text, f"{real!r} appears in the tier example"
    for from_the_trades in ("837500", "470000", "787500", "367500"):
        assert from_the_trades not in text


def test_player_markets_carry_no_dollar_figure_here(db, ladder):
    """Plan section 9. breakdown.py has the one view that names athletes."""
    db.execute("INSERT INTO market (source, source_market_id, title, market_type,"
               " first_seen_at, raw_path) VALUES ('kalshi', 'KXHEISMAN-26-XYZ',"
               " 'Will Rasean Jones win the Heisman?', 'player_prop', 0, 'x')")
    db.execute("INSERT INTO trade (source, source_trade_id, source_market_id,"
               " executed_ts, executed_iso, price_dollars, count, taker_side,"
               " taker_cost_usd, is_block_trade, fetched_at, raw_path)"
               " VALUES ('kalshi', 'h1', 'KXHEISMAN-26-XYZ', 100, '100', 0.5,"
               " 90000, 'yes', 45000, 0, 0, 'x')")
    db.commit()
    rows = a.large_trades(db, a.MIN_CONTRACTS, a.MIN_TAKER_COST)
    assert all("Heisman" not in (r["title"] or "") for r in rows)
