# Methodology

Prose companion to the code. Every judgment call that affects a number gets a
dated line here the day it is made. Newest entries at the bottom of each
section.

## Sources

**Kalshi** — public REST API, `https://api.elections.kalshi.com/trade-api/v2`,
no authentication. Endpoints used: `/events` (with nested markets, for
discovery), `/markets/{ticker}` (metadata), `/markets/trades` (per-trade
history), and the `/historical/` mirrors of the last two for records older
than Kalshi's moving live/historical cutoff (three-month target window;
`2026-07-10` on 8 Sep 2026). Recon and saved responses: `docs/RECON.md`,
`data/sample/recon/`.

Kalshi publishes no rate limit for unauthenticated calls. The collector makes
one request a second, sequentially, and backs off exponentially on 429 or 5xx.
The User-Agent names the project and a contact.

**Polymarket** — public REST, no authentication, two hosts.
`gamma-api.polymarket.com/events` for discovery (the College Football series
12756 for games, tag 100351 for futures, `/public-search` for keyword
candidates), `gamma-api.polymarket.com/markets/{id}` for metadata, and
`data-api.polymarket.com/trades` for fills. Published Cloudflare limits are per
10 seconds; the collector runs at about three requests a second, roughly 15%
of the tightest of them.

**Sportsbook player props** — probed on 8 Sep 2026 and ruled out of scope the
same day; the project covers Kalshi and Polymarket only. Every book and DFS
API refuses automated access, the one open source is a curated weekly page
that did not carry Nebraska at all, and the paid feed that would reach the DFS
platforms is a plan §2 non-goal. Full reasoning in RECON "Phase 0b".

**Kalshi trade-data daily files** — the page exists but no file has been
retrieved yet (RECON §1). The API is the primary source until then.

## What a Kalshi row means

- Kalshi counts one Yes/No pair as one contract (exchange notice, 24 Dec 2021).
  Contract face value is $1. `count_fp` is contracts, fractional since 2026.
- `yes_price_dollars` × `count_fp` is what the Yes side paid; `count_fp` × $1
  is the payout to whichever side wins. Neither is "money at risk on Nebraska"
  without saying which side you mean.
- Exchange `volume_fp` on a market counts contracts traded, not dollars, and
  every trade has a Yes buyer and a No buyer. Do not add Yes-side and No-side
  markets of the same game and call it one number without saying so.

## What a Polymarket row means

- `size` is shares and `price` is dollars per share, so `size × price` is
  dollars paid and `size × $1` is the payout if that outcome wins.
- The trades feed defaults to `takerOnly=true`, which returns one row per
  taker fill. The same market pulled with `takerOnly=false` returned 739 rows
  where the default returned 326, because maker rows are included. This
  project keeps the default, so Polymarket rows are taker fills and are *not*
  double-counted the way exchange volume is. Kalshi's `volume_fp` and
  Polymarket's trade rows therefore do not mean the same thing and must never
  be added together without saying so.
- Polymarket volume figures on event and market objects are US dollars.
  Kalshi's `volume_fp` is contracts. Different units, same word.

## Collection decisions

- **2026-09-08.** Discovery walks a fixed list of 33 college-football series
  (`config/targets.yml`) rather than the whole exchange. The list is every
  series in Kalshi's college-football set where Nebraska could plausibly
  appear, read from the live series dump. A Nebraska market in a series not on
  the list would be missed; the series dump is re-checked when the list is
  revised.
- **2026-09-08.** A market is "about Nebraska" if its ticker contains a `NEB`
  segment after the series prefix, or if any of title, subtitle, yes/no
  sub-title, rules or parent event title contains "nebraska", "husker" or
  "matt rhule". Both sides of a Nebraska game match (the opponent's win market
  is a Nebraska market too). The reason each market matched is stored in
  `data/state/kalshi.json`. Award markets for Nebraska players match only
  through the `:: Nebraska` subtitle; that is the fragile edge.
- **2026-09-08.** Player-level markets (stat leaders, awards, Heisman) are
  collected because they are markets, and reported only as aggregate volume by
  market type. Nothing in this repo attaches a figure to a player's name.
- **2026-09-08.** The raw archive is append-only. Each response is stored as
  received inside an envelope that records fetch time, URL and HTTP status.
  Re-running a day writes new files; nothing is overwritten. Duplicate trades
  across overlapping fetches are resolved downstream on `trade_id`.
- **2026-09-08.** Trades are fetched from a per-market watermark (newest
  `created_time` seen) minus 60 seconds, so a re-run refetches only the tail.
  A market seen with status `finalized` has its metadata and trades fetched
  once more and is then skipped for good; Kalshi does not trade settled
  markets.
- **2026-09-08, Polymarket.** Nebraska's basketball teams use the same slug
  abbreviations as its football teams (`cbb-ill-nebr-2026-02-01` traded
  $503,345; `cbb-iowa-nebr-2026-03-26` is a $5.8m event). A first pass matched
  67 basketball events and would have put them in a "Nebraska football"
  number. Every one of the 1,624 college-football events seen carried tag
  `100351` and none of the 67 basketball events did, so an event must carry a
  football tag before anything else is considered. This is the single most
  important filter in the Polymarket collector.
- **2026-09-08, Polymarket.** Polymarket has used three spellings of Nebraska
  in slugs: `neb` in 2024-25 (`cfb-mich-neb-2025-09-20`), `nebr` in 2026
  (`cfb-ohio-nebr-2026-09-05`), and a longer form in 2024
  (`cfb-indiana-vs-nebraska`). A pattern covering only `nebr` missed five 2024
  and 2025 games, which matched by title only. All three are now matched.
- **2026-09-08, Polymarket.** For a game event every nested market counts,
  because every market on `cfb-ohio-nebr-2026-09-05` is about that game. For a
  league-wide futures event only the Nebraska rung counts: the Big Ten
  champion event has 22 markets and exactly one of them is Nebraska's.
- **2026-09-08, Polymarket.** **Polymarket trades have no unique id.** There
  is no `trade_id` field, and `transactionHash` is not unique: one transaction
  carried 18 separate fills in the sample, and a composite of every field on
  the row still collided once in 739 rows. The normalizer will therefore
  synthesise `source_trade_id` as a hash of the whole row plus an ordinal for
  exact repeats within a market, and two genuinely identical fills are
  indistinguishable in this feed. This is a real limitation, not a bug.
- **2026-09-08, Polymarket.** Market metadata is captured once, on first
  sight, because settled markets drop off the listing endpoints. Trades are
  paged with `offset`; when a market's history runs past the API's 10,000
  offset ceiling the collector re-anchors with `end=<oldest row seen>`.
  `start` and `end` are inclusive, verified against the live API, so boundary
  rows repeat and are de-duplicated downstream.
- **2026-09-08, Polymarket.** The daily run sweeps closed game events back to
  the start of the current season only. The College Football series carries
  every FBS game, about 1-3 MB per page of events we do not want, so three
  seasons would be tens of megabytes re-downloaded every run. Earlier seasons
  come from `/public-search` candidates and from a deliberate
  `--backfill-since`, run by hand. Coverage of 2024 and 2025 therefore depends
  on Polymarket's search index and is not guaranteed complete.
- **2026-09-08.** Metadata for each matched market is fetched from
  `/markets/{ticker}` on every run it is active, even though the discovery
  pages already contain it, so every normalized row can point at one small
  raw file rather than a multi-megabyte discovery page.

## Limitations

- Kalshi publishes no taker identity. Nothing here can say who traded.
- Kalshi's unauthenticated rate limit is unpublished; collection speed is a
  guess on the polite side.
- Pre-cutoff history (`--historical`) is walked only when asked, because it
  re-reads every settled college-football market on each run.
- Sportsbooks publish no per-game handle, so any book comparison is lines,
  not bets.
- Offshore and sweepstakes platforms are out of scope.
- Volume counts both sides of a transaction; notional volume is not dollars
  at risk.
- Polymarket trades have no unique identifier; see the 8 Sep 2026 note above.
- Polymarket coverage before 2026 relies on the platform's search index.
- polymarket.com tells US visitors to trade on polymarket.us instead. Whether
  polymarket.us fills appear in these public feeds is unconfirmed, and it
  matters for any claim about Nebraskans trading on Polymarket.
- No sportsbook or DFS prop lines are collected, by decision on 8 Sep 2026
  (RECON "Phase 0b"). Any claim about how many chances there were to bet on a
  Nebraska athlete at a sportsbook is therefore out of this project's reach;
  what it can support is how much money traded on Nebraska markets at Kalshi
  and Polymarket.
- Kalshi's Data Terms of Use (RECON §1, "Terms") restrict collection and
  republication of website data. This project's use of the documented public
  API for newsgathering has not been reviewed by counsel as of 8 Sep 2026.

## Collection log

- **2026-09-08, first Kalshi run.** 33 series, 49 discovery pages, 22,523
  markets seen, 228 matched (221 by ticker, 7 by text). 553 requests at one a
  second, no 429s, no retries. 66,355 trade rows archived in 553 raw files
  (4.1 MB gzipped). 90 of the 228 markets were already finalized.
- **2026-09-08, second run, ~4 h later.** Same 228 matched. 325 requests:
  discovery, metadata for the 138 active markets, and trade tails from each
  watermark. 103 new trade rows; the 90 finalized markets were skipped.
- **2026-09-08, first Polymarket run.** 48 discovery pages, 2,396 events seen,
  33 events matched, 265 markets. 597 requests, no retries. 28,182 trade rows
  in 597 raw files (20 MB gzipped). 246 of the 265 markets were already
  closed. A run before the football tag gate matched 100 events and 549
  markets; the 67 basketball events it wrongly included are the difference.
