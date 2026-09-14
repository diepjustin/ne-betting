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
  - `Authorization: Bearer <the token from step 1>`
- Body (raw JSON): `{"ref":"main"}`
- Schedule: daily, ~10:00-11:00 UTC (5-6am Central) -- after Kalshi's
  overnight lull, before Saturday kickoffs move the numbers.

**Weekly (`collect-wide.yml`):**

- URL: `https://api.github.com/repos/diepjustin/ne-betting/actions/workflows/collect-wide.yml/dispatches`
- Same method, headers, and body as above.
- Schedule: weekly, Tuesday ~09:00-10:00 UTC -- after the weekend's games
  have settled, before the next slate opens.

For both: the exact minute no longer matters; picking one away from `:00`
was only ever a workaround for GitHub's own queue, and this path doesn't
go through it.

Enter the token directly into the cron service's own header field. Don't
paste it into a chat session, a commit, or anywhere in this repo -- treat
it like any other credential.

## 3. Verify it fired

```bash
gh run list --workflow=collect.yml --limit 3 --json databaseId,status,conclusion,createdAt,event
gh run list --workflow=collect-wide.yml --limit 3 --json databaseId,status,conclusion,createdAt,event
```

A successful external trigger shows up with `"event":"workflow_dispatch"`
at (close to) the scheduled minute, instead of `"event":"schedule"` hours
late.

## If the token expires or the cron service job gets disabled

The workflow just stops firing -- there's no error surfaced in this repo,
only an absence in `gh run list`. Worth an occasional glance, same as any
external dependency.
