from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


@dataclass(frozen=True)
class TimeWindow:
    """Half-open interval [start_time, end_time)."""

    start_time: datetime
    end_time: datetime


def require_utc(dt: datetime) -> datetime:
    """Return *dt* after validating it is timezone-aware UTC."""
    offset = dt.utcoffset()
    if dt.tzinfo is None or offset is None:
        raise ValueError("datetime must be timezone-aware UTC")
    if offset.total_seconds() != 0:
        raise ValueError("datetime must be timezone-aware UTC")
    return dt


def truncate_to_minute(dt: datetime) -> datetime:
    """Zero seconds and microseconds. Normalization only; does not validate UTC."""
    return dt.replace(second=0, microsecond=0)


def is_minute_aligned(dt: datetime) -> bool:
    return dt.second == 0 and dt.microsecond == 0


def gaps_in_window(
    window: TimeWindow,
    blockers: tuple[TimeWindow, ...],
) -> tuple[tuple[datetime, datetime], ...]:
    """Return gap intervals within *window* not covered by overlapping *blockers*."""
    blocking = sorted(
        (
            segment
            for segment in blockers
            if segment.start_time < window.end_time and segment.end_time > window.start_time
        ),
        key=lambda segment: segment.start_time,
    )

    gaps: list[tuple[datetime, datetime]] = []
    cursor = window.start_time
    for segment in blocking:
        gap_end = min(segment.start_time, window.end_time)
        if cursor < gap_end:
            gaps.append((cursor, gap_end))
        cursor = max(cursor, segment.end_time)
        if cursor >= window.end_time:
            break

    if cursor < window.end_time:
        gaps.append((cursor, window.end_time))

    return tuple(gaps)


def validate_time_window(window: TimeWindow) -> None:
    """Reject invalid windows. Does not mutate or truncate sub-minute values."""
    require_utc(window.start_time)
    require_utc(window.end_time)
    if not is_minute_aligned(window.start_time):
        raise ValueError("start_time must be minute-aligned")
    if not is_minute_aligned(window.end_time):
        raise ValueError("end_time must be minute-aligned")
    if window.start_time >= window.end_time:
        raise ValueError("start_time must be before end_time")


class Clock(Protocol):
    def now_utc(self) -> datetime: ...


class SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(UTC)
