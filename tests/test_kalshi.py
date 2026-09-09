"""Collector tests against recorded fixtures. No network."""
import json
from pathlib import Path

import httpx
import pytest

from collectors import kalshi
from collectors.common import Client, RawArchive, State, load_yaml, read_raw, PROJECT_ROOT

FIX = Path(__file__).parent / "fixtures"
CFG = load_yaml(PROJECT_ROOT / "config" / "targets.yml")["kalshi"]


def fixture(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture
def matcher():
    return kalshi.Matcher.from_config(CFG["match"])


def test_matcher_accepts_every_market_found_in_recon(matcher):
    rows = fixture("kalshi_nebraska_markets_full.json")
    assert len(rows) == 225
    misses = [r["ticker"] for r in rows if matcher.why(r, r["event_title"]) is None]
    assert misses == []


def test_matcher_rejects_other_teams_and_heisman_field(matcher):
    rows = fixture("kalshi_negative_markets.json")
    assert len(rows) > 50
    hits = [(r["ticker"], matcher.why(r, r["event_title"])) for r in rows if matcher.why(r, r["event_title"])]
    assert hits == []


def test_matcher_ticker_pattern_covers_known_shapes(matcher):
    for t in ["KXNCAAFGAME-26SEP05OHIONEB-OHIO", "KXNCAAFGAME-26SEP26NEBMSU-MSU",
              "KXNCAAFWINS-26NEB-6", "KXNCAAFTEAMYDS-26SEP05OHIONEB-NEB275",
              "KXNCAAFBIGTENLEADER-26SACK-NEBOCHA", "KXNCAAFB10-26-NEB"]:
        assert matcher.why({"ticker": t}) == "ticker", t
    # the series prefix itself must not count
    assert matcher.why({"ticker": "KXNEBPRIMARY-26-SMITH"}) is None


class Router:
    """Fake Kalshi: serves fixtures, records every request, paginates trades."""

    def __init__(self):
        self.calls = []
        self.events = fixture("kalshi_events_KXNCAAFB10.json")
        self.market = fixture("kalshi_market_KXNCAAFB10-26-NEB.json")
        self.trades = fixture("kalshi_trades_KXNCAAFB10-26-NEB.json")["trades"]
        self.fail_first = 0  # number of 429s to return before succeeding

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.url.path, dict(request.url.params)))
        if self.fail_first:
            self.fail_first -= 1
            return httpx.Response(429, json={"error": "too many requests"})
        p = request.url.path
        q = request.url.params
        if p.endswith("/events"):
            if q.get("series_ticker") == "KXNCAAFB10":
                return httpx.Response(200, json=self.events)
            return httpx.Response(200, json={"events": [], "cursor": ""})
        if p.endswith("/markets/KXNCAAFB10-26-NEB"):
            return httpx.Response(200, json=self.market)
        if p.endswith("/markets/trades"):
            rows = self.trades
            if "min_ts" in q:
                min_ts = int(q["min_ts"])
                rows = [t for t in rows if int(kalshi.parse_rfc3339(t["created_time"]).timestamp()) >= min_ts]
            if q.get("cursor") == "page2":
                return httpx.Response(200, json={"trades": rows[50:], "cursor": ""})
            if len(rows) > 50:
                return httpx.Response(200, json={"trades": rows[:50], "cursor": "page2"})
            return httpx.Response(200, json={"trades": rows, "cursor": ""})
        return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def small_cfg():
    cfg = dict(CFG)
    cfg["series"] = ["KXNCAAFB10", "KXNCAAFWINS"]
    cfg["min_seconds_between_requests"] = 0
    return cfg


def run_once(tmp_path, router, cfg, matcher, historical=False):
    client = Client(cfg["base_url"], min_interval=0, transport=httpx.MockTransport(router.handler))
    archive = RawArchive(tmp_path / "raw")
    state = State(tmp_path / "state" / "kalshi.json")
    stats = kalshi.RunStats()
    matched = kalshi.discover(client, archive, cfg, matcher, historical, stats)
    for m in matched:
        kalshi.collect_market(client, archive, cfg, state, m, historical, stats)
    state.save(force=True)   # what main() does at the end of a run
    client.close()
    return matched, state, archive, stats


def test_end_to_end_archives_verbatim_and_watermarks(tmp_path, small_cfg, matcher):
    router = Router()
    matched, state, archive, stats = run_once(tmp_path, router, small_cfg, matcher)
    assert [m.ticker for m in matched] == ["KXNCAAFB10-26-NEB"]
    assert matched[0].reason == "ticker"
    # two discovery pages (one per series), one market fetch, two trade pages
    assert stats.pages == 2 and stats.trades_pages == 2 and stats.trades_rows == 83
    files = sorted((tmp_path / "raw" / "kalshi").rglob("*.json.gz"))
    assert len(files) == 5
    # raw body is byte-for-byte what the transport sent
    trades_files = [f for f in files if f.name.startswith("trades_")]
    env = read_raw(trades_files[0])
    assert env["source"] == "kalshi" and env["status"] == 200 and env["fetched_at"].endswith("Z")
    assert json.loads(env["body"]) == {"trades": router.trades[:50], "cursor": "page2"}
    # watermark is the newest trade timestamp
    st = state.market("KXNCAAFB10-26-NEB")
    newest = max(int(kalshi.parse_rfc3339(t["created_time"]).timestamp()) for t in router.trades)
    assert st["watermark_ts"] == newest and st["status"] == "active" and "finalized_complete" not in st


def test_second_run_uses_watermark_and_never_rewrites_raw(tmp_path, small_cfg, matcher):
    router = Router()
    run_once(tmp_path, router, small_cfg, matcher)
    before = sorted((tmp_path / "raw").rglob("*.json.gz"))
    router.calls.clear()
    matched, state, archive, stats = run_once(tmp_path, router, small_cfg, matcher)
    trade_calls = [q for p, q in router.calls if p.endswith("/markets/trades")]
    assert trade_calls and all("min_ts" in q for q in trade_calls)
    newest = state.market("KXNCAAFB10-26-NEB")["watermark_ts"]
    assert int(trade_calls[0]["min_ts"]) == newest - small_cfg["watermark_overlap_seconds"]
    after = sorted((tmp_path / "raw").rglob("*.json.gz"))
    assert set(before) <= set(after) and len(after) > len(before)
    for f in before:  # untouched
        assert f.stat().st_size > 0


def test_finalized_market_is_fetched_once_then_skipped(tmp_path, small_cfg, matcher):
    router = Router()
    router.market = {"market": dict(router.market["market"], status="finalized")}
    run_once(tmp_path, router, small_cfg, matcher)
    router.calls.clear()
    _, state, _, stats = run_once(tmp_path, router, small_cfg, matcher)
    assert state.market("KXNCAAFB10-26-NEB")["finalized_complete"] is True
    assert stats.skipped_finalized == 1
    assert not any(p.endswith("/markets/trades") for p, _ in router.calls)


def test_429_is_retried_with_backoff(tmp_path, small_cfg, matcher, monkeypatch):
    sleeps = []
    import collectors.common as common
    monkeypatch.setattr(common.time, "sleep", lambda s: sleeps.append(s))
    router = Router()
    router.fail_first = 2
    client = Client(small_cfg["base_url"], min_interval=0, transport=httpx.MockTransport(router.handler))
    resp = client.get("/markets/KXNCAAFB10-26-NEB")
    assert resp.status == 200 and client.retries == 2
    assert sleeps[:2] == [1.0, 2.0]


def test_raw_archive_refuses_overwrite(tmp_path):
    from collectors.common import Response, utc_now
    a = RawArchive(tmp_path)
    now = utc_now()
    r = Response("http://x/y", 200, "{}", now)
    a.write("kalshi", "market", "T", r)
    with pytest.raises(FileExistsError):
        a.write("kalshi", "market", "T", r)


def test_wide_scope_keeps_every_market_in_the_walked_series():
    wide = kalshi.Matcher.from_config(CFG["match"], "all")
    rows = fixture("kalshi_negative_markets.json")      # other teams, Heisman field
    assert rows and all(wide.why(r, r["event_title"]) == "scope:all" for r in rows)


def test_untraded_markets_cost_no_requests(tmp_path, small_cfg, matcher):
    """At full scale most strike-level markets never trade. One that has not
    is recorded and skipped, and its metadata is already in the archived
    discovery page."""
    router = Router()
    client = Client(small_cfg["base_url"], min_interval=0,
                    transport=httpx.MockTransport(router.handler))
    archive = RawArchive(tmp_path / "raw")
    state = State(tmp_path / "state" / "kalshi.json")
    stats = kalshi.RunStats()
    m = kalshi.Matched("KXNCAAFB10-26-NEB", "KXNCAAFB10", "KXNCAAFB10-26",
                       "ticker", "live", "active", "", volume=0.0)
    kalshi.collect_market(client, archive, small_cfg, state, m, False, stats)
    client.close()
    assert stats.skipped_no_volume == 1
    assert router.calls == []
    assert state.market("KXNCAAFB10-26-NEB")["untraded"] is True


def test_one_markets_failure_does_not_end_the_pass(tmp_path, small_cfg, matcher, monkeypatch):
    """A 2.9-hour pass died at market 6,453 of 22,564 because a single market's
    retries ran out. The run must record it and carry on."""
    import collectors.common as common
    monkeypatch.setattr(common.time, "sleep", lambda s: None)

    class Failing(Router):
        def handler(self, request):
            if request.url.path.endswith("/markets/trades"):
                return httpx.Response(503, text="down")
            return super().handler(request)

    router = Failing()
    cfg = dict(small_cfg, max_attempts=2)
    client = Client(cfg["base_url"], min_interval=0, max_attempts=2,
                    transport=httpx.MockTransport(router.handler))
    archive = RawArchive(tmp_path / "raw")
    state = State(tmp_path / "state" / "kalshi.json")
    stats = kalshi.RunStats()
    matched = kalshi.discover(client, archive, cfg, matcher, False, stats)
    failed = []
    for m in matched:
        try:
            kalshi.collect_market(client, archive, cfg, state, m, False, stats)
        except kalshi.RetryError:
            failed.append(m.ticker)
    client.close()
    assert failed == ["KXNCAAFB10-26-NEB"]
    assert state.market("KXNCAAFB10-26-NEB").get("watermark_ts") is None


def test_network_errors_get_a_longer_budget_than_http_errors(monkeypatch):
    """A remote 429 means slow down. A local resolver failure means wait,
    because nothing else is reachable either."""
    import collectors.common as common
    monkeypatch.setattr(common.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def always_down(request):
        calls["n"] += 1
        raise httpx.ConnectError("nodename nor servname provided")

    c = Client("https://x.test", min_interval=0, max_attempts=2,
               network_error_budget=5.0, transport=httpx.MockTransport(always_down))
    with pytest.raises(kalshi.RetryError):
        c.get("/markets/trades")
    c.close()
    # max_attempts is 2; the network budget must have carried it well past that
    assert calls["n"] > 2, f"gave up after {calls['n']} attempts, ignoring the budget"


def test_state_is_batched_but_never_lost(tmp_path):
    """1.3 MB rewritten per market is tens of gigabytes across a wide pass.
    Batching is fine as long as the run ends with everything on disk."""
    p = tmp_path / "state" / "kalshi.json"
    s = State(p, flush_every=10)
    for i in range(9):
        s.market(f"T{i}")["watermark_ts"] = i
        s.save()
    assert not p.exists(), "should not have written yet"
    s.save(force=True)
    assert len(State(p).data["markets"]) == 9


# --- the pre-cutoff sweep ----------------------------------------------------

class HistoricalRouter(Router):
    """Kalshi with a live/historical cutoff.

    `/markets/trades` serves the recent rows, `/historical/trades` serves rows
    from before the cutoff. Both honour `min_ts`, which is the whole point:
    the live watermark is newer than every historical row, so passing it to
    the historical endpoint returns nothing.
    """

    def __init__(self):
        super().__init__()
        oldest = min(int(kalshi.parse_rfc3339(t["created_time"]).timestamp())
                     for t in self.trades)
        self.cutoff = oldest
        self.historical = [
            dict(t, trade_id=f"h{i}",
                 created_time="2026-02-0%dT12:00:00.000000Z" % (i + 1))
            for i, t in enumerate(self.trades[:3])]

    def handler(self, request):
        p = request.url.path
        if p.endswith("/historical/trades"):
            self.calls.append((p, dict(request.url.params)))
            q = request.url.params
            rows = self.historical
            if "min_ts" in q:
                mt = int(q["min_ts"])
                rows = [t for t in rows
                        if int(kalshi.parse_rfc3339(t["created_time"]).timestamp()) >= mt]
            return httpx.Response(200, json={"trades": rows, "cursor": ""})
        return super().handler(request)


def test_historical_sweep_is_not_bounded_by_the_live_watermark(tmp_path, small_cfg, matcher):
    """The bug that would have wasted a twenty-hour pass.

    Every 2026 futures market opened in January, and coach hedges are
    plausibly written preseason. That window is only reachable through
    `/historical/trades`. Passing the live watermark as `min_ts` asks that
    endpoint for a window starting after its data ends, so the run costs a
    full day and returns none of what it was run for.
    """
    router = HistoricalRouter()
    run_once(tmp_path, router, small_cfg, matcher)          # live-only first
    router.calls.clear()
    _, state, _, stats = run_once(tmp_path, router, small_cfg, matcher, historical=True)

    hist = [q for p, q in router.calls if p.endswith("/historical/trades")]
    assert hist, "the historical endpoint was never called"
    assert all("min_ts" not in q for q in hist), \
        "the historical sweep must not inherit the live watermark"
    assert state.market("KXNCAAFB10-26-NEB")["historical_done"] is True


def test_the_historical_sweep_runs_once_and_then_stops(tmp_path, small_cfg, matcher):
    """Pre-cutoff data is settled and immutable, so it is swept, not polled."""
    router = HistoricalRouter()
    run_once(tmp_path, router, small_cfg, matcher, historical=True)
    router.calls.clear()
    run_once(tmp_path, router, small_cfg, matcher, historical=True)
    assert not any(p.endswith("/historical/trades") for p, _ in router.calls)


def test_a_settled_market_still_gets_its_pre_cutoff_history(tmp_path, small_cfg, matcher):
    """The other half of the bug.

    2,836 markets were already flagged finalized by the live-only pass. A
    settled market cannot trade again, so it is skipped -- which would have
    meant its January-to-July history was never fetched at all.
    """
    router = HistoricalRouter()
    router.market = {"market": dict(router.market["market"], status="finalized")}
    _, state, _, _ = run_once(tmp_path, router, small_cfg, matcher)
    assert state.market("KXNCAAFB10-26-NEB")["finalized_complete"] is True

    router.calls.clear()
    _, state, _, stats = run_once(tmp_path, router, small_cfg, matcher, historical=True)
    assert any(p.endswith("/historical/trades") for p, _ in router.calls), \
        "a finalized market was skipped before its historical sweep"
    # and it costs no metadata request, because settled metadata is frozen
    assert not any(p.endswith("/markets/KXNCAAFB10-26-NEB") for p, _ in router.calls)
    assert state.market("KXNCAAFB10-26-NEB")["historical_done"] is True

    router.calls.clear()
    _, _, _, stats = run_once(tmp_path, router, small_cfg, matcher, historical=True)
    assert stats.skipped_finalized == 1
    assert not router.calls or not any("trades" in p for p, _ in router.calls)
