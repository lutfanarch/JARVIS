"""Unit tests for the DST‑aware scheduler.

These tests verify that the scheduler computes the correct next run
time across daylight saving boundaries for the America/New_York
timezone.  They do not rely on any network or external services.
"""

from datetime import datetime, timezone

from informer.orchestration.scheduler import compute_next_run


def test_next_run_before_dst_start() -> None:
    """The next run before DST starts should use UTC‑4 offset."""
    now = datetime(2026, 3, 7, 12, 0, tzinfo=timezone.utc)
    next_run = compute_next_run(now=now, tz_name="America/New_York")
    # DST in New York begins on 2026‑03‑08 at 02:00.  The next
    # scheduled weekday after 2026‑03‑07 (Saturday) is Monday
    # 2026‑03‑09.  At 10:15 EDT (UTC‑4) the time in UTC is 14:15.
    assert next_run == datetime(2026, 3, 9, 14, 15, tzinfo=timezone.utc)


def test_next_run_after_dst_end() -> None:
    """The next run after DST ends should use UTC‑5 offset."""
    now = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
    next_run = compute_next_run(now=now, tz_name="America/New_York")
    # DST in New York ends on 2026‑11‑01 at 02:00.  The next
    # scheduled weekday after 2026‑10‑31 (Saturday) is Monday
    # 2026‑11‑02.  At 10:15 EST (UTC‑5) the time in UTC is 15:15.
    assert next_run == datetime(2026, 11, 2, 15, 15, tzinfo=timezone.utc)