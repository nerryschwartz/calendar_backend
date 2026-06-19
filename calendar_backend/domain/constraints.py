"""Pure constraint-group validation and OR-window normalization for write paths.

Plan constraints use AND-of-OR semantics: each group is an OR of windows; merge
applies within one group only (not across groups on a plan). Not an ORM invariant
entry point — persisted-shape checks live in ``domain/invariant_validation.py``
([repo convention §9]).
"""

from __future__ import annotations

from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.time import TimeWindow, validate_time_window


def validate_user_group_windows(windows: tuple[TimeWindow, ...]) -> ServiceMessage | None:
    """Reject empty groups and invalid windows. Does not merge."""
    if not windows:
        return ServiceMessage(
            code=MessageCode.EMPTY_CONSTRAINT_GROUP,
            message="USER constraint group must contain at least one window",
            details={},
        )

    for index, window in enumerate(windows):
        try:
            validate_time_window(window)
        except ValueError as exc:
            message = str(exc)
            details = {"window_index": str(index)}
            if "minute-aligned" in message:
                return ServiceMessage(
                    code=MessageCode.NON_MINUTE_ALIGNED_WINDOW,
                    message=message,
                    details=details,
                )
            return ServiceMessage(
                code=MessageCode.INVALID_TIME_WINDOW,
                message=message,
                details=details,
            )

    return None


def merge_or_windows(windows: tuple[TimeWindow, ...]) -> tuple[TimeWindow, ...]:
    """Merge overlapping or touching half-open windows. Inputs must already be valid."""
    if not windows:
        return ()

    ordered = sorted(windows, key=lambda window: window.start_time)
    merged: list[TimeWindow] = [ordered[0]]

    for window in ordered[1:]:
        last = merged[-1]
        if window.start_time <= last.end_time:
            merged[-1] = TimeWindow(
                start_time=last.start_time,
                end_time=max(last.end_time, window.end_time),
            )
        else:
            merged.append(window)

    return tuple(merged)
