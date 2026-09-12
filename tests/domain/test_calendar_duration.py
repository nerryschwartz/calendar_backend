from datetime import UTC, datetime

import pytest
from calendar_backend.domain.calendar_duration import CalendarDuration


@pytest.mark.parametrize(
    ("start", "duration", "expected"),
    [
        ("2024-02-29T12:00:00Z", CalendarDuration(years=1), "2025-02-28T12:00:00Z"),
        ("2026-01-31T12:00:00Z", CalendarDuration(months=1), "2026-02-28T12:00:00Z"),
        ("2026-03-07T08:30:00Z", CalendarDuration(days=1), "2026-03-08T08:30:00Z"),
        ("2026-10-31T06:30:00Z", CalendarDuration(days=1), "2026-11-01T06:30:00Z"),
        ("2026-11-01T07:30:00Z", CalendarDuration(minutes=1), "2026-11-01T07:31:00Z"),
        ("2026-09-12T12:00:00Z", CalendarDuration(years=2), "2028-09-12T12:00:00Z"),
    ],
)
def test_calendar_duration_month_ends_and_dst(start, duration, expected):
    assert duration.end_at(
        datetime.fromisoformat(start), "America/Chicago"
    ) == datetime.fromisoformat(expected)


@pytest.mark.parametrize("parts", [{}, {"days": -1}, {"years": 1.5}, {"minutes": True}])
def test_calendar_duration_rejects_invalid_parts(parts):
    with pytest.raises(ValueError):
        CalendarDuration(**parts)


def test_calendar_duration_overflow():
    with pytest.raises((ValueError, OverflowError)):
        CalendarDuration(years=10000).end_at(datetime(2026, 1, 1, tzinfo=UTC), "UTC")
