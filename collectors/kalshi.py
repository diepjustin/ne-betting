"""Kalshi collector.

    uv run python -m collectors.kalshi [--historical] [--dry-run] [--data-dir DIR]

Each run:
  1. walks every series in config/targets.yml through /events (live) and,
     with --historical, /historical/markets, archiving every page;
  2. keeps the markets whose ticker or text matches the Nebraska patterns;
  3. for each kept market fetches /markets/{ticker} and every page of
     /markets/trades since that market's watermark, archiving every response;
  4. advances the watermark and prints a summary.

Nothing is parsed beyond what is needed to paginate, match and watermark.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .common import (
    PROJECT_ROOT,
    Client,
    RawArchive,
    State,
    load_yaml,
    parse_rfc3339,
    setup_logging,
)

log = logging.getLogger("husker.kalshi")
SOURCE = "kalshi"

TEXT_FIELDS = ("title", "subtitle", "yes_sub_title", "no_sub_title", "rules_primary")


def _volume(market: dict) -> float:
    try:
        return float(market.get("volume_fp") or 0)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class Matcher:
    text_patterns: list[re.Pattern]
    ticker_pattern: re.Pattern
    match_all: bool = False

    @classmethod
    def from_config(cls, cfg: dict, scope: str = "nebraska") -> "Matcher":
        return cls(
            [re.compile(p, re.I) for p in cfg["text_patterns"]],
            re.compile(cfg["ticker_pattern"]),
            match_all=(scope == "all"),
        )

    def why(self, market: dict, event_title: str = "") -> str | None:
        """Return the reason a market matches, or None."""
        if self.match_all:
            return "scope:all"
        ticker = market.get("ticker", "")
        if self.ticker_pattern.search(ticker):
            return "ticker"
        texts = [str(market.get(f) or "") for f in TEXT_FIELDS] + [event_title]
        for p in self.text_patterns:
            for t in texts:
                if p.search(t):
                    return f"text:{p.pattern}"
        return None


@dataclass
class Matched:
    ticker: str
    series: str
    event_ticker: str
    reason: str
    partition: str  # live | historical
    status: str
    close_time: str
    volume: float = 0.0


@dataclass
class RunStats:
    series_walked: int = 0
    pages: int = 0
    markets_seen: int = 0
    matched: list[Matched] = field(default_factory=list)
    trades_pages: int = 0
    trades_rows: int = 0
    skipped_finalized: int = 0
    skipped_no_volume: int = 0
    requests: int = 0
    retries: int = 0
    files: int = 0
    bytes: int = 0


def discover(client: Client, archive: RawArchive, cfg: dict, matcher: Matcher,
             historical: bool, stats: RunStats) -> list[Matched]:
    found: dict[str, Matched] = {}
    for series in cfg["series"]:
        stats.series_walked += 1
        # live side: events with nested markets
        cursor = None
        page = 0
        while True:
            page += 1
            params = {"series_ticker": series, "limit": cfg["page_size"],
                      "with_nested_markets": "true"}
            if cursor:
                params["cursor"] = cursor
            resp = client.get("/events", params)
            archive.write(SOURCE, "events", f"{series}_p{page}", resp)
            stats.pages += 1
            body = resp.json()
            for ev in body.get("events", []):
                for m in ev.get("markets", []) or []:
                    stats.markets_seen += 1
                    why = matcher.why(m, ev.get("title", ""))
                    if why and m["ticker"] not in found:
                        found[m["ticker"]] = Matched(
                            m["ticker"], series, ev.get("event_ticker", ""), why,
                            "live", m.get("status", ""), m.get("close_time", ""),
                            _volume(m))
            cursor = body.get("cursor")
            if not cursor:
                break
        if not historical:
            continue
        cursor = None
        page = 0
        while True:
            page += 1
            params = {"series_ticker": series, "limit": cfg["trades_page_size"]}
            if cursor:
                params["cursor"] = cursor
            resp = client.get("/historical/markets", params)
            archive.write(SOURCE, "historical_markets", f"{series}_p{page}", resp)
            stats.pages += 1
            body = resp.json()
            for m in body.get("markets", []):
                stats.markets_seen += 1
                why = matcher.why(m)
                if why and m["ticker"] not in found:
                    found[m["ticker"]] = Matched(
                        m["ticker"], series, m.get("event_ticker", ""), why,
                        "historical", m.get("status", ""), m.get("close_time", ""),
                        _volume(m))
            cursor = body.get("cursor")
            if not cursor:
                break
    stats.matched = list(found.values())
    return stats.matched


def collect_market(client: Client, archive: RawArchive, cfg: dict, state: State,
                   m: Matched, historical: bool, stats: RunStats) -> None:
    st = state.market(m.ticker)
    # Never traded, so there is nothing to pull. Its metadata is already in the
    # archived discovery page, and the normalizer reads markets from there too.
    if m.volume <= cfg["min_volume_for_trades"] and not st.get("watermark_ts"):
        st["series"], st["status"], st["volume"] = m.series, m.status, m.volume
        st["untraded"] = True
        stats.skipped_no_volume += 1
        return
    st.pop("untraded", None)
    if st.get("finalized_complete"):
        # trades cannot occur after settlement and metadata is frozen; the
        # last full fetch is already in the archive.
        stats.skipped_finalized += 1
        return

    meta_path = "/markets/" if m.partition == "live" else "/historical/markets/"
    resp = client.get(f"{meta_path}{m.ticker}")
    archive.write(SOURCE, "market" if m.partition == "live" else "historical_market",
                  m.ticker, resp)
    status = m.status
    if resp.status == 200:
        status = (resp.json().get("market") or {}).get("status", status)

    trades_path = "/markets/trades" if m.partition == "live" else "/historical/trades"
    endpoints = [trades_path]
    if historical and m.partition == "live":
        # a live market that opened before the cutoff has trades on both sides
        endpoints.append("/historical/trades")

    newest = st.get("watermark_ts")  # epoch seconds, int
    newest_seen = newest
    for ep in endpoints:
        cursor = None
        page = 0
        while True:
            page += 1
            params = {"ticker": m.ticker, "limit": cfg["trades_page_size"]}
            if newest is not None:
                params["min_ts"] = max(0, newest - cfg["watermark_overlap_seconds"])
            if cursor:
                params["cursor"] = cursor
            resp = client.get(ep, params)
            label = "trades" if ep == "/markets/trades" else "historical_trades"
            archive.write(SOURCE, label, f"{m.ticker}_p{page}", resp)
            stats.trades_pages += 1
            body = resp.json()
            rows = body.get("trades", [])
            stats.trades_rows += len(rows)
            for t in rows:
                ts = int(parse_rfc3339(t["created_time"]).timestamp())
                if newest_seen is None or ts > newest_seen:
                    newest_seen = ts
            cursor = body.get("cursor")
            if not cursor or not rows:
                break

    st["series"] = m.series
    st["event_ticker"] = m.event_ticker
    st["matched_by"] = m.reason
    st["partition"] = m.partition
    st["status"] = status
    if newest_seen is not None:
        st["watermark_ts"] = newest_seen
    if status == "finalized":
        st["finalized_complete"] = True
    state.save()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    ap.add_argument("--config", type=Path, default=PROJECT_ROOT / "config" / "targets.yml")
    ap.add_argument("--historical", action="store_true",
                    help="also walk /historical/markets and /historical/trades (pre-cutoff data)")
    ap.add_argument("--dry-run", action="store_true", help="discover and match only; fetch no trades")
    ap.add_argument("--scope", choices=("nebraska", "all"),
                    help="override the configured scope; `all` is every college football market")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)
    setup_logging(args.log_level)

    cfg = load_yaml(args.config)["kalshi"]
    if args.scope:
        cfg = dict(cfg, scope=args.scope)
    matcher = Matcher.from_config(cfg["match"], cfg.get("scope", "nebraska"))
    client = Client(cfg["base_url"], min_interval=cfg["min_seconds_between_requests"])
    archive = RawArchive(args.data_dir / "raw")
    state = State(args.data_dir / "state" / "kalshi.json")
    stats = RunStats()
    try:
        matched = discover(client, archive, cfg, matcher, args.historical, stats)
        log.info("discovery: %d series, %d pages, %d markets seen, %d matched",
                 stats.series_walked, stats.pages, stats.markets_seen, len(matched))
        if not args.dry_run:
            for i, m in enumerate(sorted(matched, key=lambda x: x.ticker), 1):
                log.info("[%d/%d] %s (%s, %s)", i, len(matched), m.ticker, m.reason, m.status)
                collect_market(client, archive, cfg, state, m, args.historical, stats)
    finally:
        client.close()
        stats.requests = client.requests_made
        stats.retries = client.retries
        stats.files = archive.files_written
        stats.bytes = archive.bytes_written

    by_reason: dict[str, int] = {}
    for m in stats.matched:
        by_reason[m.reason] = by_reason.get(m.reason, 0) + 1
    summary = {
        "series_walked": stats.series_walked,
        "discovery_pages": stats.pages,
        "markets_seen": stats.markets_seen,
        "markets_matched": len(stats.matched),
        "matched_by": by_reason,
        "trades_pages": stats.trades_pages,
        "trades_rows_fetched": stats.trades_rows,
        "finalized_skipped": stats.skipped_finalized,
        "untraded_skipped": stats.skipped_no_volume,
        "scope": cfg.get("scope", "nebraska"),
        "http_requests": stats.requests,
        "http_retries": stats.retries,
        "raw_files_written": stats.files,
        "raw_bytes_written": stats.bytes,
    }
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
