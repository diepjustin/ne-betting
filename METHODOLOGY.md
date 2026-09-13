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
- **2026-09-08, Polymarket.** **Trader identity is redacted from everything
  committed to this repo.** A Polymarket trade row names the person who made
  it: `proxyWallet` is their on-chain address, and `transactionHash` resolves
  to that same address on any block explorer, so removing one without the
  other removes nothing. Both are replaced with stable placeholders
  (`redacted-trader-001`, `redacted-tx-001`), numbered by first appearance, and
  `name`, `pseudonym`, `bio` and the avatar fields are nulled. No mapping back
  to the real values exists anywhere in the repo. Plan §9 is the reason: a
  wallet address is a person's trading history, and committing a sample day to
  a public repository publishes it.

  What is deliberately *not* redacted is the protocol infrastructure that
  appears in market metadata, because none of it identifies a person:
  `assetAddress` is a single address across 921 occurrences (USDC on Polygon),
  `resolvedBy` is nine oracle and adapter contracts, `submitted_by` is six
  Polymarket accounts that create markets, and `marketMakerAddress` is one
  automated market maker contract per market. Redacting those would make it
  impossible to check which oracle resolved a market and would protect nobody.

  Analysis still works on the redacted files. Sizes, prices, timestamps,
  outcomes and the count of distinct traders all survive, so the sample still
  shows 93 traders and $12,734.96 of notional on the Ohio game. The
  unredacted responses remain in the local raw archive, which is gitignored,
  so any figure can still be traced to the exact response that produced it.
- **2026-09-08.** Metadata for each matched market is fetched from
  `/markets/{ticker}` on every run it is active, even though the discovery
  pages already contain it, so every normalized row can point at one small
  raw file rather than a multi-megabyte discovery page. **Correction,
  13 Sep 2026: this is not what the loader actually does for a market
  discovered before it traded.** `load_markets_from_discovery` runs first
  and inserts from the (large) discovery page; when the per-market fetch's
  own row lands afterward, its `ON CONFLICT` clause
  (`normalize/load.py`, `load_kalshi`/`load_polymarket`) only refreshes
  `status`, `settled_outcome` and `close_ts` -- not `raw_path`. A market
  seen in a discovery page first keeps pointing at that discovery page for
  its whole life, even once its own small per-market file exists. The
  traceability invariant still holds -- every row names *a* raw file that
  produced it -- but not always the small one this paragraph originally
  claimed, and not for the reason given. Left as a documentation fix rather
  than a code change: changing the `ON CONFLICT` clause would need every
  row rebuilt from raw to take effect, and nothing here is wrong enough to
  justify that against a live 8.6 GB store.

## Normalization decisions

- **2026-09-08.** The store is derived. `data/husker.db` is deleted and
  rebuilt from the raw archive on every load, so a mapping bug is fixed by
  changing the code and re-running, never by editing data. Every row records
  the raw file it came from.
- **2026-09-08.** A game is identified as `<date>-NEB-<opponent>`. Kalshi
  encodes it in the ticker (`26SEP05OHIONEB`), Polymarket in the event slug
  (`cfb-ohio-nebr-2026-09-05`). Polymarket's 2024 slugs carry no date, so the
  date comes from the market's own `gameStartTime`. Five 2024-era markets
  still resolve to no game because their slugs use hyphenated team names that
  cannot be split unambiguously; they are left unmapped rather than guessed.
- **2026-09-08.** A gamma market response carries no `events` key, and a
  spread market's own slug is not its game's, so the event a market belongs to
  is recovered from the event listings already in the archive.
- **2026-09-08.** Polymarket markets created before the platform tagged a
  `sportsMarketType` phrase the same wagers as plain questions ("Will Nebraska
  beat Colorado?", "Will there be 49 or more combined points scored?"). Those
  phrasings are matched explicitly. Anything unmatched lands in `unresolved`
  with its text; the loader prints the count and the current count is zero.
- **2026-09-08.** **Kalshi files coach awards in the same series as player
  awards.** The Eddie Robinson Award market names Matt Rhule and would
  otherwise have been counted as a market on a player. The award named in the
  market's rules is the only thing separating a head coach from a
  20-year-old, so coach awards are listed explicitly and the list grows only
  when such a market is actually seen.
- **2026-09-08.** Polymarket trade ids are synthesised as a hash of the row,
  with an ordinal for exact repeats, because the platform serves no id and
  transaction hashes are not unique. Rows carry `id_is_synthetic` so a
  synthesised key is never mistaken for a real one.
- **2026-09-08.** The per-player breakdown names athletes and is written to
  `data/analysis/`, which is gitignored. Plan §9 keeps published figures
  aggregate; naming players is for reporting, and publication is the editor's
  call.
- **2026-09-12.** The ordinal on a synthesised Polymarket trade id is now
  scoped to one response. It had been scoped to the whole load, so the same
  fill served in two overlapping fetches -- the 60-second watermark overlap,
  and the inclusive `end=` boundary row on every re-anchor -- was given a
  fresh ordinal and stored twice, which is the opposite of what the collector
  promises when it says duplicates are removed downstream. On the archive as
  loaded that was 27 rows and $4,272 of $311m -- every one of the 27 a row
  repeated across two raw files, none a repeat inside one response, checked
  by joining each `#2` key to its base and comparing `raw_path`. Small only
  because the archive came from one pass; every scheduled run would have
  added another overlap window. A repeat inside one response is still two
  fills. `tests/test_load.py` pins both cases.

## Widening past Nebraska

- **2026-09-08.** Both collectors take a `scope`: `nebraska` keeps only markets
  naming Nebraska, `all` keeps every market in the college-football series
  walked. The football gate applies in both, so widening the scope never
  admits college basketball. The configured default stays `nebraska`, and the
  scheduled job is deliberately not switched.
- **2026-09-08.** A market that has never traded has no trades to fetch, so
  anything with zero volume is recorded as seen and skipped. Its metadata is
  not lost: the discovery page carrying it is archived, and the normalizer now
  reads markets from those pages. Measured on the live exchange, this is the
  difference between roughly 7.7 and 12.5 hours for one full Kalshi pass.
- **2026-09-08.** Games are identified as `<date>-<away>-<home>` using
  canonical abbreviations, so a Kalshi ticker and a Polymarket slug for the
  same game produce the same identifier and can be joined. This replaced a
  Nebraska-only scheme. On the archive as it stands, 419 games are present on
  both platforms.
- **2026-09-08.** Kalshi runs the two team codes together with no separator
  (`OHIONEB`), so the split is only knowable from the vocabulary of codes
  actually observed. That vocabulary is learned from the archive and stored in
  `data/reference/kalshi_team_codes.json.gz`, and it deliberately excludes the
  codes Kalshi reuses across divisions. A blob that splits more than one way
  resolves to no game rather than the wrong one.
- **2026-09-08.** Polymarket slug abbreviations (`nebr`) are not school names,
  so they get their own learned vocabulary in
  `data/reference/polymarket_team_codes.json.gz`. Six of 406 codes were
  refused because no single reading dominated.

## Where the run's own outputs live

- **2026-09-08.** Collector watermarks moved from a daily git commit into the
  Actions cache. At the Nebraska scope the file is 130 KB; at the wide scope
  it tracks 22,564 markets, and a file that size rewritten by a commit every
  day is how a repository's history gets heavy. This one has already been
  cleaned once.

  The cache key carries the run id so each run writes a fresh entry, and a
  prefix restore-key picks up the newest previous one. State is saved even
  when a collector fails, because the watermarks it did advance are still
  correct and re-fetching them costs hours at the wide scope.

  **Losing the cache is survivable, not a data gap.** GitHub evicts entries
  untouched for a week, so a job left disabled that long starts cold; the
  collectors then refetch from the beginning of each platform's live window,
  which is slow rather than lossy, and the run log says which happened. The
  job now needs no write access to the repository at all.

## Which school, continued

- **2026-09-08.** The ticker suffix is only shortened for the families that
  append a player code. Shortening everything invented teams: `NWIA` is
  Northwestern (Iowa), an NAIA school, and trimming it to `NW` made it
  Northwestern of the Big Ten; `TCHA` became `TUSC` and `MCRA` became `MRHO`
  the same way. A suffix that is not a known code now names no team.
- **2026-09-08.** Kalshi market titles name their own team in a few shapes
  ("Kansas St. wins by over 9.5 points", "Nebraska: 275+ total yards"), which
  is the only way the reused-code schools can be named at all, since they are
  deliberately absent from the code vocabulary.
- **2026-09-08.** `away_team` and `home_team` are resolved at load time rather
  than by splitting `game_id` on hyphens, because real abbreviations contain
  one: `M-OH`, `W&M`, `TA&M`. The date is a fixed width and the split is taken
  at the separator that leaves two known abbreviations.
- The per-school view counts a market once for a school whether the market
  names one team or two. 310 schools have markets; the schools that the old
  code erased now carry real volume.

## What a cost figure means

- **2026-09-08.** Kalshi serves a Yes price and a No price on every trade, and
  `cost_usd` multiplies the Yes price by the count whichever side took it. That
  is a fine definition and a wrong answer to "what did the taker pay": 853,418
  of the Kalshi trades collected were taken by the No side, and the export
  column was labelled `taker_cost_usd`. Both are now stored. Summed across the
  archive the difference is $69.7m against $78.9m, so a "money bet" figure
  built on the old column understated the No side by about 13%.
- Polymarket prices the side actually bought, so its taker cost and quoted cost
  are the same number.

## Which school a market belongs to

- **2026-09-08.** Game identity now comes from the event title's team names,
  with the ticker's codes as a fallback and a check. This reverses an earlier
  decision to split the ticker's team blob, which was safe but lossy: Kalshi
  reuses short codes across divisions, so `WSU` is Winona State in
  `26AUG27WSUUST` ("Winona State Warriors vs St. Thomas") and Washington State
  in `25DEC22WSUUSU` ("Washington St. at Utah St."). Splitting refused both
  rather than guess, which erased 140 Kansas State, 212 Washington State, 210
  Colorado State, 245 Weber State and 121 Lane markets from any per-school
  view. Each would have appeared as a real program with almost no betting on
  it, which is the kind of wrong number that looks plausible.
- **2026-09-08.** Titles on non-game series append what the market measures
  after a colon ("Kansas St. at Arizona: Spread"); everything from the colon on
  describes the market, not the school. Both "A vs B" and "A at B" put the
  first-named school first, matching the order in the ticker.
- Widening the series list without widening the market-type map would have
  typed all 43 new series `other`, flooding `unresolved` and making the wider
  scope look emptier than the narrow one. A test now fails if a configured
  series has no type.
- After both fixes, Kalshi game markets without a game identifier fell from
  over 1,050 to 125 of 16,090, and all five reused-code schools resolve fully.

## Surviving a long pass

- **2026-09-08.** Network errors now get a budget measured in elapsed time
  (20 minutes by default) rather than sharing the six-attempt limit used for
  HTTP errors. A wide pass that had run 2.9 hours died at market 6,453 of
  22,564 because the machine briefly lost DNS resolution and six attempts
  across about a minute were not enough to wait it out. A remote 429 means
  slow down; a local resolver failure means wait, because nothing else is
  reachable either.
- **2026-09-08.** A single market whose retries run out no longer ends the
  run. It is logged, counted, and named in the run summary, and its watermark
  is left untouched so the next pass picks it up. A silent gap would be worse
  than a slow one.

## What the scheduled job publishes

- **2026-09-08.** The job uploaded `data/raw` as a 90-day workflow artifact.
  On a public repository any GitHub account can download one, and raw
  Polymarket trades carry `proxyWallet`, `name` and `pseudonym`, so that
  artifact was a compiled per-market listing of who traded. It contradicted the
  redaction promise above: an artifact is not a commit, but it is a
  publication. The job now stages a separate redacted copy
  (`scripts/redact_for_upload.py`) and uploads only that. The raw archive is
  append-only and is not rewritten; unredacted responses stay local, which is
  where reporting works from. Kalshi files are copied byte for byte, because
  the exchange publishes no identity for the parties to a trade.

## Looking for a hedge, not a bet

`analysis/anomalies.py`, added 9 Sep 2026.

A coach's contract pays bonuses at season milestones. A Kalshi contract
settles at $1. So a party who owes those bonuses can buy a number of
contracts equal to the money owed and be covered if the team wins. On
13 Aug 2026, four block trades on LSU milestone markets executed in 72
seconds: 2,462,500 contracts bought for $597,550, reported by CBS and InGame
as exactly that. Those trades are in this project's archive, and the detector
was written to find them.

**It uses trading evidence only.** No coach's contract is consulted, because
this project holds none. `config/milestones.yml` carries an empty
`bonus_tiers` table with the shape a cited entry would take; until a contract
document is in hand, no trade is matched to a bonus figure. Plan §7: a tier
invented to make a match is a fabricated number in a story.

**What counts as a finding.** One school, several different rungs of the
season ladder, inside one window, bought at prices that are not a sure thing.
Everything else ladder-shaped is filed as a lead carrying the reason it was
set aside. The pattern that makes the LSU trades legible is not their size,
it is that they walk the ladder in one go.

**Direction and price decide it, and they were nearly missed.** A bonus hedge
is a Yes purchase: the person owes money when the team succeeds, so they buy
the success. Reviewing the first version turned up the false positive that
would have been published. Of the 73 orders clearing the size floors on
ladder markets, 62 are No takers and 39 of those paid 95 cents or more per
contract -- parking cash for a 1-to-5% return on a team that will not win.
That is a yield trade, and a cash-parker doing it across three rungs of one
school in fifteen minutes has the exact shape of a hedge. A cluster is now
set aside when it is mostly a No purchase or when the taker paid near
certainty, with the reason recorded in the row.

**Fills are not orders.** Kalshi returns fills and publishes no order id: one
taker order crossing several resting orders comes back as several rows
sharing a timestamp. Applying a size floor per fill discards any hedge routed
through the book rather than negotiated as a block -- six such orders on
ladder markets already clear the floors only once their fills are combined.
Fills sharing a market, a timestamp and a taker side are treated as one
order. That is a proxy, not an order id, because the feed does not carry one.

**A flagged block skips the floors.** South Carolina's exchange-flagged
40,000 contracts at $0.11 cost $4,400 and fell under the cost floor. Kalshi's
own flag is better evidence than our threshold.

**Clusters group on the school, not on the side.** `taker_side` names
whoever crossed the spread, so a hedger who rests a bid and waits to be hit
is the maker and their fill carries the opposite side. Splitting clusters on
the side would cut such a ladder in half. The Yes/No split is reported
instead, with a flag when a cluster is mixed.

**Why a trade must be big in two ways.** A contract floor alone is a bad
filter. The largest counts in this archive are penny sweeps of markets that
had already been decided: 400,139 contracts at $0.01 is $4,001, not a whale.
A trade must clear a floor in contracts *and* in what the taker paid.

**Why the thresholds are what they are.** `analysis/calibrate.py`, added
9 Sep 2026, sweeps window, contract floor and cost floor together and prints
which settings still find the LSU hedge and how much else each drags in. It
exists because the first justification for these numbers was a set of SQL
queries typed into a shell, which nobody could re-run or check.

Running it corrected that justification. **The cost floor is doing the work
and the contract floor is close to inert.** Once any cost floor of $1,000 or
more is applied, every contract floor from 1,000 to 50,000 returns the same
single event, at every window from five minutes to an hour. The original
sweep varied only the contract floor and credited it with an effect that
belongs to the other knob. What a contract floor does do is silently drop a
*small* rung: a ladder whose lowest rung is 3,000 contracts loses that rung
at a 5,000 floor, falls to one rung, and is reclassified from a finding to a
lead without anything appearing to fail. A $25,000 bonus rung bought at ten
cents is 25,000 contracts costing $2,500, and today's floors would not see
it.

**Re-derived 10 Sep 2026, after the wide historical pass.** Every rung now
has traded markets, including the 68 title-game markets and 13 of the 19
conference-title series, so the gap that made the first numbers provisional
is closed. On 605,925 ladder orders drawn from 21.9 million trades, the
existing settings -- a 15-minute gap, 5,000 contracts, $5,000 -- still return
exactly one event, and it is LSU. They were not changed, because a threshold
that returns the one case an outside source confirms and nothing else is not
improved by moving it.

**The contract floor is not merely inert, it is arithmetically redundant.**
A Kalshi contract cannot cost more than $1, so an order of fewer than 5,000
contracts cannot cost $5,000. Any contract floor at or below 5,000 is
therefore invisible behind the cost floor: the sweep keeps an identical 1,628
orders at floors of 1,000 and 5,000. It is retained only because it does bite
once the cost floor is lowered, and lowering the cost floor is the only way
to reach a smaller hedge.

**What a smaller hedge would cost to look for.** Dropping the cost floor to
$1,000 takes the count from 1 event to 22 at a 15-minute gap, and to 57 at a
day. Those are not noise by construction -- they have already survived the
direction and price tests -- but they are a review queue rather than a set of
findings, and they should be read as one. `--min-taker-cost 1000` runs it.

**A wide window destroys the case rather than widening it.** At a full day
and no cost floor the sweep stops finding LSU altogether: the ladder absorbs
neighbouring orders until the cluster is mixed-side, and the grading sets it
aside. That is the strongest argument against reaching for a longer window to
catch a hedge spread over days. A per-day, per-rung roll-up is the way to do
that; a wider window is not.

**One confirmed case is not a calibration.** There is exactly one hedge in
the record that an outside source confirms. One true positive cannot support
a false-negative rate or a claim that a setting is optimal; it supports only
"which settings still find the case we can check, and how much else comes
with them." The tool prints the quietest setting that still finds LSU and
then says in the same breath that quietest is not best, because a floor tight
enough to return one event also drops every hedge smaller than the one case
we happen to know about.

**What the takers on ladder markets actually pay**, and the grounds for the
near-certainty cut. Of 605,925 ladder orders in the full archive, 11,888 were
priced at 95 cents or more per contract and 10,618 of those were No takers,
carrying $7.5m of taker cost. Cash-parking is not a handful of trades to be
explained away; it is a standing feature of these markets and the single
likeliest thing to be mistaken for a hedge.

**The window is a gap, not a span.** `--window-minutes` bounds the time
between one order and the next, so a long chain of closely spaced orders is
one cluster however long it runs. `span_seconds` in the output says how long
that was. At the default the longest lead spans nine minutes; widening the
window to days turns ordinary week-long accumulation into "events", which is
why a wider window is not the way to catch a hedge spread over days. A
per-day, per-rung roll-up would be.

**Roundness is recorded and not used.** Every LSU count is a multiple of
2,500, which is suggestive. It is also unremarkable: 101 of the 266 non-block
Kalshi trades over 50,000 contracts are multiples of 2,500, because round
order sizes are ordinary. The column is in the output as an attribute of a
cluster, never as a filter.

**Clusters are found by gap, not by clock.** A fixed 15-minute bucket splits
any cluster that happens to straddle a boundary, so consecutive trades are
grouped while each gap between them is short.

**Percentiles need a distribution.** In a market with five trades the largest
is trivially the highest, so no percentile is reported below 30 trades in the
market; the cell reads "market too thin" instead of carrying a number that
only reflects thinness. 310 of 12,786 large trades fall in that class.

**Days are Central.** A 6:30pm Central kickoff is 23:30 UTC, so UTC dates cut
games in half. **`analysis/breakdown.py`'s `daily_volume` view bucketed by a
plain SQL `DATE(executed_ts, 'unixepoch')` until 13 Sep 2026** -- a UTC date,
the exact bug this paragraph exists to warn against, sitting in the other
analysis module the whole time. It now imports `central_day` from this
module and buckets in Python instead of SQL, because SQLite has no named
timezone support and a fixed UTC offset would be wrong half the year across
the daylight-saving boundary. The daily timeline is dated in America/Chicago and carries the
UTC dates alongside so a figure can still be checked against a UTC-stamped
source. Two platforms serve time differently and the store keeps both
verbatim: Kalshi sends RFC 3339 to the microsecond, Polymarket sends a
whole-second unix epoch. The output renders the epoch as UTC rather than
inventing precision Polymarket did not send.

**A timestamp is not a key.** One market in this archive has 24 distinct
trades stamped to the same microsecond, where a single taker order swept the
book at settlement. That is real trading, not a loading error, and it is why
rank within a market is computed with a window function rather than by
joining a trade to its own market on the time it executed.

**A per-school figure is not a share of a total.** A game market names two
schools and is counted under both, because "money traded on games involving
Ole Miss" is the question a per-school row answers. Sum those rows and every
game market is counted twice: on 7 Sep 2026 SMU and Florida State each show
about $19.9m and it is the same $19.9m. Adding the school rows gives $573m on
Polymarket against a real $310,970,572, and $475m on Kalshi against
$241,145,893 -- inflation of 1.84x and 1.97x. The timeline therefore ships a
companion totals table that never joins to a school, and every per-school row
carries a `shared_trades` count so the overlap is visible in the data rather
than only in this paragraph.

Collapsing a school to one row per market is part of that. A spread names its
school in `team` and again as one side of the game, so a school counted from
both columns nearly doubles its own total. This is the same shape as the
attribution bug that once made Nebraska look like 87% of all college football
money, and it now has a test.

**Which markets are rungs.** Bowl selection, conference qualification,
conference title, playoff berth, quarterfinal, semifinal, title game,
national title. Bowl selection is in because Kalshi's market reads "selected
to play in a bowl game or the College Football Playoff", which is bowl
eligibility and the rung a mid-tier program is likeliest to be paid on -- a
ladder scoped to playoff markets would be blind to it. Deliberately out, with
the reasoning in `config/milestones.yml` so it is not re-litigated: seed
number, undefeated season, win totals, single games, and the two markets that
ask which *conference* the champion comes from, which is nobody's bonus.

**Season win totals are a rung, added 10 Sep 2026.** A win-total bonus is
among the commonest in a coaching contract, and `KXNCAAFWINS` asks "Will West
Virginia win at least 8 games this season?" Every strike maps to the one
`season_wins` rung, so a hedger buying the 8-win and 9-win markets is one
rung and cannot inflate a rung count. The four conference win series are not
rungs and the titles are why: `KXNCAAFSECWINS` and its siblings ask whether
at least three teams in a conference win ten games, which is a league-level
count, and `KXNCAAFH2HWINS` compares two schools' win totals to each other.
Those were read off the archived titles; going by the ticker names alone
would have added all four.

**The detector cannot tell an unwind from a hedge, and says so.** Kalshi
publishes no party identity. South Carolina, 15 July 2026, has four
exchange-flagged block trades: 40,000 Yes on playoff qualification, then
eighty minutes later 40,000 No at the same price plus 50,000 more No, then
100,000 Yes on the 8-win market nine seconds after that. Read one way that is
a bonus hedge rotating from one rung to another. Read another way it is a
position being closed. Nothing in the feed decides it. `offsetting_markets`
counts the markets a cluster hit from both sides, which is the only
mechanical trace a closed-out position leaves; a cluster carrying one is
still reported as a finding, with a line telling a person to read it rather
than quote it. This limit applies to every finding, including LSU's -- which
happens to have no offsetting market at all, five separate markets bought
from one side.

**Named athletes carry no dollar figure here.** The outsized-trade view
excludes player and draft markets. Those markets are titled with an athlete's
name, this view exists to chase school-level milestone trading, and plan §9
keeps a dollar figure away from a named 20-year-old. `analysis/breakdown.py`
holds the one view that names players and it carries the editor warning.

**What it currently finds.** Across 21.9 million trade rows: nine block
trades, two ladder events, 725 leads. The events are LSU's five-rung ladder
and a two-rung South Carolina cluster that carries the offsetting flag. Of
the leads, 717 are a single rung; the other eight are multi-rung clusters set
aside on direction or price, and without those two tests this run would
report nine findings where one belongs.

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
- Committed Polymarket samples carry redacted trader identifiers, so they
  cannot be used to study individual trading behaviour. That analysis needs
  the local raw archive.
- Polymarket coverage before 2026 relies on the platform's search index.
- polymarket.com tells US visitors to trade on polymarket.us instead, and
  polymarket.us is a separate product on its own backend (RECON §2, resolved
  10 Sep 2026): its own market and outcome ids, no `conditionId`, a distinct
  gateway. Its fills do not appear in the feeds collected here. Any claim
  about Nebraskans trading on Polymarket therefore covers polymarket.com
  only; a polymarket.us collector has not been scoped.
- About 1,050 game markets carry no game identifier, mostly older events whose
  slugs or tickers use a code that names more than one school. They are left
  unidentified rather than guessed.
- The daily scheduled job runs the Nebraska scope. A second, weekly job
  (`collect-wide.yml`, added 12 Sep 2026) runs `--scope all` -- weekly is
  this project's own response to what running the wide scope actually
  measured (13,892 non-finalized Kalshi markets, ~101x Nebraska's), not
  what SCOPE-all-cfb.md predicted; that doc expected daily increments to
  stay small and recommended only the first pass be run by hand, written
  before the series list was audited from 33 to 77. A season is 19 weeks
  and each run's artifact carries the same 90-day retention as the daily
  job's, so
  the earliest weekly snapshots will expire before the season does unless
  someone separately archives them. **Where the wide-scope archive lives
  past 90 days is still an open decision** -- the same one that gated
  switching this on in the first place, not resolved by adding the job,
  deliberately left open when it was built.
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
- **2026-09-08, first normalization run.** 493 markets and 94,568 trades
  loaded from the raw archive, 72 duplicate trade rows collapsed on their
  keys, zero markets unresolved. Of 24 named players with markets listed
  across both platforms, three have any trade at all, totalling 12 trades and
  668 contracts.
- **2026-09-08, series list re-audited for the wide scope.** The original 33
  series were chosen as "every series where Nebraska could plausibly appear",
  which was right for Nebraska and wrong once the scope widened. Checked
  against the full `/series` dump, 44 college-football series were missing,
  including every conference championship outside the Big Ten and
  `KXNCAAFFINALIST`. That last one matters: the four LSU block trades in our
  archive sum to $2,462,500, and the fifth rung of the $3,000,000 ladder the
  CBS story describes sits in the series we were not walking. The list is now
  77 series. The four basketball series that matched the audit filter were
  deliberately excluded.
- **2026-09-08, measured for all of college football.** Discovery costs the
  same 49 requests as the Nebraska scope, because those pages were always
  being walked. It finds 22,564 Kalshi markets, of which 13,791 have ever
  traded, carrying 824,128,445 contracts. A full first pass is roughly 7.7
  hours at one request a second. Nothing at that scale has been collected yet;
  where three gigabytes of raw archive lives is still undecided.
- **2026-09-08, Polymarket run at `--scope all`, logged 12 Sep 2026.** Run the
  same evening as the Kalshi measurement above but never written down until
  Justin asked, on 12 Sep, how much bigger the project would get at full
  college-football scope and the answer came from querying the database
  instead of a log entry that should already have existed. Recovered from
  `data/state/fullpass_polymarket.log`, 16:04:03-18:08:45 UTC. 48 discovery
  pages, 2,396 events seen -- identical to the Nebraska-scope run, because
  discovery walks the same pages regardless of scope -- of which 1,837
  matched and 20,881 of their markets were skipped as never-traded. 31,281
  markets matched, 10,115 with metadata captured. 20,974 requests in just
  over two hours, 7 retries. 10,804 trade pages, 1,281,759 trade rows, 150.8
  MB of gzipped raw in 20,967 files. Trades in the loaded archive now reach
  back to 21 Aug 2024, which the event-listing sweep alone does not reach for
  a non-Nebraska school -- `search_terms` in `config/targets.yml` are
  Nebraska-specific and would not have surfaced, say, an LSU game two seasons
  back -- so `--backfill-since` was almost certainly used for this pass,
  though the exact value was not captured in the log and is not recorded
  here as fact.
- **2026-09-09, hedging detector first run.** Over the 4,633,771 trade rows and
  53,845 markets loaded so far: seven trades carry Kalshi's block flag, three
  on South Carolina's playoff market in July and four on LSU's ladder in
  August. 12,786 trades clear both the 5,000-contract and $5,000 floors. One
  multi-rung cluster exists in the whole archive, and it is the LSU hedge:
  four rungs in 72 seconds, 2,462,500 contracts for $597,550. 49 single-rung
  clusters are filed as leads. No bonus tier was matched, because no coach's
  contract is on file. The Kalshi side of this is still the partial pass that
  stopped at market 6,453 of 22,564, and `KXNCAAFFINALIST` -- the fifth LSU
  rung -- is configured but not yet collected.
- **2026-09-09, before the wide historical pass.** Two defects would have made
  a twenty-hour run return almost none of what it was run for. The collector
  passed one watermark to both trade endpoints, so `/historical/trades` was
  asked for a window starting after the live watermark -- newer than every
  pre-cutoff row, and the January-to-July window the pass exists to reach.
  And 2,836 markets flagged `finalized_complete` by the live-only pass were
  skipped before the historical sweep could run, because a settled market
  cannot trade again. The historical side is now swept once per market and
  flagged rather than watermarked, a settled market still gets its sweep, and
  a settled market's frozen metadata is not re-fetched to do it. Three tests.
- **2026-09-10, wide historical pass.** 77 series, 188 discovery pages, 45,195
  markets -- twice the 22,564 the live-only walk finds, because `--historical`
  surfaces markets the live endpoint no longer lists. 90,615 requests at one a
  second over about 39 hours, 120 retries, no market given up on, 643 MB of
  gzipped raw in 90,495 files. 17,267,838 trade rows fetched. Every one of the
  2,836 markets the earlier pass had marked settled did get its pre-cutoff
  sweep, which is what the collector fix was for. Loaded: 75,533 markets and
  21,896,119 trades, 5,576 duplicate trade rows collapsed, 163 markets
  unresolved -- the same 163 as before, so widening the archive fivefold added
  none.
- **2026-09-10, the reference case is complete.** The LSU ladder now has its
  fifth rung: `KXNCAAFFINALIST-27-LSU`, 537,500 contracts at $0.12 for
  $64,500, block-flagged, timestamped 19:06:16.640392Z between the semifinal
  and the national title. The five rungs sum to **exactly 3,000,000
  contracts** for $662,050 across 72.3 seconds, which is the figure the CBS
  and InGame story reported. The earlier $2,462,500 was not a discrepancy in
  the reporting; it was a series missing from our own target list.
- **2026-09-10, a verb erased a school.** Three ladder markets carrying trades
  resolved to no school. Two are honest: a Big 12 championship market whose
  title names no team at all, and a Division III program that is not in the
  canonical team list. The third was ours -- "Will Kansas St. reach the
  College Football Playoff National Championship Game?" -- because the title
  verb list covered win, qualify, be, finish and go but not `reach`, and KSU
  is a reused code deliberately kept out of the ticker vocabulary. The verb
  list is now the set counted in the archive rather than the set imagined:
  `reach` appears in 50 titles and singular `record` in 21.
- **2026-09-10, the offsetting check was looking in the wrong window.** Added
  the same day and immediately wrong: it counted only markets a cluster hit
  from both sides *inside* the cluster, and South Carolina's opening 40,000
  Yes is eighty minutes before the No that reverses it, so only the last three
  trades fall in one 15-minute cluster and inside it the playoff market shows
  No takers alone. The check found nothing on the single case it was written
  for. It now looks a day either side of a cluster and counts only an order at
  least a quarter the size of what the cluster did in that market, because
  these markets carry constant small retail flow on both sides and a flag that
  fires on everything says nothing. South Carolina now reports 40,000
  offsetting contracts; LSU reports none, five markets bought from one side.
- **2026-09-10, second ladder event.** South Carolina, 15 July 2026: two rungs
  in 26 seconds, 190,000 contracts for $107,100, three of the four trades
  exchange-flagged as blocks. It is reported as a finding and printed with a
  line telling a reader to read it rather than quote it. **Not published on
  the page.** The LSU cluster is corroborated by outside reporting; this one
  rests entirely on our own reading of ambiguous evidence, and putting a
  school's name beside the phrase "coach bonus hedging" on evidence we have
  ourselves recorded as unresolvable would insinuate what it cannot support.
  The school, the timestamps and the reasoning stay here and in the CSVs.
- **2026-09-12, a weekly wide-scope job added alongside the daily one.**
  Justin asked how much bigger the project would get at all-of-college-
  football scope; the answer came from querying the archive the 8 Sep
  Polymarket pass and the 10 Sep Kalshi pass had already produced (see both
  entries above), not from a fresh estimate. That led to checking whether
  the *daily* job could simply be widened: at wide scope 13,892 of the
  22,564 markets Kalshi's live endpoint discovers are non-finalized on a
  given day, about 101x the Nebraska scope's ~137, and a live-side pass at
  that size is estimated at 7.7 hours -- past GitHub-hosted runners' ~6-hour
  job ceiling before a single retry. Daily wasn't viable at this scope.
  `collect-wide.yml` runs the same collectors at `--scope all` on a Tuesday
  weekly cron instead, sharing its state and its `concurrency.group` with
  the daily job so the two never race to save the same per-ticker
  watermarks. Its Kalshi step carries its own 200-minute timeout, separate
  from the job's 350-minute ceiling, specifically so a cut-off Kalshi run
  still leaves the job enough budget to save state and let Polymarket run
  -- without it, a job-level timeout could silently zero out a Tuesday's
  progress instead of banking it. The first several runs are expected not
  to finish Kalshi in one pass; each keeps whatever watermark progress it
  made and the next Tuesday continues from there. What this did not solve,
  on purpose: each
  run's artifact still carries 90-day retention, shorter than a 19-week
  season, so the earliest weekly snapshots expire before the season ends
  unless archived separately. See the Limitations entry above.
