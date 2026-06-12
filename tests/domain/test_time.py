from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from calendar_backend.domain.time import (
    SystemClock,
    TimeWindow,
    is_minute_aligned,
    require_utc,
    truncate_to_minute,
    validate_time_window,
)


@dataclass(frozen=True)
class FakeClock:
    fixed: datetime

    def now_utc(self) -> datetime:
        return self.fixed


def _utc(y: int, m: int, d: int, h: int, mi: int, s: int = 0, us: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, s, us, tzinfo=UTC)


def test_require_utc_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        require_utc(datetime(2026, 6, 7, 10, 0))


def test_require_utc_rejects_non_utc_offset() -> None:
    eastern = datetime(2026, 6, 7, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        require_utc(eastern)


def test_require_utc_accepts_utc_datetime() -> None:
    dt = _utc(2026, 6, 7, 10, 0)
    assert require_utc(dt) is dt


def test_require_utc_accepts_zero_offset_timezone() -> None:
    dt = datetime(2026, 6, 7, 10, 0, tzinfo=timezone(timedelta(0)))
    assert require_utc(dt) is dt


def test_is_minute_aligned() -> None:
    assert is_minute_aligned(_utc(2026, 6, 7, 10, 0))
    assert not is_minute_aligned(_utc(2026, 6, 7, 10, 0, s=1))
    assert not is_minute_aligned(_utc(2026, 6, 7, 10, 0, us=1))


def test_truncate_to_minute_zeros_sub_minute_fields() -> None:
    dt = _utc(2026, 6, 7, 10, 15, s=30, us=500)
    assert truncate_to_minute(dt) == _utc(2026, 6, 7, 10, 15)


def test_validate_time_window_accepts_valid_half_open_window() -> None:
    window = TimeWindow(start_time=_utc(2026, 6, 7, 9, 0), end_time=_utc(2026, 6, 7, 12, 0))
    validate_time_window(window)


@pytest.mark.parametrize(
    ("start", "end", "match"),
    [
        (_utc(2026, 6, 7, 9, 0, s=1), _utc(2026, 6, 7, 12, 0), "start_time must be minute-aligned"),
        (_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0, us=1), "end_time must be minute-aligned"),
        (_utc(2026, 6, 7, 12, 0), _utc(2026, 6, 7, 9, 0), "start_time must be before end_time"),
        (_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 9, 0), "start_time must be before end_time"),
    ],
)
def test_validate_time_window_rejects_invalid_windows(
    start: datetime, end: datetime, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        validate_time_window(TimeWindow(start_time=start, end_time=end))


def test_validate_time_window_rejects_naive_times() -> None:
    window = TimeWindow(
        start_time=datetime(2026, 6, 7, 9, 0),
        end_time=_utc(2026, 6, 7, 12, 0),
    )
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        validate_time_window(window)


def test_fake_clock_returns_fixed_instant() -> None:
    fixed = _utc(2026, 1, 2, 3, 4)
    assert FakeClock(fixed).now_utc() == fixed


def test_system_clock_returns_utc_aware_datetime() -> None:
    now = SystemClock().now_utc()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
