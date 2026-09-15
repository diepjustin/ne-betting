"""The Kalshi step's 200-minute timeout is expected for the ramp-up's first
three or four weeks (METHODOLOGY.md, 12 Sep 2026). This monitor only earns
its keep if it tells that apart from a streak that never stops -- these
tests exercise the classification and streak-counting logic without any
network access (the `gh`-calling functions are exercised only through the
pure functions they feed).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.check_wide_scope_timeouts import step_duration_minutes, streak_of_timeouts, timed_out


def step(conclusion, started, completed):
    return {"name": "Collect Kalshi (scope all)", "conclusion": conclusion,
            "started_at": started, "completed_at": completed}


TIMED_OUT = step("failure", "2026-09-15T10:23:13Z", "2026-09-15T13:43:25Z")  # 200m12s
CRASHED_EARLY = step("failure", "2026-09-08T10:23:00Z", "2026-09-08T10:41:00Z")  # 18m
SUCCEEDED = step("success", "2026-09-08T10:23:00Z", "2026-09-08T12:00:00Z")


def test_step_duration_minutes_reads_the_iso_timestamps():
    assert step_duration_minutes(TIMED_OUT) == 200.2


def test_step_duration_minutes_is_none_without_both_timestamps():
    assert step_duration_minutes({"started_at": None, "completed_at": None}) is None


def test_a_200_minute_failure_is_a_timeout():
    assert timed_out(TIMED_OUT) is True


def test_an_early_failure_is_not_a_timeout():
    """A crash at 18 minutes is a different problem and must not feed the
    same streak a real timeout would -- conflating the two would either mask
    a real bug behind "ramp-up as expected" or falsely alarm on a one-off
    crash."""
    assert timed_out(CRASHED_EARLY) is False


def test_a_successful_run_is_not_a_timeout():
    assert timed_out(SUCCEEDED) is False


def test_a_missing_step_is_not_a_timeout():
    """No Kalshi step data at all (an earlier failure, or data GitHub
    hasn't backfilled yet) is ambiguous, not a hit."""
    assert timed_out(None) is False


def test_streak_counts_consecutive_timeouts_from_the_newest_run():
    steps = [TIMED_OUT, TIMED_OUT, TIMED_OUT, SUCCEEDED, TIMED_OUT]
    assert streak_of_timeouts(steps) == 3


def test_streak_stops_at_a_non_timeout_even_if_earlier_runs_also_timed_out():
    steps = [SUCCEEDED, TIMED_OUT, TIMED_OUT]
    assert streak_of_timeouts(steps) == 0


def test_streak_stops_at_a_missing_step_rather_than_guessing():
    steps = [TIMED_OUT, TIMED_OUT, None, TIMED_OUT]
    assert streak_of_timeouts(steps) == 2


def test_empty_history_is_a_zero_streak():
    assert streak_of_timeouts([]) == 0
