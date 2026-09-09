"""Resolve a team as a platform writes it onto one canonical school.

The trap this module exists to avoid: **a platform's team code is not a unique
identifier.** Kalshi reuses short codes across divisions, so in the archive
`WSU` is Washington St. in one game and Winona State in another, `KSU` is
Kansas St. and Kentucky State, `CSU` is Colorado St. and Central State (OH),
`WEB` is Weber St. and Webber International, and `LC` is Louisiana Christian
and Lane. Keying on the code silently merges two schools and produces a number
that is wrong in a way nobody can see.

So resolution runs on the team *name* each market carries -- Kalshi's
`yes_sub_title`, Polymarket's event title -- and the code is only ever a hint
inside a single game. Names are matched against ESPN's team list, with a
hand-checked override file for the residue.
"""
from __future__ import annotations

import gzip
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from collectors.common import PROJECT_ROOT, load_yaml

TEAMS_FILE = PROJECT_ROOT / "data" / "reference" / "espn_teams.json.gz"
OVERRIDES_FILE = PROJECT_ROOT / "config" / "team_overrides.yml"


def normalize_name(s: str) -> str:
    """Fold the spellings the platforms actually use onto one key."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    s = re.sub(r"\bst\.?\b", "state", s)
    s = re.sub(r"\buniv(ersity)?\b", "", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(s.split())


@dataclass(frozen=True)
class Team:
    id: str
    name: str
    abbr: str | None


@dataclass(frozen=True)
class Resolution:
    team: Team | None
    how: str                # exact | override | ambiguous | unmatched | unresolvable


@lru_cache(maxsize=1)
def _load():
    with gzip.open(TEAMS_FILE, "rt") as f:
        teams = json.load(f)
    by_id = {t["id"]: Team(t["id"], t["name"], t.get("abbr")) for t in teams}
    index: dict[str, set[str]] = {}
    for t in teams:
        for key in (t["name"], t.get("short"), t.get("location"), t.get("abbr")):
            k = normalize_name(key or "")
            if k:
                index.setdefault(k, set()).add(t["id"])
    cfg = load_yaml(OVERRIDES_FILE)
    overrides = {normalize_name(k): v for k, v in (cfg.get("overrides") or {}).items()}
    unresolvable = {normalize_name(k) for k in (cfg.get("unresolvable") or {})}
    return by_id, index, overrides, unresolvable


def resolve_team(name: str) -> Resolution:
    by_id, index, overrides, unresolvable = _load()
    n = normalize_name(name)
    if not n:
        return Resolution(None, "unmatched")
    if n in overrides:
        return Resolution(by_id.get(overrides[n]), "override")
    if n in unresolvable:
        return Resolution(None, "unresolvable")
    hit = index.get(n)
    if hit and len(hit) == 1:
        return Resolution(by_id[next(iter(hit))], "exact")
    if hit:
        return Resolution(None, "ambiguous")
    # No prefix fallback. Dropping trailing words looks helpful and is not:
    # on this data it turned North Carolina St. into North Carolina, ULM into
    # Louisiana, Southern Mississippi into Southern and Texas Permian Basin
    # into Texas. Every one of those merges two real schools into one number
    # that looks fine. An unmatched name is honest; a wrong one is a
    # correction. Anything landing here belongs in the override file.
    return Resolution(None, "unmatched")
