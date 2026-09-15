# External cron for collect.yml and collect-wide.yml

Neither `collect.yml` nor `collect-wide.yml` has a `schedule:` trigger.
GitHub's own scheduler queue is drained best-effort with no timing
guarantee -- `collect.yml` measured 3-4 hour delays under both `0 10 * * *`
and `17 10 * * *` (issue #1), and that's a repo-wide, provider-side
property, not something particular to that workflow's minute or scope, so
`collect-wide.yml`'s `43 9 * * 2` was dropped on the same reasoning without
waiting to independently measure its lag. Cadence for both is instead
driven by an external clock calling `workflow_dispatch` over the GitHub
REST API. This setup has to be done by hand, once, in a place this repo has
no access to (a third-party account and a credential neither Claude nor
this repo should ever hold).

## 1. Create a token scoped to just this

GitHub Settings -> Developer settings -> **Fine-grained personal access
tokens** -> Generate new token.

- Repository access: **Only select repositories** -> `ne-betting`
- Permissions: **Actions** -> **Read and write** (this is the only
  permission `workflows/{id}/dispatches` needs)
- Expiration: pick something you're willing to renew (fine-grained tokens
  cap at 1 year); note the date somewhere you'll see it, since an expired
  token means silent non-firing, same failure mode this replaced.

Copy the token once -- GitHub won't show it again.

## 2. Register the jobs with a cron service

Any free scheduler that can send an authenticated POST works. Example
using [cron-job.org](https://cron-job.org) (free, no card, per-minute
granularity). Register two separate jobs, one per workflow -- the token
from step 1 covers both since it's scoped to the whole repo, not a single
workflow.

**Daily (`collect.yml`):**

- URL: `https://api.github.com/repos/diepjustin/ne-betting/actions/workflows/collect.yml/dispatches`
- Method: `POST`
- Headers:
  - `Accept: application/vnd.github+json`
  - `X-GitHub-Api-Version: 2022-11-28`
  - `Content-Type: application/json`
  - `Authorization: Bearer <the token from step 1>` -- `Bearer` and the
    space before the token are both part of the value; leaving them off
    is a 401 (visible in cron-job.org's own execution history, not
    anywhere in this repo -- see the last section below).
- Body (raw JSON): `{"ref":"main"}`
- Configured as: daily, **5:43am Central** (10:43 UTC while Central is on
  daylight time) -- after Kalshi's overnight lull, before Saturday
  kickoffs move the numbers.

**Weekly (`collect-wide.yml`):**

- URL: `https://api.github.com/repos/diepjustin/ne-betting/actions/workflows/collect-wide.yml/dispatches`
- Same method, headers, and body as above.
- Configured as: weekly, **Tuesday 5:23am Central** (10:23 UTC while
  Central is on daylight time) -- after the weekend's games have settled,
  before the next slate opens.

Central, not UTC, is the number that actually governs both jobs (see the
time-zone note below) -- so it's stated first. The UTC figures will shift
by an hour when Central daylight time ends (1 Nov 2026 this season): the
jobs keep firing at 5:43/5:23am Central either way, but a `gh run list`
check done in November should expect 11:43/11:23 UTC, not 10:43/10:23.

For both: the exact minute no longer matters; picking one away from `:00`
was only ever a workaround for GitHub's own queue, and this path doesn't
go through it.

cron-job.org defaults a new job's schedule to the account's own local time
zone (`America/Chicago` here), not UTC -- its Hours/Minutes picker is
already in Central, so there's no manual UTC conversion to do when setting
the schedule (the Central times above are what to type directly).

`Content-Type` isn't optional in practice: cron-job.org's own UI prompts
for it the moment the method is set to POST, and a missing one is one more
way this can silently misbehave.

Enter the token directly into the cron service's own header field. Don't
paste it into a chat session, a commit, or anywhere in this repo -- treat
it like any other credential. If a token does end up somewhere it
shouldn't (a screenshot, a paste), revoke it and generate a fresh one
rather than trying to un-expose it.

## 3. Verify it fired

```bash
gh run list --workflow=collect.yml --limit 3 --json databaseId,status,conclusion,createdAt,event
gh run list --workflow=collect-wide.yml --limit 3 --json databaseId,status,conclusion,createdAt,event
```

A successful external trigger shows up with `"event":"workflow_dispatch"`
at (close to) the scheduled minute, instead of `"event":"schedule"` hours
late. First confirmed fire, `collect.yml`: configured for 5:43am Central
(10:43:00 UTC that day), actually dispatched 2026-09-14T10:43:01Z -- one
second off.

## If the token expires or the cron service job gets disabled

The workflow just stops firing -- there's no error surfaced in this repo,
only an absence in `gh run list`. Worth an occasional glance, same as any
external dependency.
