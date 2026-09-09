# husker-markets

Collection and analysis pipeline for prediction-market activity on Nebraska
football (Kalshi, Polymarket). Owner: Justin Diep, The Daily Nebraskan.

- `docs/RECON.md` — what the platforms expose, verified 8 Sep 2026, with saved
  responses in `data/sample/recon/`.
- `METHODOLOGY.md` — sources, units, every collection decision, limitations.
- `config/targets.yml` — which Kalshi series are walked and how Nebraska
  markets are recognised.
- `collectors/` — `common.py` (rate-limited client, raw archive, state),
  `kalshi.py` (Phase 1) and `polymarket.py` (Phase 2).
- `data/raw/` (gitignored) — append-only gzipped responses,
  `<source>/<YYYY-MM-DD>/<endpoint>_<key>_<fetch_ts>.json.gz`.
- `data/state/kalshi.json`, `data/state/polymarket.json` — per-market
  watermarks and match reasons.
- `normalize/` — `schema.sql`, `resolve.py` (title to game, market type, team,
  player) and `load.py` (raw to SQLite, rebuilt from scratch each run).
- `analysis/breakdown.py` — tidy CSVs by market type, game, market, player and
  day. Output is gitignored; the per-player view names athletes.
- `analysis/anomalies.py` — block trades, outsized trades, and clusters that
  walk a school's season ladder in one window, which is what hedging a coach's
  bonus schedule looks like. `config/milestones.yml` defines the ladder and
  holds the bonus-tier table, which ships empty on purpose: a tier is only
  entered from a contract document that can be cited.

## Run

```bash
uv sync
uv run python -m collectors.kalshi            # live side, ~10 min at 1 req/s
uv run python -m collectors.kalshi --dry-run  # discovery only
uv run python -m collectors.kalshi --historical  # also pre-cutoff history

uv run python -m collectors.polymarket          # ~4 min
uv run python -m collectors.polymarket --dry-run
uv run python -m collectors.polymarket --backfill-since 2024-01-01  # by hand

uv run python -m collectors.kalshi --scope all --dry-run   # all of college football
uv run python -m collectors.polymarket --scope all --dry-run

uv run python -m normalize.load        # raw -> data/husker.db
uv run python -m analysis.breakdown    # CSVs into data/analysis/
uv run python -m analysis.anomalies    # block trades, ladder clusters, timeline

uv run pytest
```

Requires Python 3.11+ and `uv`. No credentials; copy `.env.example` to `.env`
only to set the contact string in the User-Agent.

## Rules

No trading code, no credentials, no social-media scraping, no paid data, no
inferring trader identity. See the project plan and `METHODOLOGY.md`.
