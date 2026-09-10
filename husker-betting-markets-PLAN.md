# Project plan: `husker-markets`

Tracking prediction-market and sportsbook activity on Nebraska football.

**Audience for this doc:** Claude Code, working in an empty repo.
**Human owner:** Justin Diep, The Daily Nebraskan.
**Read this whole file before writing code. Then confirm the Phase 0 assumptions with me before starting Phase 1.**

---

## 1. Why this exists

Nebraska law bans all sports wagering on in-state college teams — Huskers game
bets, in-game bets, and player props are prohibited at the state's retail
sportsbooks. Meanwhile Kalshi and Polymarket, both unlicensed by Nebraska, take
Nebraska football action from Nebraska users. Nebraskans vote on legalizing
online sports betting on Nov. 3, 2026.

Nobody has published the number: **how much money has actually traded on
Nebraska football markets, and when.**

Second thread, modeled on CBS Sports' Aug. 2026 LSU story: athletic departments
hedge coaches' performance bonuses through third-party insurers, and at least
one of those insurers lays the risk off on Kalshi. The trade sizes match the
bonus figures in the coach's contract. Matt Rhule's contract is a public record
at a public university. If someone is hedging Nebraska's playoff bonuses on
Kalshi, the block trades are in the public daily trade files and the payouts
will match the contract.

This repo is the collection and analysis pipeline for both.

**Model repo:** `github.com/catelizabethmurphy/ire26`. Same shape — scrapers,
a committed sample that runs end to end offline, and a prose METHODOLOGY.md.
Match that structure. It is what makes a data story verifiable by an editor.

---

## 2. Non-goals — do not build these

- **No trading.** Do not touch order-placement endpoints on any platform. Never
  add auth for anything beyond read access. If an SDK bundles trading methods,
  do not import them.
- **No account credentials in the repo.** `.env` is gitignored; ship
  `.env.example` only.
- **No social media scraping in this repo.** The athlete-harassment analysis is
  a separate project with its own ethics review. Do not add it here, even as a
  stub.
- **No paid data vendors.** Everything here comes from public endpoints and
  public files. If a source requires payment, note it in METHODOLOGY.md as a
  limitation and move on.
- **No inferring or publishing individual trader identity from Kalshi.** Kalshi
  does not publish taker identity. Do not attempt to correlate, deanonymize, or
  guess. Polymarket wallet addresses are on-chain and public — those you may
  record, but see §9.

---

## 3. Stack

Python 3.11+, `uv` for deps (matching the model repo). `httpx` for requests,
`pandas` + `pyarrow` for analysis, `pytest` for tests. SQLite via stdlib
`sqlite3` for the normalized store; raw responses land on disk as gzipped JSON.

No ORM, no framework, no web app in v1.

---

## 4. Repo layout

```
husker-markets/
├── README.md
├── METHODOLOGY.md              prose companion — write as you go, not at the end
├── pyproject.toml / uv.lock
├── .env.example
├── .gitignore                  data/raw/ excluded, data/sample/ committed
├── config/
│   ├── targets.yml             which series/markets to watch, keyword filters
│   └── bonus_ladder.yml        Rhule contract bonus rungs (I fill this in)
├── collectors/
│   ├── kalshi.py
│   ├── polymarket.py
│   └── common.py               retry, backoff, raw-archive writer, logging
├── normalize/
│   ├── schema.sql
│   ├── load.py                 raw JSON -> SQLite
│   └── resolve.py              market title -> canonical game/team/market_type
├── analysis/
│   ├── anomalies.py            block-trade and bonus-ladder detection
│   └── notebook.ipynb          runs end-to-end on data/sample/, no network
├── data/
│   ├── raw/<source>/<YYYY-MM-DD>/*.json.gz     gitignored
│   ├── sample/                                 committed, one day of real data
│   └── husker.db                               gitignored
└── .github/workflows/collect.yml               scheduled collection
```

---

## 5. Phases

### Phase 0 — Recon. Do this first, write no collectors yet.

Produce `docs/RECON.md` answering, with the actual URL and a saved sample
response for each:

1. What is the current Kalshi public REST base URL and the correct path for
   listing series, events, markets, and trades? **My working assumption is
   `https://api.elections.kalshi.com/trade-api/v2` with `/series`, `/events`,
   `/markets`, `/markets/trades` — verify against docs.kalshi.com and correct
   me if it has moved.** Confirm which of these need no authentication.
2. What college football series exist? Dump the full series list and grep for
   football. **Do not guess ticker strings.** Record the real ones.
3. Which Kalshi events currently reference Nebraska? Search event and market
   titles for `Nebraska`, `Cornhuskers`, `Huskers`, `NEB`. Record the exact
   title formats — the normalizer depends on them.
4. Does `kalshi.com/trade-data` offer bulk daily files for download, or is the
   API the only route to per-trade history? If files exist, get the URL pattern
   and one sample. This matters: the CBS story ran on those files.
5. Same for Polymarket — confirm the gamma/markets endpoint and the trades
   endpoint, whether `proxyWallet` is present on trade records, and how far
   back history goes.
6. Rate limits and terms for each. Note them in RECON.md and honor them.
7. Does either platform list markets on individual Nebraska *players*? Flag
   loudly if so — that is its own story.

**Stop after Phase 0 and show me RECON.md.** If an assumption above is wrong,
say so plainly rather than building around a guess.

### Phase 1 — Kalshi collector

`collectors/kalshi.py`, run as `uv run python -m collectors.kalshi`.

- Discover markets each run rather than hardcoding tickers; filter by the
  keyword list in `config/targets.yml`.
- For each matching market, pull trades since the last watermark and full
  market metadata.
- Write every raw response, unmodified, to
  `data/raw/kalshi/<date>/<endpoint>_<ticker>_<fetch_ts>.json.gz`.
- **The raw archive is append-only and immutable.** Never rewrite or clean a
  raw file. All cleaning happens downstream. This is the difference between a
  story that survives a challenge and one that doesn't.
- Record a `fetched_at` UTC timestamp with every response.
- Idempotent: re-running for a date must not duplicate rows downstream.
- Handle 429s with exponential backoff, cap concurrency at something polite,
  set a real User-Agent identifying the project and my contact.

### Phase 2 — Polymarket collector

Same contract, `collectors/polymarket.py`. Additional requirement: capture
`proxyWallet` / taker address per trade where present. Markets settle and
disappear — capture full market metadata on first sight and don't assume you
can re-fetch it later.

### Phase 3 — Normalization

The hard part, and the part the model repo spends its notebook on. Two
platforms describe the same wager differently.

Build `normalize/resolve.py` to map raw titles onto:

- `game_id` — canonical opponent + date, joined against a schedule table
  (2026 Nebraska schedule; ESPN's public schedule endpoint is fine as the
  source, and the model repo has a scraper for it).
- `market_type` — enum: `game_winner`, `spread`, `total`, `season_wins`,
  `conference_champ`, `playoff_berth`, `natty`, `player_prop`, `other`.
- `team` — normalized to a single Nebraska identifier.

Every unmapped title goes to a `unresolved` table with its raw text. Print a
count of unresolved rows at the end of each load. Never silently drop a row,
and never guess a mapping — an unresolved row is honest, a wrong mapping is a
correction.

### Phase 4 — Analysis

`analysis/anomalies.py`, three detectors:

1. **Block trades.** Flag any single trade above the 99th percentile of trade
   size for that market, and any trade that is the only trade in its market
   that day. That combination is exactly what made the LSU trades visible.
2. **Bonus-ladder match.** Given `config/bonus_ladder.yml` — a list of
   `{milestone, bonus_dollars}` from Rhule's contract — flag trades whose
   payout (contracts × $1 face value) falls within a configurable tolerance of
   a bonus figure, and flag *sets* of trades across the playoff milestone
   ladder executed within a short window of each other. Report execution
   timestamps to the second.
3. **Volume timeline.** Daily and weekly notional volume on Nebraska markets,
   broken out by market type and platform, with the season schedule and the
   ballot-measure calendar as annotations.

Output is a tidy CSV per detector plus the notebook. `notebook.ipynb` must run
top to bottom on `data/sample/` with no network and no credentials.

### Phase 5 — Ship-readiness

- `METHODOLOGY.md`: sources, what each does and doesn't cover, collection
  cadence, every normalization decision, and a stated limitations section.
  Limitations to state explicitly: Kalshi publishes no taker identity;
  sportsbooks publish no per-game handle, so book *lines* are not book *bets*;
  offshore and sweepstakes platforms are out of scope; volume counts both sides
  of a transaction and notional volume is not dollars at risk.
- `data/sample/`: one real collection day from each platform, raw plus cleaned
  output, with its own README walking through what's in it.
- GitHub Action running collection on a schedule. **Cadence matters more than
  the code here** — markets settle and delist, so a gap is unrecoverable. Get
  this running early, even against a partial collector.

---

## 6. Schema sketch

Refine as needed, but keep these invariants: raw is immutable, every row traces
to a raw file, every row carries a fetch timestamp.

```sql
CREATE TABLE market (
  source            TEXT NOT NULL,        -- 'kalshi' | 'polymarket'
  source_market_id  TEXT NOT NULL,        -- native ticker / condition id
  title             TEXT NOT NULL,        -- verbatim
  series_id         TEXT,
  event_id          TEXT,
  open_ts           INTEGER,
  close_ts          INTEGER,
  settled_outcome   TEXT,
  game_id           TEXT,                 -- resolved, nullable
  market_type       TEXT,                 -- resolved, nullable
  first_seen_at     INTEGER NOT NULL,
  raw_path          TEXT NOT NULL,
  PRIMARY KEY (source, source_market_id)
);

CREATE TABLE trade (
  source            TEXT NOT NULL,
  source_trade_id   TEXT NOT NULL,
  source_market_id  TEXT NOT NULL,
  executed_ts       INTEGER NOT NULL,     -- seconds, UTC
  price_cents       INTEGER NOT NULL,
  count             INTEGER NOT NULL,
  taker_side        TEXT,                 -- 'yes' | 'no' | null
  taker_address     TEXT,                 -- polymarket only
  cost_usd          REAL,                 -- price × count, derived
  payout_usd        REAL,                 -- count × $1 face, derived
  fetched_at        INTEGER NOT NULL,
  raw_path          TEXT NOT NULL,
  PRIMARY KEY (source, source_trade_id)
);

CREATE TABLE unresolved (
  source           TEXT, source_market_id TEXT, title TEXT, seen_at INTEGER
);
```

---

## 7. Rules for the agent

- **Never fabricate an endpoint, ticker, field name, or figure.** If Phase 0
  can't confirm something, write "unconfirmed" in RECON.md and ask. A guessed
  ticker that returns an empty array looks identical to a real ticker with no
  trades, and that is the kind of error that ends up in print.
- **Do not clean data in the collector.** Collect raw, normalize downstream.
- **Every derived number needs a traceable path back to a raw file.** If I get
  a call from a Kalshi spokesperson, I need to point at the exact response.
- **Write METHODOLOGY.md as you go.** Every normalization judgment call gets a
  line the same day you make it.
- Tests: normalization gets real unit tests against fixture titles. Collectors
  get a smoke test against a recorded fixture, not the live API.
- Commit in reviewable chunks with real messages.

---

## 8. What I'm doing in parallel (not your job)

Listed so you know what data is coming and can leave a slot for it:

- Public records request to NU Athletics: Rhule's current contract with the
  full bonus schedule, plus any contract, invoice, or correspondence with Game
  Point Capital or any third-party insurer covering coach or staff incentive
  bonuses. → fills `config/bonus_ladder.yml`.
- Nebraska Racing and Gaming Commission monthly handle reports, and Iowa
  Racing and Gaming Commission reports for the cross-border comparison. →
  probably a small CSV I hand you later; leave `collectors/` open for it.
- Reading the Flatwater Free Press March 2026 piece on prediction markets
  evading Nebraska's ban, so we don't rewrite it.
- Interview requests: AG's office, NU Athletics, Kalshi comms, WarHorse.

---

## 9. Ethics guardrails

These are not optional and they are not negotiable in a refactor.

- Publishing a wallet address is publishing a person's trading history. Record
  Polymarket addresses for analysis; do not put one in a story without a
  conversation with an editor first.
- If player-level prop markets show up, the analysis stops at aggregate volume.
  We do not build anything that attaches a dollar figure to a named
  20-year-old's stat line and hands it to a comment section.
- If any of this touches identifiable UNL athletes, it goes to the editor
  before it goes in the repo's public README.

---

## 10. First thing to do

Phase 0 recon only. Produce `docs/RECON.md`, save one sample response per
endpoint under `data/sample/recon/`, and come back with what's confirmed, what
isn't, and which of my assumptions in §5 were wrong.
