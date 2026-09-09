-- Normalized store. Raw stays immutable on disk; this is rebuilt from it.
-- Invariants: every row names the raw file it came from, every row carries a
-- fetch timestamp, and nothing is dropped silently -- a title that cannot be
-- mapped lands in `unresolved` with its text intact.

CREATE TABLE IF NOT EXISTS market (
  source            TEXT NOT NULL,          -- 'kalshi' | 'polymarket'
  source_market_id  TEXT NOT NULL,          -- ticker | condition id
  title             TEXT,                   -- verbatim
  subtitle          TEXT,                   -- verbatim
  series_id         TEXT,
  event_id          TEXT,
  open_ts           INTEGER,
  close_ts          INTEGER,
  status            TEXT,
  settled_outcome   TEXT,
  game_id           TEXT,                   -- resolved, nullable
  market_type       TEXT,                   -- resolved, nullable
  team              TEXT,                   -- resolved, nullable
  player            TEXT,                   -- resolved, nullable
  line              REAL,                   -- spread/total strike where known
  first_seen_at     INTEGER NOT NULL,
  raw_path          TEXT NOT NULL,
  PRIMARY KEY (source, source_market_id)
);

CREATE TABLE IF NOT EXISTS trade (
  source            TEXT NOT NULL,
  source_trade_id   TEXT NOT NULL,          -- native id, or synthesised for Polymarket
  source_market_id  TEXT NOT NULL,
  executed_ts       INTEGER NOT NULL,       -- seconds, UTC
  executed_iso      TEXT NOT NULL,          -- full precision as served
  price_dollars     REAL NOT NULL,          -- Kalshi: the Yes price, as served
  price_no_dollars  REAL,                   -- Kalshi only; Polymarket prices the side bought
  count             REAL NOT NULL,          -- contracts (Kalshi) | shares (Polymarket)
  taker_side        TEXT,
  taker_address     TEXT,                   -- Polymarket only
  cost_usd          REAL,                   -- price_dollars x count (Yes side on Kalshi)
  taker_cost_usd    REAL,                   -- what the taker actually paid
  payout_usd        REAL,                   -- count x $1 face
  is_block_trade    INTEGER,
  id_is_synthetic   INTEGER NOT NULL DEFAULT 0,
  fetched_at        INTEGER NOT NULL,
  raw_path          TEXT NOT NULL,
  PRIMARY KEY (source, source_trade_id)
);

CREATE TABLE IF NOT EXISTS unresolved (
  source            TEXT,
  source_market_id  TEXT,
  title             TEXT,
  reason            TEXT,
  seen_at           INTEGER
);

CREATE TABLE IF NOT EXISTS schedule (
  game_id     TEXT PRIMARY KEY,
  game_date   TEXT NOT NULL,
  opponent    TEXT NOT NULL,
  home_away   TEXT,
  source      TEXT
);

CREATE INDEX IF NOT EXISTS trade_market  ON trade (source, source_market_id);
CREATE INDEX IF NOT EXISTS trade_time    ON trade (executed_ts);
CREATE INDEX IF NOT EXISTS market_type_i ON market (market_type);
CREATE INDEX IF NOT EXISTS market_player ON market (player);
