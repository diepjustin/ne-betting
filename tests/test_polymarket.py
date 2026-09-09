"""Polymarket collector tests against recorded fixtures. No network."""
import json
from pathlib import Path

import httpx
import pytest

from collectors import polymarket
from collectors.common import Client, RawArchive, State, load_yaml, read_raw, PROJECT_ROOT

FIX = Path(__file__).parent / "fixtures"
CFG = load_yaml(PROJECT_ROOT / "config" / "targets.yml")["polymarket"]


def fixture(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture
def matcher():
    return polymarket.Matcher.from_config(CFG["match"], CFG["football"])


@pytest.fixture
def wide():
    return polymarket.Matcher.from_config(CFG["match"], CFG["football"], "all")


def test_matches_nebraska_game_events_on_slug(matcher):
    for e in fixture("pm_nebraska_events.json"):
        assert matcher.why(e) == "slug", e["slug"]


def test_rejects_other_teams_games(matcher):
    negs = fixture("pm_negative_events.json")
    assert len(negs) >= 20
    assert [e["slug"] for e in negs if matcher.why(e)] == []


def test_futures_event_matches_but_only_the_nebraska_rung_counts(matcher):
    b10 = [e for e in fixture("pm_futures_events.json")
           if "big-ten-conference-winner" in e["slug"]][0]
    why = matcher.why(b10)
    assert why and why.startswith("market:")
    keep = [m for m in b10["markets"] if matcher.market_is_nebraska(m, why)]
    assert len(b10["markets"]) == 22
    assert [m["question"] for m in keep] == [
        "Will Nebraska Cornhuskers Win the 2026 Big Ten Championship?"]


def test_every_market_of_a_nebraska_game_counts(matcher):
    ohio = [e for e in fixture("pm_nebraska_events.json")
            if e["slug"] == "cfb-ohio-nebr-2026-09-05"][0]
    keep = [m for m in ohio["markets"] if matcher.market_is_nebraska(m, "slug")]
    assert len(keep) == len(ohio["markets"]) == 148


class Router:
    """Fake gamma + data-api. Records calls, paginates trades by offset."""

    def __init__(self, page_size=1000):
        self.calls = []
        self.events = fixture("pm_nebraska_events.json")
        self.market = fixture("pm_market_510126.json")
        self.trades = sorted(fixture("pm_trades_ohio.json"),
                             key=lambda t: t["timestamp"], reverse=True)
        self.page_size = page_size

    def handler(self, request: httpx.Request) -> httpx.Response:
        p, q = request.url.path, dict(request.url.params)
        self.calls.append((p, q))
        if p.endswith("/events"):
            # one page of Nebraska games for the open-games listing, empty after
            if q.get("offset", "0") != "0":
                return httpx.Response(200, json=[])
            if q.get("series_id") == "12756" and q.get("closed") == "false":
                return httpx.Response(200, json=self.events)
            return httpx.Response(200, json=[])
        if p.endswith("/public-search"):
            return httpx.Response(200, json={"events": [], "pagination": {"hasMore": False}})
        if "/markets/" in p:
            return httpx.Response(200, json=self.market)
        if p.endswith("/trades"):
            rows = self.trades
            if "start" in q:
                rows = [t for t in rows if t["timestamp"] >= int(q["start"])]
            if "end" in q:
                rows = [t for t in rows if t["timestamp"] <= int(q["end"])]
            off = int(q.get("offset", 0))
            return httpx.Response(200, json=rows[off:off + self.page_size])
        return httpx.Response(404, json={"error": "not found"})


def run_once(tmp_path, router, cfg, matcher):
    tr = httpx.MockTransport(router.handler)
    gamma = Client(cfg["gamma_url"], min_interval=0, transport=tr)
    data = Client(cfg["data_url"], min_interval=0, transport=tr)
    archive = RawArchive(tmp_path / "raw")
    state = State(tmp_path / "state" / "polymarket.json")
    stats = polymarket.RunStats()
    matched = polymarket.discover(gamma, archive, cfg, matcher, stats)
    for m in matched:
        polymarket.collect_market(gamma, data, archive, cfg, state, m, stats)
    state.save(force=True)   # what main() does at the end of a run
    gamma.close()
    data.close()
    return matched, state, archive, stats


@pytest.fixture
def cfg():
    return dict(CFG, min_seconds_between_requests=0)


def test_end_to_end_archives_and_watermarks(tmp_path, cfg, matcher, monkeypatch):
    router = Router()
    matched, state, archive, stats = run_once(tmp_path, router, cfg, matcher)
    assert stats.events_matched == 3
    assert stats.markets_matched == 156  # 148 + 7 + 1
    # Only the 38 that have ever traded cost a request; the other 118 are
    # recorded as untraded and skipped, which is where the saving comes from
    # at full college-football scale.
    assert stats.metadata_captured == 38
    assert stats.skipped_no_volume == 118
    files = list((tmp_path / "raw" / SOURCE_DIR).rglob("*.json.gz"))
    assert len([f for f in files if f.name.startswith("gamma_market_")]) == 38
    env = read_raw([f for f in files if f.name.startswith("trades_")][0])
    assert env["source"] == "polymarket" and env["status"] == 200
    assert json.loads(env["body"]) == json.loads(env["body"])  # body is valid JSON text
    newest = max(t["timestamp"] for t in router.trades)
    st = state.market("polymarket:3889420")  # Ohio vs. Nebraska moneyline
    assert st["watermark_ts"] == newest
    assert st["condition_id"].startswith("0x")


SOURCE_DIR = "polymarket"


def test_second_run_uses_start_and_skips_closed(tmp_path, cfg, matcher):
    router = Router()
    run_once(tmp_path, router, cfg, matcher)
    router.calls.clear()
    _, state, _, stats = run_once(tmp_path, router, cfg, matcher)
    trade_calls = [q for p, q in router.calls if p.endswith("/trades")]
    # closed and previously collected, so nothing is re-fetched
    assert stats.skipped_closed == 37
    assert stats.metadata_captured == 0
    open_calls = [q for q in trade_calls if "start" in q]
    assert open_calls, "open markets should be re-polled from their watermark"


def test_metadata_captured_once_then_not_refetched(tmp_path, cfg, matcher):
    router = Router()
    run_once(tmp_path, router, cfg, matcher)
    first = len([q for p, q in router.calls if "/markets/" in p])
    router.calls.clear()
    run_once(tmp_path, router, cfg, matcher)
    second = len([q for p, q in router.calls if "/markets/" in p])
    assert first == 38 and second == 0


def test_offset_paging_walks_whole_history(tmp_path, cfg, matcher):
    router = Router(page_size=50)
    small = dict(cfg, trades_page_size=50)
    _, _, _, stats = run_once(tmp_path, router, small, matcher)
    # the Ohio moneyline has 326 rows: 7 pages of 50 for that market alone
    ohio = [q for p, q in router.calls
            if p.endswith("/trades") and q.get("market", "").startswith("0x77dfd142")]
    assert len(ohio) == 7
    assert [int(q.get("offset", 0)) for q in ohio] == [0, 50, 100, 150, 200, 250, 300]


def test_offset_ceiling_reanchors_with_end(tmp_path, cfg, matcher):
    router = Router(page_size=50)
    tiny = dict(cfg, trades_page_size=50, max_offset=100)
    run_once(tmp_path, router, tiny, matcher)
    ohio = [q for p, q in router.calls
            if p.endswith("/trades") and q.get("market", "").startswith("0x77dfd142")]
    assert any("end" in q for q in ohio), "should re-anchor once offset hits the ceiling"
    reanchored = [q for q in ohio if "end" in q]
    assert int(reanchored[0]["offset"]) == 0


def test_nebraska_basketball_is_not_collected(matcher):
    """cbb-ill-nebr-2026-02-01 traded $503k. It is not football."""
    bb = fixture("pm_basketball_events.json")
    assert len(bb) >= 10
    assert [e["slug"] for e in bb if matcher.why(e)] == []
    assert [e["slug"] for e in bb if matcher.is_football(e)] == []


def test_older_nebraska_abbreviations_still_match(matcher):
    """Polymarket used `neb` in 2024-25 and `nebr` in 2026."""
    old = fixture("pm_oldstyle_football_events.json")
    assert {e["slug"] for e in old} >= {"cfb-mich-neb-2025-09-20", "cfb-indiana-vs-nebraska"}
    for e in old:
        assert matcher.why(e) == "slug", e["slug"]


def test_wide_scope_keeps_every_football_market_but_no_basketball(wide):
    """Widening to all of college football is not licence to collect
    basketball: the sport gate runs first in every scope."""
    games = fixture("pm_negative_events.json")          # other teams' football
    assert all(wide.why(e) == "scope:all" for e in games)
    bb = fixture("pm_basketball_events.json")           # Nebraska basketball
    assert [e["slug"] for e in bb if wide.why(e)] == []


def test_wide_scope_keeps_every_rung_of_a_futures_event(wide, matcher):
    b10 = [e for e in fixture("pm_futures_events.json")
           if "big-ten-conference-winner" in e["slug"]][0]
    wide_keep = [m for m in b10["markets"] if wide.market_is_nebraska(m, "scope:all")]
    narrow_keep = [m for m in b10["markets"]
                   if matcher.market_is_nebraska(m, matcher.why(b10))]
    assert len(wide_keep) == 22 and len(narrow_keep) == 1
