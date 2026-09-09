"""The artifact must not carry trader identity.

The scheduled job uploaded data/raw as a 90-day artifact on a public
repository, and raw Polymarket trades name the person who made each one.
"""
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.redact_for_upload import redact_rows, stage


def write(path: Path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump({"fetched_at": "2026-09-08T04:55:55.550510Z", "source": "x",
                   "endpoint": "trades", "key": "k", "url": "u", "status": 200,
                   "body": json.dumps(body)}, f)


def read(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        env = json.load(f)
    return env, json.loads(env["body"])


ROW = {"proxyWallet": "0x50A0cebecFb81DbCbFA6d38F82040343F1E3D95F",
       "transactionHash": "0x796f2e616efb78a4d62ea6ffd18745c54799cd69",
       "name": "Someone", "pseudonym": "Jealous-Focus", "bio": "hi",
       "side": "BUY", "size": 22, "price": 0.999, "timestamp": 1788636315,
       "conditionId": "0x77dfd142d449298d9969541da0ae69bca326e1c2008c9fb216a5ce83ca6baf39"}


def test_wallet_and_transaction_hash_both_go():
    """A transaction hash resolves to the same wallet on any block explorer,
    so removing one without the other removes nothing."""
    rows, n = redact_rows([dict(ROW)])
    r = rows[0]
    assert n == 1
    assert r["proxyWallet"].startswith("redacted-trader-")
    assert r["transactionHash"].startswith("redacted-tx-")
    assert r["name"] is None and r["pseudonym"] is None and r["bio"] is None


def test_market_data_survives_redaction():
    rows, _ = redact_rows([dict(ROW)])
    r = rows[0]
    assert (r["side"], r["size"], r["price"], r["timestamp"]) == ("BUY", 22, 0.999, 1788636315)
    assert r["conditionId"] == ROW["conditionId"]      # a market id, not a person


def test_same_wallet_gets_one_placeholder():
    rows, _ = redact_rows([dict(ROW), dict(ROW)])
    assert rows[0]["proxyWallet"] == rows[1]["proxyWallet"]


def test_staging_leaves_the_raw_archive_untouched(tmp_path):
    raw = tmp_path / "raw"
    src = raw / "polymarket" / "2026-09-08" / "trades_1_x.json.gz"
    write(src, [dict(ROW)])
    before = src.read_bytes()
    stats = stage(raw, tmp_path / "out")
    assert src.read_bytes() == before, "raw is append-only and must not be rewritten"
    assert stats["redacted_files"] == 1 and stats["rows_with_identity_removed"] == 1
    _, body = read(tmp_path / "out" / "polymarket" / "2026-09-08" / "trades_1_x.json.gz")
    assert body[0]["proxyWallet"].startswith("redacted-")


def test_kalshi_files_pass_through_byte_for_byte(tmp_path):
    """Kalshi publishes no identity for the parties to a trade."""
    raw = tmp_path / "raw"
    src = raw / "kalshi" / "2026-09-08" / "trades_T_x.json.gz"
    write(src, {"trades": [{"trade_id": "abc", "count_fp": "1.0"}]})
    stage(raw, tmp_path / "out")
    dst = tmp_path / "out" / "kalshi" / "2026-09-08" / "trades_T_x.json.gz"
    assert dst.read_bytes() == src.read_bytes()


def test_no_wallet_survives_but_market_ids_do(tmp_path):
    """A wallet is 40 hex characters; a Polymarket condition id is 64 and is a
    market, not a person, so it must come through untouched."""
    import re
    raw = tmp_path / "raw"
    write(raw / "polymarket" / "2026-09-08" / "trades_1_x.json.gz", [dict(ROW)])
    stage(raw, tmp_path / "out")
    for p in (tmp_path / "out").rglob("*.json.gz"):
        with gzip.open(p, "rt") as f:
            text = f.read()
        assert not re.search(r"0x[0-9a-fA-F]{40}(?![0-9a-fA-F])", text), "wallet survived"
        assert ROW["conditionId"] in text, "market id must survive"
