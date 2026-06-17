from __future__ import annotations

from datetime import UTC, datetime

from calendar_backend.domain.constraints import merge_or_windows, validate_user_group_windows
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
