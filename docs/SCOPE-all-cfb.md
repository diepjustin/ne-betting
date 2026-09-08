# Scope: expanding from Nebraska to all of college football

Written 8 Sep 2026, after Justin asked whether the pipeline could cover every
school with filtering by school and player, taking the shape from Cat Murphy's
March Madness reporting.

**Step one, alias resolution, is built.** The rest of this document is a plan,
not a promise, and the numbers in it are measured from the archive rather than
estimated.

---

## 1. What the model story actually does, and what transfers

Her reporting rests on two datasets: a count of how many platforms carry
athlete-specific props, and an analysis of Instagram abuse aimed at players.
The link between them is Charlie Baker's argument that a prop attaches a
name to a bet and invites harassment.

Only the first transfers here. Plan §2 puts social-media scraping in a
separate project with its own ethics review, so the abuse half stays out of
this repo. The availability half is what this pipeline already does for one
school.

## 2. What it costs to widen

Measured from what the collectors already walk past:

| | Nebraska today | All of college football |
|---|---|---|
| Kalshi markets | 221 | 22,524, in the same 33 series |
| Polymarket game events | 3 | 1,624 |
| Polymarket markets | 265 | 42,695, averaging 26 per game |
| Raw archive | 29 MB | roughly 3 GB |

**Discovery is already paid for.** Those 22,524 Kalshi markets stream past on
every run and are discarded. The new cost is metadata and trades per market.
Discovery pages carry each market's volume, so trades need fetching only where
volume is above zero; on Nebraska that was 141 of 228 markets, and across a
full slate of strike-level spread markets it will be a smaller fraction.

Rough shape: a first run of 12 to 24 hours, then cheap daily increments. The
binding constraint is politeness, not the platforms' published limits.

Storage is the real decision. Three gigabytes a season does not belong in git
and does not fit the 90-day artifact pattern comfortably. That question is
open and is Justin's, not mine.

## 3. Step one, done: alias resolution

This gates everything, because a wrong team mapping is invisible in the
output. What the archive showed:

**A platform's team code is not a unique identifier.** Kalshi reuses short
codes across divisions. In the data already collected, `WSU` is both
Washington St. and Winona State, `KSU` is Kansas St. and Kentucky State, `CSU`
is Colorado St. and Central State, `WEB` is Weber St. and Webber
International, and `LC` is Louisiana Christian and Lane. Keying on the code
merges two schools into one number that looks perfectly fine.

So resolution runs on the team **name** each market carries, with the code
used only as a hint inside a single game. Names resolve against ESPN's team
list plus a hand-checked override file.

**A prefix fallback was written, measured, and removed.** Dropping trailing
words resolved 30 more names and got most of them wrong: North Carolina St.
became North Carolina, Louisiana-Monroe became Louisiana, Southern Mississippi
became Southern, Texas Permian Basin became Texas, and Lawrence Tech became
Lawrence. Each merges two real schools. It is gone, and the tests pin the
collisions apart.

Where it landed:

| | |
|---|---|
| Distinct team names mined from the archive | 420 |
| Resolved to a canonical school | 410 (98%) |
| Deliberately unresolved, with a recorded reason | 10 |
| Unexplained | 0 |

The ten are schools absent from ESPN's list (Chicago State, Lawrence Tech,
Texas Wesleyan, Northwestern College Iowa, UT Rio Grande Valley) plus
Roosevelt, which ESPN lists twice under one name. They are named in
`config/team_overrides.yml` with the reason, so nobody repeats the lookup.

Code is in `normalize/teams.py`, overrides in `config/team_overrides.yml`, the
canonical list in `data/reference/espn_teams.json.gz`, tests in
`tests/test_teams.py`.

## 4. Step two, done: the collectors take a scope

Both collectors now accept `--scope all`, and the normalizer identifies games
on both platforms with one joinable key. Measured against the live exchange
rather than estimated:

| | |
|---|---|
| Kalshi college-football markets discovered | 22,564 |
| Of those that have ever traded | 13,791 (61%) |
| Contracts traded across them | 824,128,445 |
| Discovery cost | 49 requests, the same as the Nebraska scope |
| Full first pass, at one request a second | roughly 7.7 hours |
| Games identified on both platforms | 419 |

The volume gate is what makes it affordable: a market that has never traded
has nothing to fetch, and skipping those saves about five hours a pass. Their
metadata is not lost, because the normalizer now reads markets out of the
archived discovery pages.

**The scheduled job has not been switched.** One thing still gates it. State
has moved out of git into the Actions cache, so that objection is gone, but
the first wide pass writes roughly three gigabytes of raw archive and where
that lives is undecided. Daily runs after it are small increments; it is the
first pass that needs an answer, and it should be run by hand anyway.

## 5. Steps that follow

1. **Player identity.** The same problem, harder. Names arrive as
   `yes_sub_title` on Kalshi and inside the question on Polymarket, with
   variants like "Jacory Barney Jr." against "Jacory Barney Jr". No canonical
   roster has been chosen yet. Expect the same discipline: match, then leave
   the residue unresolved rather than guessing two players into one.
2. **Widen the collectors.** Drop the Nebraska filter, keep the football gate,
   fetch trades only where volume is above zero, and run the first pass by
   hand rather than on the schedule.
3. **Decide where raw lives** before the first full run, not after.
4. **Build the site payload.** The contracts project in this repo already
   solves this shape: build a payload, ship it as a workflow artifact rather
   than committing it, serve a static page that filters client-side.

## 6. The fork that is not a technical decision

A public page where anyone picks a school, then a player, and sees the betting
markets carrying that athlete's name is close to the artifact plan §9 says not
to build. By Baker's argument it is also a convenience for the people doing
the harassing. The model story counts props in aggregate and reports the
abuse; it does not ship a per-athlete betting browser.

The split that gets the reporting without shipping the harassment vector:

- **Public**: filter by school, market type and date. Volumes, market counts,
  and how many markets carry an athlete's name.
- **Reporting**: per-player detail, locally, as it works today.

That still supports the finding the current data cannot reach: Nebraska has 24
named players with markets and three with any trade, and whether that is
typical nationally.

## 7. Sportsbook props, revisited

Justin asked on 8 Sep whether the free RotoWire props could be scraped after
all, reversing the earlier decision to keep the project to Kalshi and
Polymarket. Re-probed the same day:

| Source | Result |
|---|---|
| RotoWire public college football props | 200, 685 KB, 297 prop rows across 9 books, embedded in the page |
| Action Network scoreboard | 200, free JSON, 86 games, but the odds array is empty on this endpoint |
| Covers.com odds | 200, 12 MB of HTML, unparsed, terms unchecked |
| ESPN | free and dependable for schedules and rosters, but refuses the project's User-Agent |
| The Odds API | 401, still needs a paid key |
| PrizePicks, DraftKings, BettingPros, Underdog | unchanged, all refuse automated access |

**RotoWire is the only verified free props source, and it is partial.** The
page carried 21 teams and no Nebraska on both days it was checked. It is a
curated selection of marquee games, not the slate.

Used honestly it is a sampler: capture what it publishes each week close to
kickoff and report which players had props at which books, never how many
props existed in total. Anything stronger needs a source we do not have.
