"""Canonical team resolution.

The collision tests are the point of this file. Kalshi reuses short codes
across divisions, so keying on a code merges two schools into one number and
nothing in the output looks wrong.
"""
import pytest

from normalize.teams import normalize_name, resolve_team


@pytest.mark.parametrize("a,b", [
    ("Washington St.", "Winona State Warriors"),   # both appear as WSU
    ("Kansas St.", "Kentucky State Thorobreds"),   # both appear as KSU
    ("Colorado St.", "Central State (OH) Marauders"),  # both appear as CSU
    ("Weber St.", "Webber International Warriors"),    # both appear as WEB
    ("Louisiana Christian Wildcats", "Lane Dragons"),  # both appear as LC
])
def test_codes_reused_across_schools_resolve_apart(a, b):
    ra, rb = resolve_team(a), resolve_team(b)
    assert ra.team and rb.team, (a, b, ra.how, rb.how)
    assert ra.team.id != rb.team.id, f"{a} and {b} collapsed onto one school"


@pytest.mark.parametrize("variants", [
    ("SMU", "SMU Mustangs"),
    ("Maryland", "Maryland Terrapins"),
    ("North Carolina St.", "NC State Wolfpack"),
    ("Nebraska", "Nebraska Cornhuskers"),
])
def test_name_variants_of_one_school_agree(variants):
    ids = {resolve_team(v).team.id for v in variants if resolve_team(v).team}
    assert len(ids) == 1, f"{variants} did not agree: {ids}"


@pytest.mark.parametrize("name,expected", [
    ("Appalachian St.", "App State Mountaineers"),
    ("Hawaii", "Hawai'i Rainbow Warriors"),
    ("Bowling", "Bowling Green Falcons"),
    ("Charlotte", "Charlotte 49ers"),
    ("Troy", "Troy Trojans"),
    ("University at Albany", "UAlbany Great Danes"),
])
def test_hand_checked_overrides(name, expected):
    r = resolve_team(name)
    assert r.how == "override" and r.team.name == expected


def test_names_with_no_defensible_match_stay_unresolved():
    for n in ("UT Rio Grande Valley", "Roosevelt"):
        r = resolve_team(n)
        assert r.team is None and r.how == "unresolvable"


def test_nonsense_is_reported_not_guessed():
    r = resolve_team("Not A Real Team 12345")
    assert r.team is None and r.how == "unmatched"


def test_normalisation_folds_the_spellings_platforms_use():
    assert normalize_name("North Carolina St.") == normalize_name("North Carolina State")
    assert normalize_name("Texas A&M") == normalize_name("Texas A and M")
