# Phase 0 recon samples

Raw responses saved during the 8 Sep 2026 recon, one per endpoint probed, plus
the docs pages they were checked against. Nothing here is cleaned. File names
carry the endpoint and, where relevant, the ticker or query. `docs/RECON.md`
at the repo root explains what each one established.

All requests were unauthenticated and sent with the User-Agent
`husker-markets-recon (Justin Diep, The Daily Nebraskan, <email>)`, except the
ESPN call, which refused that UA and was repeated with curl's default.

## `kalshi/`

| File | What it is |
|---|---|
| `kalshi_series.json.gz` | `GET /series?limit=1000`: all 13,849 series in one response |
| `kalshi_cfb_series_list.txt` | the 130 college-football series filtered out of that dump (ticker, title, frequency) |
| `kalshi_series_KXNCAAFGAME.json` | `GET /series/KXNCAAFGAME` |
| `kalshi_events_p1.json`, `kalshi_markets_p1.json`, `kalshi_trades_p1.json` | first page of the bare `/events`, `/markets`, `/markets/trades` endpoints (shape check) |
| `kalshi_events_<SERIES>.json` | `GET /events?series_ticker=…&with_nested_markets=true` for the first eight series probed (uncompressed) |
| `kalshi_events_<SERIES>_p<N>.json.gz` | same endpoint, paginated, for the 30-series Nebraska sweep |
| `kalshi_events_KXNCAAFGAME-26SEP05OHIONEB.json` | `GET /events/{event}?with_nested_markets=true`, the settled Ohio game |
| `kalshi_nebraska_markets_found.json` | derived: the 225 Nebraska markets the sweep matched (series, event, ticker, title, sub-title, status, volume_fp, open, close) |
| `kalshi_market_KXNCAAFB10-26-NEB.json` | `GET /markets/{ticker}` |
| `kalshi_trades_KXNCAAFB10-26-NEB.json` | `GET /markets/trades?ticker=…&limit=1000`, 83 trades |
| `kalshi_trades_KXNCAAFTEAMYDS-26SEP05OHIONEB-NEB275.json` | live trades for a finalized post-cutoff market (5 trades) |
| `kalshi_historical_cutoff.json` | `GET /historical/cutoff` |
| `kalshi_historical_markets_KXNCAAFB10-25.json` | `GET /historical/markets?event_ticker=…`, 18 settled 2025 markets |
| `kalshi_historical_trades_KXNCAAFB10-25-NEB.json` | `GET /historical/trades?ticker=…`, 201 trades May–Nov 2025 |
| `kalshi_historical_trades_sample.json` | `/historical/trades` for a post-cutoff ticker: empty, as documented |
| `kalshi_trade-data_page.html` | what curl gets from `kalshi.com/trade-data`: a Vercel bot checkpoint (HTTP 429) |

## `polymarket/`

| File | What it is |
|---|---|
| `polymarket_gamma_markets_p1.json`, `polymarket_data_trades_p1.json` | shape checks of `/markets` and `data-api /trades` |
| `polymarket_gamma_sports.json` | `GET /sports`: 465 leagues; `cfb` is id 9, series 12756, tag 100351 |
| `polymarket_gamma_tags.json` | `GET /tags?limit=1000` (only 100 returned) |
| `polymarket_gamma_public-search_*.json` | `/public-search` for Nebraska, Huskers, Cornhuskers, Matt Rhule, with page and active variants |
| `polymarket_gamma_events_nebraska.json.gz` | `/events?slug_contains=nebraska`: returned unrelated events, parameter not honoured |
| `polymarket_gamma_events_recent_sports.json.gz` | `/events?closed=false&order=startDate` first 100 |
| `polymarket_gamma_events_cfb_tag100351_open.json.gz` | open CFB futures by tag |
| `polymarket_gamma_events_cfb_series12756.json.gz` | first page of open CFB game events by series |
| `polymarket_gamma_events_series12756_closed{false,true}_off<N>.json.gz` | the paginated sweep of the CFB series (468 events) |
| `polymarket_nebraska_2026_events_found.json` | derived: the three 2026 Nebraska game events with all nested markets |
| `polymarket_gamma_events_cfb-ohio-nebraska.json` | `/events?slug=cfb-ohio-nebraska-2026-09-05`: empty, the real slug uses `nebr` |
| `polymarket_gamma_market_510126.json` | `GET /markets/510126`, the 2024 Indiana–Nebraska winner market |
| `polymarket_data_trades_indiana-nebraska.json` | `data-api /trades?market=<conditionId>` for that 2024 market, 34 fills |
| `polymarket_data_trades_ohio-nebr-2026.json` | same for the 2026 Ohio game moneyline, 326 fills |
| `polymarket_data_trades_ohio-nebr-2026_off1000.json` | offset probe, empty |

## `espn/`

`espn_nebraska_schedule_2026.json`: ESPN public schedule endpoint for team 158, 12 games.

## `docs/`

Markdown/text copies of the docs pages cited in RECON.md (Kalshi `llms.txt`, rate limits, historical data, get-trades, get-markets, historical endpoints; Polymarket `llms.txt`, rate limits, discover-markets, analytics, data-api trades) and the two Kalshi PDFs (Data Terms of Use, 2021 volume/OI notice) with extracted text. `polymarket_tos.html` is the raw terms page; its text did not render server-side.

## `props/`

Sportsbook and DFS player-prop probes from 8 Sep 2026 (RECON "Phase 0b"). All
sent with the project's identifying User-Agent; no spoofed browser strings and
no bot-detection bypass.

| File | What it is |
|---|---|
| `prizepicks_leagues.json` | `api.prizepicks.com/leagues`: 403, a DataDome bot wall |
| `draftkings_cfb.json` | DraftKings sportsbook API: 403 Access Denied (Akamai) |
| `bettingpros_offers.json` | BettingPros v3 offers: 403 Forbidden |
| `underdog_landing.json` | Underdog hub path: 404, endpoint moved |
| `oddsapi_sports_nokey.json` | The Odds API without a key: 401, shows the signup requirement |
| `rotowire_cfb_player-props.html` | the working source: the public college-football props page, 494 KB, prop rows embedded in the HTML |
| `rotowire_cfb_props_rows_sample.json` | eight parsed rows showing the per-book field shape |
| `rotowire_ncaaf_props.html` | a wrong-path guess that 404s, kept so the correct path is not re-guessed |
| `robots_*.txt` | each site's robots.txt, including RotoWire's (which permits `/betting/`) |
| `rotowire_llms.txt` | RotoWire's published LLM policy, recorded as evidence about permission |

## What was removed before committing, and how to get it back

The Phase 0 recon downloaded more than the repo should carry permanently.
These were deleted on 8 Sep 2026; nothing in `collectors/`, `tests/` or
`config/` reads them, and each is one request away.

| Removed | Regenerate with |
|---|---|
| `polymarket_gamma_events_series12756_closed{true,false}_off*.json.gz`, `polymarket_gamma_events_cfb_series12756.json.gz` (~7 MB of paginated College Football events, overwhelmingly other teams' games) | `GET gamma-api.polymarket.com/events?series_id=12756&closed=<bool>&limit=100&offset=<n>&order=startDate&ascending=false`, or just run `collectors/polymarket.py`, which archives these pages every run |
| `polymarket_gamma_events_tag102934_open.json` | superseded wrong-tag probe; the right tag is 100351 |
| `props/rotowire_*.html` | sportsbook props are out of scope (RECON "Phase 0b"); the small probe bodies and robots files are kept as the record of why |
| `docs/docs_https___docs.kalshi.com_*.txt`, `docs_https___docs.polymarket.com_sitemap.xml.txt` | rendered vendor doc pages; the smaller `.md` versions of the same pages are kept |

Remaining files over 100 KB are gzipped. The Nebraska-specific artifacts, the
full Kalshi series dump behind the 130-series list, and every response cited by
a number in `docs/RECON.md` are still here.
