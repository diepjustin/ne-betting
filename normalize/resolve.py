"""Map a raw market onto a canonical game, market type, team and player.

Two platforms describe the same wager differently, and every mapping here was
read off a real response rather than guessed (see docs/RECON.md). A title this
module cannot place returns market_type `other` and is recorded in the
`unresolved` table with its text, because an unresolved row is honest and a
wrong mapping is a correction.
"""
from __future__ import annotations

import re
import gzip
import json
from dataclasses import dataclass
from functools import lru_cache

from collectors.common import PROJECT_ROOT
from .teams import resolve_team

# The enum the plan asks for, plus the coach markets both platforms list.
GAME_WINNER = "game_winner"
SPREAD = "spread"
TOTAL = "total"
TEAM_STAT = "team_stat"
SEASON_WINS = "season_wins"
CONFERENCE_CHAMP = "conference_champ"
PLAYOFF_BERTH = "playoff_berth"
NATTY = "natty"
RANKING = "ranking"
PLAYER_PROP = "player_prop"
COACH = "coach"
DRAFT = "draft"
OTHER = "other"

NEBRASKA = "NEB"

# Kalshi files coach awards in the same series as player awards, so the award
# named in the market's rules is the only thing separating a head coach from a
# 20-year-old. Extended only when a market naming another coach award is
# actually observed, never by guessing at the list of awards that exist.
COACH_AWARDS = ("Eddie Robinson Award",)

# Kalshi series family -> market type. Families come from the live /series dump.
KALSHI_FAMILY = {
    "KXNCAAFGAME": GAME_WINNER,
    "KXNCAAFSPREAD": SPREAD,
    "KXNCAAFTOTAL": TOTAL,
    "KXNCAAFTEAMTOTAL": TEAM_STAT,
    "KXNCAAFTEAMYDS": TEAM_STAT,
    "KXNCAAFTEAMTD": TEAM_STAT,
    "KXNCAAFWINS": SEASON_WINS,
    "KXNCAAFB10": CONFERENCE_CHAMP,
    "KXNCAAFBB10": CONFERENCE_CHAMP,
    "KXNCAAFB10QUAL": CONFERENCE_CHAMP,
    "KXNCAAFBIGTENREGTOP": CONFERENCE_CHAMP,
    "KXNCAAFBIGTENWINS": SEASON_WINS,
    "KXNCAAFPLAYOFF": PLAYOFF_BERTH,
    "KXNCAAPLAYOFF": PLAYOFF_BERTH,
    "KXNCAAFSEED": PLAYOFF_BERTH,
    "KXNCAAFQF": PLAYOFF_BERTH,
    "KXNCAAFSF": PLAYOFF_BERTH,
    "KXNCAAFTFUT": PLAYOFF_BERTH,
    "KXNCAAFBOWLGAME": PLAYOFF_BERTH,
    "KXNCAAF": NATTY,
    "KXNCAAFTOPAPRANK": RANKING,
    "KXNCAAFAPRANK": RANKING,
    "KXNCAAFAPANY": RANKING,
    "KXNCAAFUNDEFEATED": RANKING,
    "KXNCAAFLEADER": PLAYER_PROP,
    "KXNCAAFBIGTENLEADER": PLAYER_PROP,
    "KXNCAAFAWARD": PLAYER_PROP,
    "KXHEISMAN": PLAYER_PROP,
    "KXHEISMANFINALIST": PLAYER_PROP,
    "KXNCAAFCOACHOUT": COACH,
    "KXNCAAFCOACHLEAVE": COACH,
    "KXCOACHOUTNCAAFB": COACH,
    "KXNCAAFCOTY": COACH,
    "KXNCAAFCOTW": COACH,
}

# Polymarket sportsMarketType -> market type.
PM_TYPE = {
    "moneyline": GAME_WINNER,
    "spreads": SPREAD,
    "totals": TOTAL,
    "team_totals": TEAM_STAT,
    "team_touchdowns": TEAM_STAT,
    "anytime_touchdowns": PLAYER_PROP,
}

# A Kalshi game code looks like 26SEP05OHIONEB: two-digit year, month, day,
# then away and home team codes run together. Nebraska is always NEB.
_KALSHI_GAME = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})([A-Z]{4,12})(?=-|$)")
_MONTHS = {m: i for i, m in enumerate(
    "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}
# Polymarket game slug: cfb-<away>-<home>-<YYYY-MM-DD>. Nebraska is neb or nebr.
_PM_GAME = re.compile(r"^cfb-([a-z0-9]+)-([a-z0-9]+)-(\d{4})-(\d{2})-(\d{2})$")
# 2024 events used a dateless slug; their date comes from gameStartTime.
_PM_GAME_UNDATED = re.compile(r"^cfb-([a-z0-9]+)-vs-([a-z0-9]+)$")
_PM_NEB = re.compile(r"^(neb|nebr|nebraska)$")


@dataclass
class Resolved:
    market_type: str
    game_id: str | None = None
    team: str | None = None
    player: str | None = None
    line: float | None = None
    reason: str | None = None      # why it is unresolved, when it is


CODES_FILE = PROJECT_ROOT / "data" / "reference" / "kalshi_team_codes.json.gz"
PM_CODES_FILE = PROJECT_ROOT / "data" / "reference" / "polymarket_team_codes.json.gz"


@lru_cache(maxsize=1)
def _kalshi_codes() -> dict:
    """Kalshi team code -> ESPN team id, learned from the archive.

    Only codes that map to exactly one school are here. Codes Kalshi reuses
    across divisions (WSU, KSU, CSU, WEB, LC) are deliberately absent, so a
    game using one of them resolves to no game rather than the wrong one.
    """
    try:
        with gzip.open(CODES_FILE, "rt") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _abbr(team_id: str) -> str | None:
    from .teams import _load
    by_id, _, _, _ = _load()
    t = by_id.get(team_id)
    return (t.abbr or t.name).upper().replace(" ", "") if t else None


def _kalshi_team(ticker: str) -> str | None:
    """The school a Kalshi market is about, from its own ticker suffix.

    Suffixes look like NEB, NEB275 or NEBOCHA, so digits and any player code
    are trimmed back to the longest segment that is a known team code.
    """
    seg = ticker.rsplit("-", 1)[-1]
    codes = _kalshi_codes()
    seg = re.sub(r"\d+$", "", seg)
    for cut in range(len(seg), 1, -1):
        if seg[:cut] in codes:
            return _abbr(codes[seg[:cut]])
    return None


def _split_kalshi_teams(blob: str) -> tuple[str, str] | None:
    """`OHIONEB` -> the two team codes that make it up.

    Kalshi runs the away and home codes together with no separator, so the
    split is only knowable from the vocabulary of codes actually observed.
    A blob that splits more than one way is refused rather than guessed.
    """
    codes = _kalshi_codes()
    splits = [(blob[:i], blob[i:]) for i in range(2, len(blob) - 1)
              if blob[:i] in codes and blob[i:] in codes]
    return splits[0] if len(splits) == 1 else None


# Event titles come as "Ohio vs Nebraska" or "Washington St. at Utah St.".
# In both forms the first named school is the one that appears first in the
# ticker's team blob, so first is away and second is home.
_KALSHI_TITLE = re.compile(r"^\s*(.+?)\s+(?:vs\.?|at)\s+(.+?)\s*$", re.I)


def _kalshi_game_id_from_title(ticker: str, event_title: str) -> str | None:
    """Game identity from the event title's team names.

    Preferred over splitting the ticker's team blob, because Kalshi reuses
    short codes across divisions: in this archive WSU is Winona State in
    `26AUG27WSUUST` ("Winona State Warriors vs St. Thomas") and Washington
    State in `25DEC22WSUUSU` ("Washington St. at Utah St."). Splitting refuses
    both to avoid picking wrong, which erases 140 Kansas State, 212 Washington
    State and 210 Colorado State markets from any per-school view. The title
    says which school it is, so ask the title.
    """
    m = _KALSHI_GAME.search(ticker)
    t = _KALSHI_TITLE.match(event_title or "")
    if not m or not t:
        return None
    yy, mon, dd, _blob = m.groups()
    if mon not in _MONTHS:
        return None
    # Non-game series append what the market measures: "Washington St. vs
    # Kansas St.: Team Total", "UCF at Kansas St.: Spread". Everything from
    # the colon on describes the market, not the school.
    strip = lambda s: re.sub(r"\s*:.*$", "", s).strip()
    away, home = resolve_team(strip(t.group(1))), resolve_team(strip(t.group(2)))
    if not away.team or not home.team:
        return None
    a, h = _abbr(away.team.id), _abbr(home.team.id)
    if not a or not h or a == h:
        return None
    return f"20{yy}-{_MONTHS[mon]:02d}-{dd}-{a}-{h}"


def _kalshi_game_id(ticker: str) -> str | None:
    """`...-26SEP05OHIONEB-...` -> `2026-09-05-OHIO-NEB`, away then home."""
    m = _KALSHI_GAME.search(ticker)
    if not m:
        return None
    yy, mon, dd, blob = m.groups()
    if mon not in _MONTHS:
        return None
    split = _split_kalshi_teams(blob)
    if not split:
        return None
    away, home = split
    codes = _kalshi_codes()
    a, h = _abbr(codes[away]), _abbr(codes[home])
    if not a or not h:
        return None
    return f"20{yy}-{_MONTHS[mon]:02d}-{dd}-{a}-{h}"


@lru_cache(maxsize=1)
def _pm_codes() -> dict:
    try:
        with gzip.open(PM_CODES_FILE, "rt") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


@lru_cache(maxsize=4096)
def _pm_abbr(slug_code: str) -> str | None:
    """Polymarket slug abbreviation -> canonical abbreviation.

    `nebr` is not a school name, so the slug alone cannot be resolved. The
    event title carries the real names and is what the alias table was built
    from; here the code is matched against that table by way of the team
    resolver, and anything unknown returns nothing rather than a guess.
    """
    codes = _pm_codes()
    if slug_code in codes:
        return _abbr(codes[slug_code])
    from .teams import _load
    by_id, index, _, _ = _load()
    hit = index.get(slug_code)
    if hit and len(hit) == 1:
        return _abbr(next(iter(hit)))
    return None


def _pm_game_id(slug: str, game_start: str | None = None) -> str | None:
    m = _PM_GAME.match(slug or "")
    if m:
        away, home, y, mo, d = m.groups()
    else:
        u = _PM_GAME_UNDATED.match(slug or "")
        if not u or not game_start or len(str(game_start)) < 10:
            return None
        away, home = u.groups()
        y, mo, d = str(game_start)[:10].split("-")
    a, h = _pm_abbr(away), _pm_abbr(home)
    if not a or not h:
        return None
    return f"{y}-{mo}-{d}-{a}-{h}"


def team_in_text(text: str) -> str | None:
    """The one school a market is about, or nothing.

    Only for markets about a single team. A game market involves two, and its
    `game_id` already carries both, so this deliberately returns nothing rather
    than picking one. Written after an earlier version assigned every market
    with a resolved game to Nebraska, which made Nebraska look like 87% of all
    college football money.
    """
    text = (text or "").strip()
    for pat in (r"^(?:Spread|Team Total|Total):\s*([A-Za-z][A-Za-z .'&()-]+?)\s*[(:]",
                r"(?i:^will (?:the )?)([A-Z][A-Za-z .'&()-]+?) (?i:win|beat|have|qualify|be|make|go|finish)\b",
                r"^([A-Z][A-Za-z .'&()-]+?) Team Total\b"):
        m = re.search(pat, text)
        if not m:
            continue
        r = resolve_team(m.group(1).strip())
        if r.team:
            return (r.team.abbr or r.team.name).upper().replace(" ", "")
    return None


def _line_from_title(title: str) -> float | None:
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:\+|or more|points|\))", title or "")
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    m = re.search(r"\((-?\d+(?:\.\d+)?)\)", title or "")
    return float(m.group(1)) if m else None


def resolve_kalshi(market: dict, event_title: str = "") -> Resolved:
    ticker = market.get("ticker") or ""
    family = ticker.split("-")[0]
    mtype = KALSHI_FAMILY.get(family)
    title = market.get("title") or ""
    player = None
    rules = market.get("rules_primary") or ""
    if mtype == PLAYER_PROP and any(a in rules for a in COACH_AWARDS):
        return Resolved(COACH, team=NEBRASKA if "NEB" in ticker.upper() else None)
    if mtype == PLAYER_PROP:
        # The player is the market's own subtitle on these families; the title
        # is the claim ("Nyziah Hunter records the most receiving yards").
        player = (market.get("yes_sub_title") or "").strip() or None
        if not player:
            m = re.match(r"^([A-Z][^,]{1,40}?) (?:records|wins)\b", title)
            player = m.group(1).strip() if m else None
    if mtype is None:
        return Resolved(OTHER, reason=f"unknown Kalshi series family {family!r}")
    # Title first, ticker codes as the fallback and the check.
    game_id = _kalshi_game_id_from_title(ticker, event_title) or _kalshi_game_id(ticker)
    return Resolved(
        market_type=mtype,
        game_id=game_id,
        team=_kalshi_team(ticker) or team_in_text(title),
        player=player,
        line=_line_from_title(title) if mtype in (SPREAD, TOTAL, TEAM_STAT) else None,
    )


def resolve_polymarket(market: dict, event_slug: str = "") -> Resolved:
    smt = market.get("sportsMarketType")
    question = market.get("question") or ""
    slug = event_slug or market.get("slug") or ""
    if smt in PM_TYPE:
        mtype = PM_TYPE[smt]
    elif smt and smt.startswith(("first_half", "second_half", "q1", "q2", "q3", "q4")):
        mtype = OTHER          # period markets: real, but not the season story
    elif smt:
        mtype = OTHER
    else:
        # Futures events carry no sportsMarketType; read the question.
        q = question.lower()
        # Polymarket tags NFL-draft markets about college players with its
        # college-football tag, so they arrive through the sport gate legitimately
        # and are not stray. They are markets on named college athletes but not
        # on college football outcomes, so they get their own type rather than
        # being dropped or folded into the season figures.
        if "draft" in q and ("pro football" in q or "nfl" in q):
            m = re.match(r"^[Ww]ill ([A-Z][\w'.\- ]+?) (?:go|be|get|land)\b", question)
            return Resolved(DRAFT, player=(m.group(1).strip() if m else None))
        # Order matters: a national championship is not a conference one, and
        # an award question names a person where a conference question names a
        # school.
        if "national champion" in q or "national championship" in q:
            mtype = NATTY
        elif re.search(r"\bheisman\b", q):
            mtype = PLAYER_PROP
        elif re.search(r"\b(award|trophy)\b", q):
            # Coach awards are filed with player awards on both platforms.
            mtype = COACH if re.search(r"coach of the year|\bcoach\b", q) else PLAYER_PROP
        elif re.search(r"coach of the year|out as|be fired|next head coach", q):
            mtype = COACH
        elif re.search(r"conference championship|win the \d{4} .+ championship|"
                       r"\b(big ten|sec|acc|big 12|pac-12|sun belt|mountain west|"
                       r"american|conference usa|mac) (conference|championship)", q):
            mtype = CONFERENCE_CHAMP
        elif "wins during the" in q or "win total" in q:
            mtype = SEASON_WINS
        elif "playoff" in q or "qualify" in q or "cfp" in q:
            mtype = PLAYOFF_BERTH
        # Markets created before Polymarket tagged a sportsMarketType phrase
        # the same wagers as plain questions.
        elif re.search(r"\bbeat\b.*\bby \d+(\.\d+)? or more points", q):
            mtype = SPREAD
        elif re.search(r"\bwin by \d+(\.\d+)? or more points", q):
            mtype = SPREAD
        elif re.search(r"combined points scored|combine for \d+(\.\d+)? or more points", q):
            mtype = TOTAL
        elif re.search(r"^will .+ beat .+\?$", q):
            mtype = GAME_WINNER
        elif re.search(r"\bvs\.?\b", q) and _pm_game_id(slug):
            mtype = GAME_WINNER
        else:
            return Resolved(OTHER, game_id=_pm_game_id(slug, market.get("gameStartTime")),
                            team=team_in_text(question),
                            reason="Polymarket question not recognised")
    player = None
    if mtype == PLAYER_PROP:
        if ":" in question:
            player = question.split(":")[0].strip() or None
        else:
            m = re.match(r"^[Ww]ill ([A-Z][\w'.\- ]+?) win\b", question)
            player = m.group(1).strip() if m else None
    line = market.get("line")
    try:
        line = float(line) if line is not None else None
    except (TypeError, ValueError):
        line = None
    game_id = _pm_game_id(slug, market.get("gameStartTime"))
    # Only set when the market is about one school. Game markets involve two,
    # and game_id already names both.
    team = team_in_text(question)
    return Resolved(
        market_type=mtype,
        game_id=game_id,
        team=team,
        player=player,
        line=line if line is not None else _line_from_title(question),
    )
