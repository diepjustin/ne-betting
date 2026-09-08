"""Shared plumbing for the collectors: polite HTTP, the raw archive, state.

The raw archive is the evidentiary record. Every response is written exactly
as received, inside a small envelope that says when and from where it was
fetched. Nothing in this module parses a body for meaning; that happens in
normalize/.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

log = logging.getLogger("husker")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONTACT = "Justin Diep, The Daily Nebraskan"


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(ts: dt.datetime) -> str:
    return ts.isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_rfc3339(s: str) -> dt.datetime:
    """Kalshi timestamps look like 2026-09-08T04:55:55.55051Z."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return dt.datetime.fromisoformat(s)


def user_agent() -> str:
    contact = os.environ.get("HUSKER_MARKETS_CONTACT", DEFAULT_CONTACT)
    return f"husker-markets/0.1 ({contact})"


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )


@dataclass
class Response:
    url: str
    status: int
    text: str
    fetched_at: dt.datetime

    def json(self) -> Any:
        return json.loads(self.text)


class RetryError(RuntimeError):
    pass


@dataclass
class Client:
    """Sequential, rate-limited GET with exponential backoff.

    One request at a time on purpose. Kalshi does not publish an unauthenticated
    limit, and being throttled by a source we are reporting on is not a story
    we want to tell.
    """

    base_url: str
    min_interval: float = 1.0
    max_attempts: int = 6
    timeout: float = 30.0
    transport: httpx.BaseTransport | None = None
    _last_request_at: float = field(default=0.0, init=False)
    requests_made: int = field(default=0, init=False)
    retries: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={"User-Agent": user_agent(), "Accept": "application/json"},
            timeout=self.timeout,
            transport=self.transport,
        )

    def close(self) -> None:
        self._http.close()

    def _pace(self) -> None:
        wait = self._last_request_at + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def get(self, path: str, params: dict | None = None) -> Response:
        delay = 1.0
        for attempt in range(1, self.max_attempts + 1):
            self._pace()
            self._last_request_at = time.monotonic()
            self.requests_made += 1
            fetched_at = utc_now()
            try:
                r = self._http.get(path, params=params)
            except httpx.HTTPError as e:
                log.warning("network error on %s (%s), attempt %d", path, e, attempt)
                status = None
            else:
                status = r.status_code
                if status < 400:
                    return Response(str(r.url), status, r.text, fetched_at)
                if status == 404:
                    return Response(str(r.url), status, r.text, fetched_at)
                if status not in (429, 500, 502, 503, 504):
                    raise RetryError(f"{status} from {r.url}: {r.text[:200]}")
                log.warning("%s from %s, attempt %d", status, r.url, attempt)
            self.retries += 1
            time.sleep(delay)
            delay = min(delay * 2, 60)
        raise RetryError(f"gave up on {path} after {self.max_attempts} attempts")


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(s: str) -> str:
    return _SAFE.sub("_", s)[:120]


@dataclass
class RawArchive:
    """Append-only store of gzipped response envelopes.

    Path: <root>/<source>/<YYYY-MM-DD>/<endpoint>_<key>_<fetch_ts>.json.gz
    The date is the fetch date in UTC. Files are never rewritten: the fetch
    timestamp in the name carries microseconds, so a re-run gets new files.
    """

    root: Path
    files_written: int = field(default=0, init=False)
    bytes_written: int = field(default=0, init=False)

    def write(self, source: str, endpoint: str, key: str, resp: Response) -> Path:
        day = resp.fetched_at.strftime("%Y-%m-%d")
        stamp = resp.fetched_at.strftime("%Y%m%dT%H%M%S%fZ")
        d = self.root / source / day
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{safe_name(endpoint)}_{safe_name(key)}_{stamp}.json.gz"
        if path.exists():
            raise FileExistsError(f"refusing to overwrite raw file {path}")
        envelope = {
            "fetched_at": iso(resp.fetched_at),
            "source": source,
            "endpoint": endpoint,
            "key": key,
            "url": resp.url,
            "status": resp.status,
            "body": resp.text,  # verbatim, still a string
        }
        data = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        with gzip.open(path, "wb", compresslevel=9) as f:
            f.write(data)
        self.files_written += 1
        self.bytes_written += path.stat().st_size
        return path


def read_raw(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


class State:
    """Small JSON file of per-market watermarks. Rewritten atomically."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {"markets": {}}
        if path.exists():
            with open(path) as f:
                self.data = json.load(f)
        self.data.setdefault("markets", {})

    def market(self, ticker: str) -> dict:
        return self.data["markets"].setdefault(ticker, {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, self.path)
