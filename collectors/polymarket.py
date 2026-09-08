"""Polymarket collector.

    uv run python -m collectors.polymarket [--dry-run] [--data-dir DIR]

Each run:
  1. gathers candidate events three ways -- the College Football game series,
     the College Football futures tag, and public-search on Nebraska terms --
     archiving every page;
  2. keeps the events whose slug, title or nested market questions match the
     Nebraska patterns;
  3. captures full metadata for each nested market the first time it is seen,
     because settled markets drop off the listing endpoints;
  4. pulls trades for every market with a condition id, from that market's
     watermark, and archives every page.

Two hosts are involved: gamma-api for events and markets, data-api for trades.
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

from .common import PROJECT_ROOT, Client, RawArchive, State, load_yaml, setup_logging

log = logging.getLogger("husker.polymarket")
SOURCE = "polymarket"

# Fields on a nested market object that can name a team.
MARKET_TEXT_FIELDS = ("question", "slug", "groupItemTitle", "description")


@dataclass
class Matcher:
    text_patterns: list[re.Pattern]
    slug_pattern: re.Pattern
    football_tag_ids: set[str]
    football_tag_slugs: set[str]
    match_all: bool = False

    @classmethod
    def from_config(cls, cfg: dict, football: dict, scope: str = "nebraska") -> "Matcher":
        return cls(
            [re.compile(p, re.I) for p in cfg["text_patterns"]],
            re.compile(cfg["slug_pattern"], re.I),
            {str(t) for t in football["tag_ids"]},
            {str(s).lower() for s in football["tag_slugs"]},
            match_all=(scope == "all"),
        )

    def is_football(self, event: dict) -> bool:
        """Nebraska's basketball games use the same slug abbreviations as its
        football games, so the sport has to be checked explicitly."""
        for t in event.get("tags") or []:
            if not isinstance(t, dict):
                continue
            if str(t.get("id")) in self.football_tag_ids:
                return True
            if str(t.get("slug") or "").lower() in self.football_tag_slugs:
                return True
        return False

    def why(self, event: dict) -> str | None:
        """Return the reason an event matches, or None.

        A game event matches on its slug (`cfb-ohio-nebr-2026-09-05`). A
        futures event matches when one of its nested markets names Nebraska,
        because the event itself is league-wide ("2026 Big Ten Champion").
        Non-football events never match.
        """
        # The football gate applies in every scope. Widening to all of college
        # football is not licence to collect college basketball.
        if not self.is_football(event):
            return None
        if self.match_all:
            return "scope:all"
        slug = str(event.get("slug") or "")
        if self.slug_pattern.search(slug):
            return "slug"
        for p in self.text_patterns:
            if p.search(str(event.get("title") or "")):
                return f"title:{p.pattern}"
        for m in event.get("markets") or []:
            for p in self.text_patterns:
                for f in MARKET_TEXT_FIELDS:
                    if p.search(str(m.get(f) or "")):
                        return f"market:{p.pattern}"
        return None

    def market_is_nebraska(self, market: dict, event_matched_by: str) -> bool:
        """Within a matched event, is this individual market a Nebraska one?

        For a game event every market is about that game, so all of them count.
        For a futures event only the Nebraska rung counts; the other 21 Big Ten
        teams' markets are not Nebraska markets.
        """
        if self.match_all or event_matched_by == "slug":
            return True
        for p in self.text_patterns:
            for f in MARKET_TEXT_FIELDS:
                if p.search(str(market.get(f) or "")):
                    return True
        return False


@dataclass
class MatchedMarket:
    market_id: str
    condition_id: str
    event_id: str
    event_slug: str
    reason: str
    closed: bool
    volume: float = 0.0


@dataclass
class RunStats:
    discovery_pages: int = 0
    events_seen: int = 0
    events_matched: int = 0
    markets_matched: int = 0
    metadata_captured: int = 0
    trades_pages: int = 0
    trades_rows: int = 0
    skipped_closed: int = 0
    skipped_no_volume: int = 0
    no_condition_id: int = 0
    requests: int = 0
    retries: int = 0
    files: int = 0
    bytes: int = 0


def _page_events(client: Client, archive: RawArchive, cfg: dict, stats: RunStats,
                 params: dict, label: str, floor: str | None = None) -> list[dict]:
    """Walk an offset-paginated gamma /events listing, archiving every page."""
    out, offset = [], 0
    while True:
        p = dict(params, limit=cfg["events_page_size"], offset=offset)
        resp = client.get("/events", p)
        archive.write(SOURCE, "gamma_events", f"{label}_off{offset}", resp)
        stats.discovery_pages += 1
        body = resp.json()
        if not isinstance(body, list) or not body:
            break
        out.extend(body)
        stats.events_seen += len(body)
        if floor and min(str(e.get("startDate") or "") for e in body) < floor:
            break
        offset += cfg["events_page_size"]
        if offset > 20000:  # listing is not supposed to be this deep
            log.warning("stopping %s at offset %d", label, offset)
            break
    return out


def discover(gamma: Client, archive: RawArchive, cfg: dict, matcher: Matcher,
             stats: RunStats) -> list[MatchedMarket]:
    candidates: dict[str, dict] = {}

    def add(events: list[dict]) -> None:
        for e in events:
            if e.get("id"):
                candidates.setdefault(str(e["id"]), e)

    # 1. College Football game events, open then closed back to the floor.
    add(_page_events(gamma, archive, cfg, stats,
                     {"series_id": cfg["cfb_series_id"], "closed": "false",
                      "order": "startDate", "ascending": "false"}, "cfb_games_open"))
    add(_page_events(gamma, archive, cfg, stats,
                     {"series_id": cfg["cfb_series_id"], "closed": "true",
                      "order": "startDate", "ascending": "false"}, "cfb_games_closed",
                     floor=cfg["earliest_start_date"]))
    # 2. College Football futures.
    for closed in ("false", "true"):
        add(_page_events(gamma, archive, cfg, stats,
                         {"tag_id": cfg["cfb_tag_id"], "closed": closed},
                         f"cfb_futures_{closed}"))
    # 3. Keyword candidates. public-search matches loosely, so these are only
    #    candidates; the strict matcher below decides.
    for term in cfg["search_terms"]:
        page = 1
        while True:
            resp = gamma.get("/public-search", {"q": term, "page": page})
            archive.write(SOURCE, "gamma_public_search", f"{term}_p{page}", resp)
            stats.discovery_pages += 1
            body = resp.json()
            evs = body.get("events", []) if isinstance(body, dict) else []
            add(evs)
            stats.events_seen += len(evs)
            if not evs or not (body.get("pagination") or {}).get("hasMore") or page >= 40:
                break
            page += 1

    matched: dict[str, MatchedMarket] = {}
    for e in candidates.values():
        why = matcher.why(e)
        if not why:
            continue
        stats.events_matched += 1
        for m in e.get("markets") or []:
            if not matcher.market_is_nebraska(m, why):
                continue
            mid = str(m.get("id"))
            if mid in matched:
                continue
            cid = str(m.get("conditionId") or "")
            if not cid:
                stats.no_condition_id += 1
            try:
                vol = float(m.get("volume") or 0)
            except (TypeError, ValueError):
                vol = 0.0
            matched[mid] = MatchedMarket(
                mid, cid, str(e.get("id")), str(e.get("slug") or ""), why,
                bool(m.get("closed")), vol)
    stats.markets_matched = len(matched)
    return list(matched.values())


def collect_market(gamma: Client, data: Client, archive: RawArchive, cfg: dict,
                   state: State, m: MatchedMarket, stats: RunStats) -> None:
    st = state.market(f"polymarket:{m.market_id}")
    # Never traded: nothing to pull, and the event listing that carries its
    # metadata is already archived.
    if m.volume <= cfg["min_volume_for_trades"] and not st.get("watermark_ts"):
        st["event_slug"], st["closed"], st["volume"] = m.event_slug, m.closed, m.volume
        st["untraded"] = True
        stats.skipped_no_volume += 1
        state.save()
        return
    st.pop("untraded", None)
    if st.get("closed_complete"):
        stats.skipped_closed += 1
        return

    # Metadata on first sight: settled markets fall off the listing endpoints,
    # so this is the only guaranteed chance to record what the market was.
    if not st.get("metadata_captured"):
        resp = gamma.get(f"/markets/{m.market_id}")
        archive.write(SOURCE, "gamma_market", m.market_id, resp)
        if resp.status == 200:
            st["metadata_captured"] = True
            stats.metadata_captured += 1

    st["event_id"] = m.event_id
    st["event_slug"] = m.event_slug
    st["condition_id"] = m.condition_id
    st["matched_by"] = m.reason
    st["closed"] = m.closed

    if not m.condition_id:
        state.save()
        return

    watermark = st.get("watermark_ts")
    newest_seen = watermark
    # Rows come back newest first. Page with `offset`; when the history runs
    # past data-api's offset ceiling, re-anchor with `end=<oldest seen>` and
    # keep going. `start`/`end` are inclusive, so the boundary row repeats and
    # is de-duplicated downstream, never here.
    end: int | None = None
    offset, page, guard = 0, 0, 0
    while True:
        guard += 1
        if guard > 200:
            log.warning("stopping trades for %s after %d pages", m.market_id, guard)
            break
        params = {"market": m.condition_id, "limit": cfg["trades_page_size"], "offset": offset}
        if watermark is not None:
            params["start"] = max(0, watermark - cfg["watermark_overlap_seconds"])
        if end is not None:
            params["end"] = end
        resp = data.get("/trades", params)
        page += 1
        archive.write(SOURCE, "trades", f"{m.market_id}_p{page}", resp)
        stats.trades_pages += 1
        rows = resp.json()
        if not isinstance(rows, list):
            break
        stats.trades_rows += len(rows)
        for t in rows:
            ts = int(t["timestamp"])
            if newest_seen is None or ts > newest_seen:
                newest_seen = ts
        if len(rows) < cfg["trades_page_size"]:
            break
        offset += cfg["trades_page_size"]
        if offset >= cfg["max_offset"]:
            end = min(int(t["timestamp"]) for t in rows)
            offset = 0

    if newest_seen is not None:
        st["watermark_ts"] = newest_seen
    if m.closed:
        st["closed_complete"] = True
    state.save()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    ap.add_argument("--config", type=Path, default=PROJECT_ROOT / "config" / "targets.yml")
    ap.add_argument("--dry-run", action="store_true", help="discover and match only")
    ap.add_argument("--scope", choices=("nebraska", "all"),
                    help="override the configured scope; `all` is every college football market")
    ap.add_argument("--backfill-since", metavar="YYYY-MM-DD",
                    help="sweep closed game events back to this date instead of the "
                         "current season; expensive, run by hand, not on a schedule")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)
    setup_logging(args.log_level)

    cfg = load_yaml(args.config)["polymarket"]
    if args.backfill_since:
        cfg = dict(cfg, earliest_start_date=args.backfill_since)
    if args.scope:
        cfg = dict(cfg, scope=args.scope)
    matcher = Matcher.from_config(cfg["match"], cfg["football"], cfg.get("scope", "nebraska"))
    interval = cfg["min_seconds_between_requests"]
    gamma = Client(cfg["gamma_url"], min_interval=interval)
    data = Client(cfg["data_url"], min_interval=interval)
    archive = RawArchive(args.data_dir / "raw")
    state = State(args.data_dir / "state" / "polymarket.json")
    stats = RunStats()
    try:
        matched = discover(gamma, archive, cfg, matcher, stats)
        log.info("discovery: %d pages, %d events seen, %d events matched, %d markets",
                 stats.discovery_pages, stats.events_seen, stats.events_matched,
                 stats.markets_matched)
        if not args.dry_run:
            for i, m in enumerate(sorted(matched, key=lambda x: (x.event_slug, x.market_id)), 1):
                log.info("[%d/%d] %s %s (%s)", i, len(matched), m.event_slug,
                         m.market_id, m.reason)
                collect_market(gamma, data, archive, cfg, state, m, stats)
    finally:
        stats.requests = gamma.requests_made + data.requests_made
        stats.retries = gamma.retries + data.retries
        gamma.close()
        data.close()
        stats.files = archive.files_written
        stats.bytes = archive.bytes_written

    print(json.dumps({
        "discovery_pages": stats.discovery_pages,
        "events_seen": stats.events_seen,
        "events_matched": stats.events_matched,
        "markets_matched": stats.markets_matched,
        "metadata_captured": stats.metadata_captured,
        "markets_without_condition_id": stats.no_condition_id,
        "trades_pages": stats.trades_pages,
        "trades_rows_fetched": stats.trades_rows,
        "closed_skipped": stats.skipped_closed,
        "untraded_skipped": stats.skipped_no_volume,
        "scope": cfg.get("scope", "nebraska"),
        "http_requests": stats.requests,
        "http_retries": stats.retries,
        "raw_files_written": stats.files,
        "raw_bytes_written": stats.bytes,
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
