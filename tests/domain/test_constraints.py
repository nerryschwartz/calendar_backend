from __future__ import annotations

from datetime import UTC, datetime

from calendar_backend.domain.constraints import (
    intersect_time_windows,
    merge_or_windows,
    validate_user_group_windows,
)
from calendar_backend.domain.errors import MessageCode
from calendar_backend.domain.time import TimeWindow


def _utc(y: int, m: int, d: int, h: int, mi: int, s: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, s, tzinfo=UTC)


def _window(start: datetime, end: datetime) -> TimeWindow:
    return TimeWindow(start_time=start, end_time=end)


def test_validate_user_group_windows_rejects_empty_tuple() -> None:
    result = validate_user_group_windows(())

    assert result is not None
    assert result.code == MessageCode.EMPTY_CONSTRAINT_GROUP


def test_validate_user_group_windows_accepts_valid_windows() -> None:
    windows = (
        _window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),
        _window(_utc(2026, 6, 7, 14, 0), _utc(2026, 6, 7, 16, 0)),
    )

    assert validate_user_group_windows(windows) is None


def test_validate_user_group_windows_rejects_invalid_window() -> None:
    windows = (_window(_utc(2026, 6, 7, 9, 0, s=30), _utc(2026, 6, 7, 12, 0)),)

    result = validate_user_group_windows(windows)

    assert result is not None
    assert result.code == MessageCode.NON_MINUTE_ALIGNED_WINDOW
    assert result.details["window_index"] == "0"


def test_validate_user_group_windows_rejects_inverted_window() -> None:
    windows = (_window(_utc(2026, 6, 7, 12, 0), _utc(2026, 6, 7, 9, 0)),)

    result = validate_user_group_windows(windows)

    assert result is not None
    assert result.code == MessageCode.INVALID_TIME_WINDOW


def test_validate_user_group_windows_rejects_naive_datetime() -> None:
    windows = (_window(datetime(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),)

    result = validate_user_group_windows(windows)

    assert result is not None
    assert result.code == MessageCode.INVALID_TIME_WINDOW


def test_merge_or_windows_returns_empty_for_empty_input() -> None:
    assert merge_or_windows(()) == ()


def test_merge_or_windows_merges_touching_intervals() -> None:
    windows = (
        _window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),
        _window(_utc(2026, 6, 7, 12, 0), _utc(2026, 6, 7, 15, 0)),
    )

    merged = merge_or_windows(windows)

    assert merged == (_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 15, 0)),)


def test_merge_or_windows_merges_overlapping_intervals() -> None:
    windows = (
        _window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 14, 0)),
        _window(_utc(2026, 6, 7, 12, 0), _utc(2026, 6, 7, 16, 0)),
    )

    merged = merge_or_windows(windows)

    assert merged == (_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 16, 0)),)


def test_merge_or_windows_keeps_separate_non_touching_intervals() -> None:
    windows = (
        _window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),
        _window(_utc(2026, 6, 7, 13, 0), _utc(2026, 6, 7, 15, 0)),
    )

    merged = merge_or_windows(windows)

    assert merged == windows


def test_merge_or_windows_sorts_before_merge() -> None:
    windows = (
        _window(_utc(2026, 6, 7, 12, 0), _utc(2026, 6, 7, 15, 0)),
        _window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),
    )

    merged = merge_or_windows(windows)

    assert merged == (_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 15, 0)),)


def test_intersect_time_windows_returns_empty_when_either_side_empty() -> None:
    left = (_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),)
    assert intersect_time_windows((), left) == ()
    assert intersect_time_windows(left, ()) == ()


def test_intersect_time_windows_returns_empty_when_ranges_do_not_overlap() -> None:
    left = (_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),)
    right = (_window(_utc(2026, 6, 7, 13, 0), _utc(2026, 6, 7, 15, 0)),)

    assert intersect_time_windows(left, right) == ()


def test_intersect_time_windows_returns_overlap_for_partial_intersection() -> None:
    left = (_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 14, 0)),)
    right = (_window(_utc(2026, 6, 7, 11, 0), _utc(2026, 6, 7, 16, 0)),)

    assert intersect_time_windows(left, right) == (
        _window(_utc(2026, 6, 7, 11, 0), _utc(2026, 6, 7, 14, 0)),
    )


def test_intersect_time_windows_returns_empty_for_touching_half_open_boundaries() -> None:
    left = (_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),)
    right = (_window(_utc(2026, 6, 7, 12, 0), _utc(2026, 6, 7, 15, 0)),)

    assert intersect_time_windows(left, right) == ()


def test_intersect_time_windows_combines_multiple_intervals() -> None:
    left = (
        _window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),
        _window(_utc(2026, 6, 7, 14, 0), _utc(2026, 6, 7, 17, 0)),
    )
    right = (
        _window(_utc(2026, 6, 7, 10, 0), _utc(2026, 6, 7, 13, 0)),
        _window(_utc(2026, 6, 7, 15, 0), _utc(2026, 6, 7, 16, 0)),
    )

    assert intersect_time_windows(left, right) == (
        _window(_utc(2026, 6, 7, 10, 0), _utc(2026, 6, 7, 12, 0)),
        _window(_utc(2026, 6, 7, 15, 0), _utc(2026, 6, 7, 16, 0)),
    )
