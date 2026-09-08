# Phase 0 recon — Kalshi and Polymarket coverage of Nebraska football

Collected 8 Sep 2026 (UTC) from public, unauthenticated endpoints only. Every
claim below points at a saved response under `data/sample/recon/`. Nothing was
guessed: every ticker, slug, field name and figure here was read from a live
response or a docs page that is also saved.

Working directory note: the plan names the repo `husker-markets`; this work
lives in `ne-betting/` inside `diepjustin.github.io`. Nothing is committed yet.

---

## Summary: which §5 assumptions held

| # | Assumption in the plan | Verdict |
|---|---|---|
| 1 | Base URL `https://api.elections.kalshi.com/trade-api/v2` with `/series`, `/events`, `/markets`, `/markets/trades` | **Correct.** All four answer 200 with no auth. Docs also list `https://external-api.kalshi.com/trade-api/v2` as the primary production host. `trading-api.kalshi.com` returns 401; `api.kalshi.com` does not resolve. |
| 1 | Trades carry `price_cents` and integer `count` (schema sketch, §6) | **Wrong.** Trades now carry `yes_price_dollars` / `no_price_dollars` as decimal strings and `count_fp` as a decimal string (fractional contracts, e.g. `"274.72"`). Market volume is `volume_fp`. The schema needs to change; see §Schema below. |
| 2 | College football series exist; do not guess tickers | **Confirmed, 130 college-football series.** Game-level: `KXNCAAFGAME`, `KXNCAAFSPREAD`, `KXNCAAFTOTAL`. Season: `KXNCAAFWINS`, `KXNCAAFB10`, `KXNCAAFB10QUAL`, `KXNCAAFPLAYOFF`, `KXNCAAF`, `KXNCAAFSEED`, `KXNCAAFQF`, `KXNCAAFSF`. Full list in `kalshi/kalshi_cfb_series_list.txt`. |
| 3 | Kalshi has Nebraska events | **Yes, 225 Nebraska markets across 17 series** as of collection (135 active, 90 finalized). Title formats in §Kalshi title formats. |
| 4 | `kalshi.com/trade-data` offers bulk daily files | **Page exists, file URL unconfirmed.** The page has a date picker (max 2026-09-07, "updated during the subsequent trading day") and a "Download trade data" button. The button fired no network request in a logged-out session and I did not log in or download anything. The API's `/historical/trades` endpoint is a confirmed alternative for per-trade history. See §Kalshi trade-data page. |
| 5 | Polymarket gamma + trades endpoints, `proxyWallet`, history depth | **Confirmed.** `gamma-api.polymarket.com` for events/markets, `data-api.polymarket.com/trades` for trades. `proxyWallet` is present on every trade record. History reaches back at least to Oct 2024 for settled Nebraska games. |
| 6 | Rate limits and terms | Polymarket publishes per-endpoint IP limits. Kalshi publishes token budgets for *authenticated* keys only; the unauthenticated limit is **unconfirmed**. **Kalshi's Data Terms of Use are a real problem for this project; see §Terms.** |
| 7 | Player-level markets | **Yes, on both platforms, naming current Nebraska players.** See §Player markets. This is the loudest flag in this document. |

Two things the plan did not anticipate:

- **Kalshi partitions data into live and historical at a moving cutoff.** As of collection the cutoff is `2026-07-10T00:00:00Z` for trades and settled markets. Anything older is only reachable through `/historical/...` endpoints, and `/events?with_nested_markets=true` silently omits pre-cutoff settled markets. Target live window is three months. The collector has to query both sides.
- **Polymarket runs two sites.** `polymarket.com` shows US visitors "Trading is blocked in the United States… Switch to polymarket.us". `polymarket.us` is a separate CFTC-regulated product. Whether polymarket.us Nebraska trades appear in the gamma / data-api feeds is **unconfirmed** and matters for the story's premise that Polymarket takes Nebraska action from Nebraskans.

---

## 1. Kalshi API

**Base URL:** `https://api.elections.kalshi.com/trade-api/v2` (confirmed). Docs list `https://external-api.kalshi.com/trade-api/v2` as primary; both are "production". Docs index: `https://docs.kalshi.com/llms.txt` (saved in `docs/`).

**Auth:** none needed for `/series`, `/events`, `/markets`, `/markets/{ticker}`, `/markets/trades`, `/historical/*`. Confirmed by 200 responses with no headers, and by the docs page "Quick Start: Market Data … without authentication".

| Endpoint | Params confirmed from docs | Sample |
|---|---|---|
| `GET /series?limit=1000` | returned all 13,849 series in one response, no cursor | `kalshi/kalshi_series.json.gz` (16.6 MB raw) |
| `GET /series/{ticker}` | | `kalshi/kalshi_series_KXNCAAFGAME.json` |
| `GET /events?series_ticker=&limit=200&with_nested_markets=true&cursor=` | cursor pagination; game series need 6–7 pages | `kalshi/kalshi_events_<SERIES>_p<N>.json.gz` |
| `GET /events/{event_ticker}?with_nested_markets=true` | | `kalshi/kalshi_events_KXNCAAFGAME-26SEP05OHIONEB.json` |
| `GET /markets?…` | `limit` (max 1000), `cursor`, `event_ticker`, `series_ticker`, `status`, `tickers`, `min/max_created_ts`, `min/max_close_ts`, `min/max_settled_ts` | `kalshi/kalshi_markets_p1.json` |
| `GET /markets/{ticker}` | | `kalshi/kalshi_market_KXNCAAFB10-26-NEB.json` |
| `GET /markets/trades` | `ticker`, `min_ts`, `max_ts`, `limit` (default 100, max 1000), `cursor`, `is_block_trade` | `kalshi/kalshi_trades_KXNCAAFB10-26-NEB.json` (83 trades, 7 Jul → 8 Sep 2026) |
| `GET /historical/cutoff` | | `kalshi/kalshi_historical_cutoff.json` |
| `GET /historical/markets` | `limit`, `cursor`, `tickers`, `event_ticker`, `series_ticker` | `kalshi/kalshi_historical_markets_KXNCAAFB10-25.json` |
| `GET /historical/trades` | same as live trades | `kalshi/kalshi_historical_trades_KXNCAAFB10-25-NEB.json` (201 trades, May → Nov 2025) |

**Trade record fields (verbatim keys):** `trade_id`, `ticker`, `created_time` (RFC 3339 with microseconds), `count_fp`, `yes_price_dollars`, `no_price_dollars`, `taker_side`, `taker_outcome_side`, `taker_book_side`, `is_block_trade`.

**Market record fields worth keeping:** `ticker`, `event_ticker`, `title`, `yes_sub_title`, `no_sub_title`, `subtitle`, `rules_primary`, `rules_secondary`, `status` (`active` / `finalized` seen), `result`, `open_time`, `close_time`, `expiration_time`, `settlement_ts` (historical only), `volume_fp`, `volume_24h_fp`, `open_interest_fp`, `last_price_dollars`, `notional_value_dollars`, `market_type`, `strike_type`, `exchange_index`.

**Units:** since 24 Dec 2021 Kalshi counts a Yes/No pair as one contract (`docs/kalshi-volume-and-oi-changes.txt`). Contract face value is $1, so `count_fp × 1` is payout at settlement and `count_fp × yes_price_dollars` is the taker's cost when taking Yes.

**Live vs historical.** `GET /historical/cutoff` on 8 Sep 2026:

```json
{"market_positions_last_updated_ts":"2026-07-10T00:00:00Z","market_settled_ts":"2026-07-10T00:00:00Z","orders_updated_ts":"2026-07-10T00:00:00Z","trades_created_ts":"2026-07-10T00:00:00Z"}
```

Verified behaviour: `KXNCAAFB10-25` (2025 Big Ten champion) shows 0 nested markets on the live `/events` endpoint but 18 markets on `/historical/markets?event_ticker=KXNCAAFB10-25`, including `KXNCAAFB10-25-NEB` (finalized, result `no`, `volume_fp` 72,267). Its 201 trades came back from `/historical/trades?ticker=`. The 2025 season is therefore recoverable through the API. Trades for the 5 Sep 2026 Ohio game are on the live side (`/historical/trades` returns empty for them, as expected).

**Rate limits.** `docs/docs_rate_limits.md`: authenticated keys get a token bucket, Basic tier = 200 read tokens/s at 10 tokens per request (20 req/s), Advanced = 300. 429 returns `{"error":"too many requests"}` with no `Retry-After` header. **No published limit for unauthenticated requests.** The sweep in this recon made roughly 60 requests over a few minutes with 0.3 s sleeps and saw no 429 from the API. (The 429 I did see was `kalshi.com/trade-data`, a Vercel bot checkpoint on the website, not the API.) Plan: stay well under 1 req/s and back off on 429.

### Kalshi title formats (normalizer input)

Tickers embed the team code `NEB` and a game date; titles are short and team-relative.

| Series | Event ticker | Market ticker | `title` | `yes_sub_title` |
|---|---|---|---|---|
| `KXNCAAFGAME` | `KXNCAAFGAME-26SEP05OHIONEB` (event title "Ohio vs Nebraska") | `KXNCAAFGAME-26SEP05OHIONEB-NEB` | "Nebraska wins" | "Nebraska" |
| `KXNCAAFSPREAD` | `KXNCAAFSPREAD-26SEP12BGSUNEB` | `KXNCAAFSPREAD-26SEP12BGSUNEB-NEB49` | "Nebraska wins by over 48.5 points" | |
| `KXNCAAFTOTAL` | `KXNCAAFTOTAL-26SEP12BGSUNEB` | `KXNCAAFTOTAL-26SEP12BGSUNEB-33` | "Over 32.5 points scored" | |
| `KXNCAAFTEAMTOTAL` | | `KXNCAAFTEAMTOTAL-26SEP12BGSUNEB-NEB17` | "Nebraska scores over 16.5 points" | |
| `KXNCAAFTEAMYDS` | | `KXNCAAFTEAMYDS-26SEP05OHIONEB-NEB275` | "Nebraska: 275+ total yards" | |
| `KXNCAAFTEAMTD` | | `KXNCAAFTEAMTD-26SEP05OHIONEB-NEB4` | "Nebraska: 4+ touchdowns" | |
| `KXNCAAFWINS` | `KXNCAAFWINS-26NEB` | `KXNCAAFWINS-26NEB-6` | "Will Nebraska win at least 6 games this season?" | |
| `KXNCAAFB10` | `KXNCAAFB10-26` | `KXNCAAFB10-26-NEB` | "Will Nebraska win the College Football Big Ten Championship?" | "Nebraska" |
| `KXNCAAFB10QUAL` | `KXNCAAFB10QUAL-26` | `KXNCAAFB10QUAL-26-NEB` | "Will Nebraska qualify for the College Football Big Ten Championship Game?" | |
| `KXNCAAFBIGTENREGTOP` | | `KXNCAAFBIGTENREGTOP-27T5-NEB` | "Will Nebraska finish in the top 5 in the Big Ten in the 2026 college football regular season?" | |
| `KXNCAAFTOPAPRANK` | | `KXNCAAFTOPAPRANK-26W12T25-NEB` | "Will Nebraska be a top 25 ranked team on the College Football AP Poll Week 12 rankings?" | |
| `KXNCAAFAPANY` | | `KXNCAAFAPANY-27T25-NEB` | "Will the Nebraska college football team be ranked top 25 during any week of the 2026-27 season?" | |
| `KXNCAAFBOWLGAME` | | `KXNCAAFBOWLGAME-26-NEB` | "Nebraska selected to play in a bowl game or the College Football Playoff" | |
| `KXNCAAFTFUT` | | `KXNCAAFTFUT-30NEB-Y` | "Will Nebraska qualify for the CFP after Issuance and before the 2030-31 season?" | |
| `KXNCAAFCOACHOUT` | | `KXNCAAFCOACHOUT-27FEB01-MRHU` | "Matt Rhule out before Feb 1, 2027" | |
| `KXNCAAFAWARD` | | `KXNCAAFAWARD-26EDDI-MRHU` | "Matt Rhule wins the award" | |

Game-date code: `26SEP05OHIONEB` = 2026, Sep 05, away `OHIO`, home `NEB` (the ESPN schedule confirms Ohio at Nebraska on that date). Event-ticker team codes seen so far: `OHIO`, `BGSU`, `UND`. The normalizer should join on date first and use the code as a check, not the other way round.

**No Nebraska markets were found** in `KXNCAAFPLAYOFF` / `KXNCAAF` (national title, 50 markets, no Nebraska), `KXNCAAFSEED`, `KXNCAAFQF`, `KXNCAAFSF`, `KXNCAAFUNDEFEATED`, `KXHEISMAN` (37 names, none from Nebraska), `KXHEISMANFINALIST`. Nebraska simply is not listed in those this season. The full sweep result is `kalshi/kalshi_nebraska_markets_found.json` (series, event, ticker, title, sub-title, status, volume, open, close).

**Coverage numbers at collection** (Kalshi, `volume_fp`, contracts not dollars):

| | |
|---|---|
| Nebraska markets found | 225 across 17 series |
| Sum of `volume_fp` across them | 9,481,743 contracts |
| Of which the Ohio game winner pair | 6,359,506 (NEB side 1,279,667; OHIO side 5,079,838) |
| `KXNCAAFB10-26-NEB` (Big Ten champ) | 134,852 contracts; 83 trades since 7 Jul |
| `KXNCAAFWINS-26NEB-*` (win totals, 9 rungs) | 70,955 |

One observation, recorded because it is the kind of thing the anomaly detector is for, not because it means anything yet: the largest trade in `KXNCAAFB10-26-NEB` is 46,759 contracts at $0.01 Yes on 2026-08-10T23:34:35Z, taker Yes. Cost ≈ $468, payout if Nebraska wins the Big Ten $46,759. It is not flagged `is_block_trade`. Three more 8,000–10,000 contract Yes trades at $0.01 landed 7–9 Aug and 3 Sep.

### Kalshi trade-data page

`https://kalshi.com/trade-data` renders (title "Recent Trading Activity & Insights") with: a link "Notice regarding Open Interest and Volume units" (PDF, saved), the line "Data for any given day is updated during the subsequent trading day", a date input (max = yesterday), and a button `aria-label="Download trade data"`. Clicking it while logged out produced no request, no dialog and no file in the sandboxed browser. I did not create an account, log in, or download. **Open question for Justin:** download one day from your own browser, then send me the file and the URL from your browser's download list. That gives the file schema and the URL pattern in one go. Until then the API `/historical/trades` + `/markets/trades` pair is the confirmed route to per-trade history and covers the same trades.

Curl gets a 429 "Vercel Security Checkpoint" on that page; that is the website's bot gate, not the API, and I did not try to get around it.

### Terms

`https://kalshi-public-docs.s3.amazonaws.com/kalshi-data-terms-of-service.pdf` (linked from the site footer as "Data Terms of Service"; text extracted to `docs/kalshi-data-terms-of-service.txt`). Relevant language, quoted:

- "You may access content only for your personal use for non-commercial purposes."
- Prohibited without written authorization: "collecting, copying, reproducing, downloading … publishing … creating derivative works, electronically extracting or scrubbing, scraping, compiling (including … systematic retrieval to create collections, compilations, databases …) or conducting 'text and data mining'".
- "use of any … scripts, software, spiders, robots … to navigate, access, copy in bulk, retrieve, harvest, index, search or analyze any portion of the Website is strictly prohibited."
- Any use "for any machine learning and/or artificial intelligence" is "expressly prohibited".

These terms are written for the website; the API docs separately invite unauthenticated market-data access and the API served every request here without a key. Whether the Data Terms bind API use, and whether newsgathering changes the analysis, is a question for an editor and counsel before Phase 1 runs on a schedule. I am flagging it, not answering it.

Polymarket: `polymarket.com/tos` is JavaScript-rendered and its text did not extract; the saved HTML shows the US "view-only mode" notice: "Trading is unavailable in the locations below. Use of measures to circumvent … is prohibited." The API docs describe the market-data endpoints as public and publish rate limits for them. Terms text still to be read by a person.

---

## 2. Polymarket

**Endpoints (confirmed, no auth):**

| Endpoint | Notes | Sample |
|---|---|---|
| `GET https://gamma-api.polymarket.com/events?series_id=12756&closed=<bool>&limit=100&offset=<n>&order=startDate&ascending=false` | `12756` is the College Football series from `/sports` (`sport: "cfb"`, `primaryTagId: 100351`). `limit` above 100 still returned 100; page with `offset`. | `polymarket/polymarket_gamma_events_series12756_closed*_off*.json.gz`, `polymarket/polymarket_gamma_sports.json` |
| `GET /events?tag_id=100351&closed=false` | futures: conference winners, win totals, Heisman, coach-out | `polymarket/polymarket_gamma_events_cfb_tag100351_open.json.gz` |
| `GET /events?slug=<slug>` | exact-slug lookup | |
| `GET /markets/{id}` | full market metadata | `polymarket/polymarket_gamma_market_510126.json` |
| `GET /public-search?q=…&events_status=active&page=N` | keyword search; "Nebraska" alone surfaces election markets first, "Cornhuskers" surfaces football/basketball, "Huskers" surfaces cricket teams named Tuskers | `polymarket/polymarket_gamma_public-search_*.json` |
| `GET https://data-api.polymarket.com/trades?market=<conditionId>&limit=…&offset=…` | docs (`docs/docs_polymarket_get-trades-for-a-user-or-markets.md`): `limit` default 100 max 10000, `offset` max 10000, `takerOnly` default true, `market` (conditionId, comma list), `eventId`, `user`, `side`, `start`, `end`, `filterType`+`filterAmount` | `polymarket/polymarket_data_trades_ohio-nebr-2026.json` |

**Trade record fields (verbatim keys):** `proxyWallet`, `side` (BUY/SELL), `asset` (token id), `conditionId`, `size`, `price`, `timestamp` (epoch seconds), `title`, `slug`, `eventSlug`, `outcome`, `outcomeIndex`, `name`, `pseudonym`, `bio`, `profileImage`, `transactionHash`. Note `takerOnly=true` by default: the feed is one row per taker fill, so it is *not* double-counted the way exchange volume is.

**Market metadata fields the normalizer needs:** `question`, `slug`, `conditionId`, `clobTokenIds`, `outcomes`, `sportsMarketType`, `line`, `gameStartTime`, `startDate`, `endDate`, `closed`, `closedTime`, `volume`, `volumeClob`, `resolutionSource`. Events carry `gameId`, `teams[]`, `sport`, `eventWeek`, `startTime`, `score`.

**Slug pattern for games:** `cfb-<away>-<home>-<YYYY-MM-DD>` with Nebraska as `nebr`. Confirmed slugs: `cfb-ohio-nebr-2026-09-05`, `cfb-bowlgr-nebr-2026-09-12`, `cfb-ndak-nebr-2026-09-19`; 2025: `cfb-usc-nebr-2025-11-01`, `cfb-nebr-minnst-2025-10-18`, `cfb-iowa-nebr-2025-11-28`, `cfb-nebr-utah-2025-12-31`. 2024 games used a different style: `cfb-indiana-vs-nebraska`.

**Market question formats** (Ohio game, 148 markets): "Ohio vs. Nebraska" (`moneyline`), "Spread: Nebraska (-24.5)" (`spreads`, `line` -24.5), "O/U 47.5" or "Ohio vs. Nebraska: O/U 46.5" (`totals`), "Nebraska Team Total: O/U 28.5" (`team_totals`), "1H Spread: Nebraska (-11.5)", "Ohio vs. Nebraska: 2H Moneyline", `q1_spreads` … `q4_moneyline`, and "<Player>: Anytime Touchdown" (`anytime_touchdowns`). `sportsMarketType` is the reliable market-type key; parse `line` rather than the title.

**Futures with Nebraska (open):** `ncaa-football-2026-big-ten-conference-winner-…` (market 3249971 "Will Nebraska Cornhuskers Win the 2026 Big Ten Championship?", volume 52), `ncaa-football-team-win-totals-…` (market 3365406 "Will Nebraska have more than 6.5 wins…"), `ncaa-football-nebraska-2026-win-total` (5 rungs 4.5–8.5, volume 797), `ncaa-football-team-to-qualify-for-2026-big-ten-championship-game`.

**Coverage numbers at collection** (Polymarket `volume`, USD-denominated):

| Event | Volume | Markets |
|---|---|---|
| `cfb-ohio-nebr-2026-09-05` (closed) | 76,545 | 148 |
| `cfb-bowlgr-nebr-2026-09-12` (open) | 27 | 7 |
| `cfb-ndak-nebr-2026-09-19` (open) | none yet | 1 |
| Ohio moneyline trades via data-api | 326 fills, 93 wallets, 2–5 Sep 2026, largest single fill 5,000 shares | |

For contrast the same Ohio game on Kalshi shows 6.36 million contracts of volume on the winner market. The platforms are not remotely the same size on this game.

**History depth:** `data-api /trades?market=` returned 34 fills from 19 Oct 2024 for the 2024 Indiana–Nebraska game, all with `proxyWallet`. Settled markets stay retrievable on gamma (2024 events still resolve by id). The plan's worry that metadata disappears at settlement looks unfounded so far, but capture-on-first-sight is still the right design.

**Rate limits** (`docs/docs_polymarket_rate-limits.md`, Cloudflare IP-based, throttled not rejected): gamma general 4,000 req/10 s, `/events` 500/10 s, `/markets` 300/10 s, `/public-search` 350/10 s; data-api `/trades` 200/10 s.

**polymarket.us:** the site alternated between "Maintenance Mode" and a CFB games list for 10–12 Sep across three loads on 8 Sep; the games list never rendered a Nebraska row while I was looking. Whether polymarket.us games share the same gamma ids and whether their fills appear in `data-api` is **unconfirmed**. This is the single most important open question for the "Nebraskans are trading on Polymarket" thread, because US users are told to trade there.

---

## 3. Player markets — flag

Both platforms list markets on named, current Nebraska players.

Kalshi (`yes_sub_title` / ticker suffix shows the player):

- `KXNCAAFBIGTENLEADER-26SACK-NEBOCHA` "Owen Chambliss records the most sacks in the Big Ten"; `-NEBWNWA` Williams Nwaneri; `-26RSHYDS-NEBACOL` Anthony Colandrea (rushing yards).
- `KXNCAAFLEADER-26RECYDS-NEBNHUN` Nyziah Hunter; `-26PASSYDS-NEBACOL` and `-26PASSTD-NEBACOL` Anthony Colandrea.
- `KXNCAAFAWARD-26RIMI-JEVA` Justin Evans; `-26PAUL-JBAR` Jacory Barney Jr.
- Volume on all of these is 0 to 87 contracts.

Polymarket: the Ohio game carried 21 `anytime_touchdowns` markets, one per named player, including Nebraska players (Jacory Barney Jr., Kwazi Gilmer, Dom Dorwart and others). Volume on those rows was null or near zero in the saved response.

Per §9 of the plan: the collector may store these rows because they are markets, but the analysis stops at aggregate volume by `market_type = player_prop`, and nothing with a player name goes in the README or a story without the editor.

---

## 4. Schedule source

`https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams/158/schedule?season=2026` returns the 12-game 2026 Nebraska schedule (`espn/espn_nebraska_schedule_2026.json`). **ESPN returns 403 to the project's identifying User-Agent** and 200 to curl's default. I did not spoof a browser. For the collector: send curl's plain default UA to ESPN and note it in METHODOLOGY, or find the schedule elsewhere.

---

## 5. Schema corrections for §6

- `trade.price_cents INTEGER` → `yes_price_dollars TEXT` (keep the string exactly as served; derive numeric columns downstream) and add `no_price_dollars`.
- `trade.count INTEGER` → `count_fp TEXT`; Kalshi contracts are fractional.
- Add `taker_outcome_side`, `taker_book_side`, `is_block_trade` (Kalshi) and `side`, `outcome`, `outcome_index`, `asset` / `token_id`, `transaction_hash` (Polymarket).
- `market`: add `sports_market_type`, `line`, `game_start_time` (Polymarket) and `yes_sub_title`, `rules_primary`, `result`, `settlement_ts` (Kalshi).
- Kalshi `executed_ts` should keep microsecond precision (`created_time` string) alongside the integer seconds.
- Add a `source_partition` column on Kalshi rows: `live` or `historical`, recording which endpoint served the row.

---

## 6. Open items before Phase 1

1. **Kalshi Data Terms of Use** — editor / counsel read before scheduled collection.
2. **Kalshi unauthenticated rate limit** — unpublished; run at ≤1 req/s with backoff and log every 429.
3. **trade-data daily file** — Justin to download one day manually; then decide whether the file or the API is the primary source (the API is confirmed and covers pre-cutoff history).
4. **polymarket.us** — confirm whether its fills surface in `data-api`. Plan: watch the Bowling Green game (`cfb-bowlgr-nebr-2026-09-12`) on both sites during and after the game and compare fills.
5. **Team-code table** — Kalshi event tickers use codes (`OHIO`, `BGSU`, `UND`, `NEB`); Polymarket slugs use others (`ohio`, `bowlgr`, `ndak`, `nebr`). Build the mapping from the ESPN schedule as games appear; never guess a code.
6. Everything player-level stays in the raw archive and out of every summary except a count.

## 7. What is in `data/sample/recon/`

See `data/sample/recon/README.md` for the file-by-file inventory. Total ≈16 MB compressed; the four multi-megabyte files are gzipped. Nothing has been committed; decide whether the 3.5 MB closed-events dump stays in the sample or is regenerated.

---

# Phase 0b — sportsbook player props: probed, then ruled out of scope

Justin asked on 8 Sep 2026 whether we could also collect sportsbook player
props, modelled on Cat Murphy's IRE project (`catelizabethmurphy.github.io/ire26`),
and after seeing what follows **decided the same day to keep this project to
Kalshi and Polymarket.** This section records what was probed so nobody
repeats it. Probe results are in `data/sample/recon/props/`.

## What Cat's project used

Her explainer names RotoWire (the public props page *and* a paid subscription
feed), ESPN's schedule API, DraftKings, BettingPros, and the DFS platforms
PrizePicks, Underdog, Sleeper and Pick6 — the DFS data reached through
RotoWire's paid feed. Her finding for the 2026 men's basketball tournament was
over 21,000 chances to bet on individual college athletes.

Her framing of the problem is the part worth keeping: there is no public
database of college player prop lines, because books and DFS apps post them in
real time and pull them at tip-off. Cadence, not code, decides whether that
story exists.

## What is actually reachable

Probed with the project's identifying User-Agent. No spoofed browser strings,
no bot-detection bypass, no accounts created.

| Source | Result |
|---|---|
| `api.prizepicks.com/projections` | 403, DataDome bot wall |
| `sportsbook-nash.draftkings.com` | 403 Access Denied (Akamai); even `/robots.txt` is denied |
| `api.bettingpros.com/v3/offers` | 403 `{"message":"Forbidden"}` |
| `api.underdogfantasy.com` | 404; endpoint moved, unverified |
| The Odds API | 401 without a key; docs describe no free tier, and player props are "non-featured markets" with limited coverage |
| RotoWire public CFB props page | 200, 494 KB, prop rows embedded in the HTML |

Only RotoWire is reachable without either paying a vendor or defeating bot
protection. Its `robots.txt` allows `/betting/` for the generic agent and a
plain identified request returns the same data a browser gets, so a collector
there would have been straightforward. Row shape was
`<book>_<prop>` / `<book>_<prop>Over` / `<book>_<prop>Under` across nine books
(betr, betrivers, caesars, circasports, draftkings, fanduel, hardrock, mgm,
thescore).

## Why it was still ruled out

- The paid RotoWire feed, which is where Cat's DFS coverage came from, is a
  plan §2 non-goal. Without it the DFS platforms — the ones that actually
  operate in Nebraska, and the real parallel to Kalshi — are unreachable.
- The free RotoWire page is a curated weekly selection, not the market. The
  Week 2 page carried 100 rows across 13 teams, all marquee games, and
  **Nebraska appeared zero times**. That is not evidence that no book offers
  Nebraska props; it is evidence that this page cannot answer the question.
- A sportsbook publishes a line and never publishes how much was bet. Kalshi
  and Polymarket publish every trade. For "how much money moved on Nebraska,"
  which is this project's question, the prediction markets are the better
  source and are already collected.

## What we already have instead

Nebraska player-prop markets are being collected, with traded volume attached:

- Kalshi: named-player markets in `KXNCAAFLEADER`, `KXNCAAFBIGTENLEADER` and
  `KXNCAAFAWARD` (§3 above).
- Polymarket: 21 `anytime_touchdowns` markets on named players for the
  5 Sep 2026 Ohio game alone.

Plan §9 still governs these: aggregate volume by market type only, and no
dollar figure against a named athlete without the editor.

## If this is ever revisited

The RotoWire page would need collecting weekly and close to kickoff, not daily.
The Odds API needs an account and a key, which is Justin's decision to make;
I cannot create accounts. Bot-detection bypass is not on the table.
