#!/usr/bin/env python3
"""Flag collect-wide.yml if its Kalshi step has timed out too many weeks running.

    uv run python scripts/check_wide_scope_timeouts.py [--threshold N] [--lookback N]

The Kalshi step's 200-minute timeout is *expected* to fire for the first
three or four weekly runs while the wide-scope pass ramps up (METHODOLOGY.md,
12 Sep 2026) -- this script only exists to catch it not stopping after that.
A streak past the threshold means the ramp-up may not be converging and is
worth a human look; it does not mean this week's run itself did anything
wrong. Runs as the last step of collect-wide.yml's job (`if: always()`),
after state is already saved, so it never gates the collectors themselves.

Reads recent run history with `gh`, so this only makes sense inside a
GitHub Actions job that already has a GH_TOKEN with `actions: read` and
`issues: write` (see collect-wide.yml's `permissions:` block). Any failure
to reach the API is swallowed and printed rather than failing the job --
this is a monitor, not a gate.

One accepted simplification: any completed run of this workflow counts
toward the streak, including a manually re-triggered debugging run, not
only the weekly cadence one. Rare enough in practice not to be worth
distinguishing.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime

REPO = "diepjustin/ne-betting"
WORKFLOW = "collect-wide.yml"
STEP_NAME = "Collect Kalshi (scope all)"
TIMEOUT_MINUTES = 200
# A run that failed at 199m40s is the same timeout; one that failed at 40
# minutes is a different problem (a crash, not a timeout) and should not
# feed this streak.
TIMEOUT_TOLERANCE_MINUTES = 5
ISSUE_TITLE = "Wide-scope Kalshi pass hasn't converged"
ISSUE_MARKER = "<!-- wide-scope-timeout-monitor -->"


def _run_gh(args: list[str]) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    return result.stdout


def fetch_recent_runs(repo: str, workflow: str, limit: int) -> list[dict]:
    out = _run_gh(["run", "list", f"--repo={repo}", f"--workflow={workflow}",
                   f"--limit={limit}", "--json",
                   "databaseId,status,conclusion,createdAt,event"])
    return [r for r in json.loads(out) if r["status"] == "completed"]


def fetch_kalshi_step(repo: str, run_id: int, step_name: str) -> dict | None:
    out = _run_gh(["api", f"repos/{repo}/actions/runs/{run_id}/jobs"])
    for job in json.loads(out).get("jobs", []):
        for step in job.get("steps", []):
            if step.get("name") == step_name:
                return step
    return None


def step_duration_minutes(step: dict) -> float | None:
    started, completed = step.get("started_at"), step.get("completed_at")
    if not started or not completed:
        return None
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    delta = datetime.strptime(completed, fmt) - datetime.strptime(started, fmt)
    return delta.total_seconds() / 60


def timed_out(step: dict | None, timeout_minutes: float = TIMEOUT_MINUTES,
              tolerance_minutes: float = TIMEOUT_TOLERANCE_MINUTES) -> bool:
    """True if `step` looks cut off by its own timeout-minutes, not some
    other failure."""
    if step is None or step.get("conclusion") != "failure":
        return False
    duration = step_duration_minutes(step)
    if duration is None:
        return False
    return duration >= (timeout_minutes - tolerance_minutes)


def streak_of_timeouts(steps: list[dict | None]) -> int:
    """`steps` ordered newest-first. Counts consecutive timeouts starting
    from the most recent run; stops at the first run that either did not
    time out or has no step data (ambiguous -- ends the streak rather than
    extending it on a guess)."""
    streak = 0
    for step in steps:
        if not timed_out(step):
            break
        streak += 1
    return streak


def find_tracking_issue(repo: str) -> int | None:
    out = _run_gh(["issue", "list", f"--repo={repo}", "--state=open",
                   "--json=number,title"])
    for issue in json.loads(out):
        if issue["title"] == ISSUE_TITLE:
            return issue["number"]
    return None


def upsert_tracking_issue(repo: str, streak: int, threshold: int, run_ids: list[int]) -> str:
    links = ", ".join(f"https://github.com/{repo}/actions/runs/{i}" for i in run_ids)
    body = (f"{ISSUE_MARKER}\n"
            f"`{WORKFLOW}`'s `{STEP_NAME}` step has hit its "
            f"{TIMEOUT_MINUTES}-minute timeout {streak} weeks in a row, past "
            f"the {threshold}-week alert threshold and past METHODOLOGY.md's "
            f"own 3-4 week ramp-up estimate. Recent runs: {links}.\n\n"
            "Worth checking whether the wide-scope pass is actually "
            "converging (shrinking backlog each week) or stuck (same or "
            "growing backlog).")
    issue_number = find_tracking_issue(repo)
    if issue_number is None:
        out = _run_gh(["issue", "create", f"--repo={repo}", f"--title={ISSUE_TITLE}",
                       f"--body={body}"])
        return f"opened: {out.strip()}"
    _run_gh(["issue", "comment", str(issue_number), f"--repo={repo}", f"--body={body}"])
    return f"commented on #{issue_number}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--workflow", default=WORKFLOW)
    ap.add_argument("--threshold", type=int, default=5)
    ap.add_argument("--lookback", type=int, default=12,
                    help="how many recent completed runs to inspect")
    args = ap.parse_args(argv)

    try:
        runs = fetch_recent_runs(args.repo, args.workflow, args.lookback)
        steps = [fetch_kalshi_step(args.repo, r["databaseId"], STEP_NAME) for r in runs]
        streak = streak_of_timeouts(steps)
        result = {"streak": streak, "threshold": args.threshold, "action": "none"}
        if streak >= args.threshold:
            run_ids = [r["databaseId"] for r in runs[:streak]]
            result["action"] = upsert_tracking_issue(args.repo, streak, args.threshold, run_ids)
    except subprocess.CalledProcessError as e:
        print(f"monitor check failed, not blocking the job: {e.stderr}", file=sys.stderr)
        return 0

    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
