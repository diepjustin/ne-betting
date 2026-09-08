"""Normalization tests against real titles taken from recorded responses."""
import pytest

from normalize import resolve as r


# --- market type -------------------------------------------------------------

@pytest.mark.parametrize("ticker,expected", [
    ("KXNCAAFGAME-26SEP05OHIONEB-NEB", r.GAME_WINNER),
    ("KXNCAAFSPREAD-26SEP12BGSUNEB-NEB49", r.SPREAD),
    ("KXNCAAFTOTAL-26SEP12BGSUNEB-33", r.TOTAL),
    ("KXNCAAFWINS-26NEB-6", r.SEASON_WINS),
    ("KXNCAAFB10-26-NEB", r.CONFERENCE_CHAMP),
    ("KXNCAAFTEAMYDS-26SEP05OHIONEB-NEB275", r.TEAM_STAT),
    ("KXNCAAFCOACHOUT-27FEB01-MRHU", r.COACH),
    ("KXNCAAFLEADER-26RECYDS-NEBNHUN", r.PLAYER_PROP),
])
def test_kalshi_market_types(ticker, expected):
    assert r.resolve_kalshi({"ticker": ticker, "title": ""}).market_type == expected


def test_unknown_kalshi_family_is_recorded_not_guessed():
    got = r.resolve_kalshi({"ticker": "KXSOMETHINGNEW-26-NEB", "title": "?"})
    assert got.market_type == r.OTHER
    assert "KXSOMETHINGNEW" in got.reason


# --- game identity -----------------------------------------------------------

@pytest.mark.parametrize("ticker,game", [
    ("KXNCAAFGAME-26SEP05OHIONEB-NEB", "2026-09-05-OHIO-NEB"),
    ("KXNCAAFSPREAD-26SEP12BGSUNEB-NEB49", "2026-09-12-BGSU-NEB"),
    ("KXNCAAFGAME-26SEP26NEBMSU-NEB", "2026-09-26-NEB-MSU"),
])
def test_kalshi_game_ids(ticker, game):
    assert r.resolve_kalshi({"ticker": ticker, "title": ""}).game_id == game


def test_season_long_markets_have_no_game():
    for t in ("KXNCAAFWINS-26NEB-6", "KXNCAAFB10-26-NEB"):
        assert r.resolve_kalshi({"ticker": t, "title": ""}).game_id is None


@pytest.mark.parametrize("slug,game", [
    ("cfb-ohio-nebr-2026-09-05", "2026-09-05-OHIO-NEB"),
    ("cfb-mich-neb-2025-09-20", "2025-09-20-MICH-NEB"),
    ("cfb-pur-ucla-2026-09-19", "2026-09-19-PUR-UCLA"),
])
def test_polymarket_dated_slugs(slug, game):
    got = r.resolve_polymarket({"sportsMarketType": "moneyline", "question": "x"}, slug)
    assert got.game_id == game


def test_polymarket_dateless_2024_slug_uses_game_start_time():
    got = r.resolve_polymarket(
        {"sportsMarketType": "moneyline", "question": "Will Indiana beat Nebraska?",
         "gameStartTime": "2024-10-19 16:00:00+00"},
        "cfb-indiana-vs-nebraska")
    assert got.game_id == "2024-10-19-IU-NEB"


def test_both_platforms_name_the_same_game_the_same_way():
    """The point of the identifier: a Kalshi ticker and a Polymarket slug for
    one game must join."""
    k = r.resolve_kalshi({"ticker": "KXNCAAFGAME-26SEP05OHIONEB-NEB", "title": ""})
    p = r.resolve_polymarket({"sportsMarketType": "moneyline", "question": "x"},
                             "cfb-ohio-nebr-2026-09-05")
    assert k.game_id == p.game_id == "2026-09-05-OHIO-NEB"


def test_a_reused_kalshi_code_refuses_to_name_a_game():
    """WSU is Washington St. and Winona State. A blob that splits more than
    one way, or uses a code that names two schools, gets no game rather than
    the wrong one."""
    assert r._split_kalshi_teams("ZZZZQQQQ") is None


# --- older Polymarket phrasing ----------------------------------------------

@pytest.mark.parametrize("question,expected", [
    ("Will Nebraska beat Colorado?", r.GAME_WINNER),
    ("Will Indiana beat Nebraska by 7 or more points?", r.SPREAD),
    ("Will Nebraska win by 7 or more points?", r.SPREAD),
    ("Will there be 49 or more combined points scored?", r.TOTAL),
    ("Will Nebraska and Boston College combine for 47 or more points?", r.TOTAL),
])
def test_untagged_polymarket_questions(question, expected):
    got = r.resolve_polymarket({"question": question}, "cfb-nebraska-vs-rutgers")
    assert got.market_type == expected


def test_unrecognised_polymarket_question_reports_why():
    got = r.resolve_polymarket({"question": "Something entirely new?"}, "")
    assert got.market_type == r.OTHER and got.reason


# --- people ------------------------------------------------------------------

def test_player_is_taken_from_the_market_not_the_prose():
    got = r.resolve_kalshi({
        "ticker": "KXNCAAFLEADER-26RECYDS-NEBNHUN",
        "title": "Nyziah Hunter records the most receiving yards in college football",
        "yes_sub_title": "Nyziah Hunter"})
    assert got.market_type == r.PLAYER_PROP and got.player == "Nyziah Hunter"


def test_polymarket_touchdown_prop_names_the_player_and_inherits_the_team():
    got = r.resolve_polymarket(
        {"sportsMarketType": "anytime_touchdowns", "question": "Jacory Barney Jr.: Anytime Touchdown"},
        "cfb-ohio-nebr-2026-09-05")
    assert got.player == "Jacory Barney Jr." and got.team == "NEB"


def test_a_coach_award_is_not_filed_as_a_player_prop():
    """Kalshi files coach awards in the player-award series. Matt Rhule is not
    a 20-year-old, and the ethics guardrail turns on that distinction."""
    got = r.resolve_kalshi({
        "ticker": "KXNCAAFAWARD-26EDDI-MRHU", "title": "Matt Rhule wins the award",
        "yes_sub_title": "Matt Rhule",
        "rules_primary": "If Matt Rhule wins the Eddie Robinson Award in the 2026 "
                         "college football season, then the market resolves to Yes."})
    assert got.market_type == r.COACH and got.player is None


def test_a_real_player_award_stays_a_player_prop():
    got = r.resolve_kalshi({
        "ticker": "KXNCAAFAWARD-26RIMI-JEVA", "title": "Justin Evans wins the award",
        "yes_sub_title": "Justin Evans",
        "rules_primary": "If Justin Evans wins the Rimington Trophy in the 2026 "
                         "college football season, then the market resolves to Yes."})
    assert got.market_type == r.PLAYER_PROP and got.player == "Justin Evans"
