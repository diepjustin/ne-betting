#!/usr/bin/env python3
"""Stage a redacted copy of the raw archive for upload.

    uv run python scripts/redact_for_upload.py [--raw DIR] [--out DIR]

Why this exists: the scheduled job uploaded `data/raw` as a workflow artifact,
and on a public repository any GitHub account can download one. Raw Polymarket
trades carry `proxyWallet`, `name` and `pseudonym`, so that artifact was a
compiled, per-market listing of who traded. METHODOLOGY promises trader
identity is redacted from what this project publishes, and an artifact is a
publication even though it is not a commit.

The raw archive itself is append-only and is never modified here. This writes a
separate tree, and only that tree is uploaded. The unredacted responses stay
local, which is where the reporting works from.

Kalshi files are copied byte for byte: the exchange publishes no identity for
the parties to a trade, so there is nothing in them to redact.
"""
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sys
from pathlib import Path

# A Polymarket trade names the person who made it. `transactionHash` resolves
# to the same wallet on any block explorer, so removing one without the other
# removes nothing.
WALLET_FIELDS = ("proxyWallet", "wallet", "maker_address", "taker_address")
TX_FIELDS = ("transactionHash",)
PROFILE_FIELDS = ("name", "pseudonym", "bio", "profileImage", "profileImageOptimized")


def redact_rows(rows: list) -> tuple[list, int]:
    """Replace identity with stable placeholders, numbered per file."""
    wallets: dict[str, str] = {}
    txs: dict[str, str] = {}
    touched = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        hit = False
        for f in WALLET_FIELDS:
            v = r.get(f)
            if isinstance(v, str) and v:
                wallets.setdefault(v, f"redacted-trader-{len(wallets) + 1:04d}")
                r[f] = wallets[v]
                hit = True
        for f in TX_FIELDS:
            v = r.get(f)
            if isinstance(v, str) and v:
                txs.setdefault(v, f"redacted-tx-{len(txs) + 1:04d}")
                r[f] = txs[v]
                hit = True
        for f in PROFILE_FIELDS:
            if r.get(f) not in (None, ""):
                r[f] = None
                hit = True
        touched += bool(hit)
    return rows, touched


def stage(raw: Path, out: Path) -> dict:
    copied = redacted = rows_touched = failed = 0
    for src in sorted(raw.rglob("*.json.gz")):
        rel = src.relative_to(raw)
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Only Polymarket trade responses carry identity.
        if rel.parts[0] != "polymarket" or not rel.name.startswith("trades_"):
            shutil.copy2(src, dst)
            copied += 1
            continue
        try:
            with gzip.open(src, "rt", encoding="utf-8") as f:
                env = json.load(f)
            body = json.loads(env["body"])
        except (OSError, EOFError, json.JSONDecodeError, KeyError):
            # Unreadable here means unreadable to a reader too. Skip rather
            # than copy something that was not checked.
            failed += 1
            continue
        if isinstance(body, list):
            body, n = redact_rows(body)
            rows_touched += n
        env["body"] = json.dumps(body, ensure_ascii=False)
        env["redacted"] = "trader identity replaced with placeholders for upload"
        with gzip.open(dst, "wt", encoding="utf-8", compresslevel=9) as f:
            json.dump(env, f, ensure_ascii=False)
        redacted += 1
    return {
        "copied_unchanged": copied,
        "redacted_files": redacted,
        "rows_with_identity_removed": rows_touched,
        "unreadable_skipped": failed,
        "staged_at": str(out),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    root = Path(__file__).resolve().parent.parent
    ap.add_argument("--raw", type=Path, default=root / "data" / "raw")
    ap.add_argument("--out", type=Path, default=root / "data" / "raw_for_upload")
    args = ap.parse_args(argv)

    if not args.raw.exists():
        print(f"no raw archive at {args.raw}", file=sys.stderr)
        return 0
    if args.out.exists():
        shutil.rmtree(args.out)
    print(json.dumps(stage(args.raw, args.out), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
