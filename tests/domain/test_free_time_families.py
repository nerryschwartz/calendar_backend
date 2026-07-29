from __future__ import annotations

import uuid
from datetime import UTC, datetime

from calendar_backend.domain.enums import FreeTimeWeekStartDay
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.free_time import (
    combined_gap_blocker_windows,
    effective_activity_block_families,
    eligible_free_time_gaps_for_activity,
    parse_activity_block_families_json,
    serialize_activity_block_families,
)
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.task_families import (
    DEFAULT_BLOCK_FAMILY,
    FREE_TIME_BLOCK_FAMILY,
    BlockPlacementSnapshot,
)
from calendar_backend.domain.time import TimeWindow

RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)
HORIZON_END = datetime(2026, 6, 14, 10, 0, tzinfo=UTC)


def _window(start: datetime, end: datetime) -> TimeWindow:
    return TimeWindow(start_time=start, end_time=end)


def test_effective_activity_block_families_null_means_free_time_and_default() -> None:
    assert effective_activity_block_families(None) == (
        FREE_TIME_BLOCK_FAMILY,
        DEFAULT_BLOCK_FAMILY,
    )
    assert effective_activity_block_families("") == (
        FREE_TIME_BLOCK_FAMILY,
        DEFAULT_BLOCK_FAMILY,
    )


def test_effective_activity_block_families_transit_appends_free_time() -> None:
    stored = serialize_activity_block_families(("transit",))
    assert stored is not None
    assert effective_activity_block_families(stored) == (
        FREE_TIME_BLOCK_FAMILY,
        "transit",
    )


def test_effective_activity_block_families_explicit_free_time_only() -> None:
    stored = serialize_activity_block_families((FREE_TIME_BLOCK_FAMILY,))
    assert stored is not None
    assert effective_activity_block_families(stored) == (FREE_TIME_BLOCK_FAMILY,)


def test_parse_activity_block_families_rejects_invalid_json() -> None:
    result = parse_activity_block_families_json("{")
    assert isinstance(result, ServiceMessage)
    assert result.code == MessageCode.INVALID_ALLOWED_BLOCK_FAMILIES


def test_eligible_gaps_re_add_transit_block_window() -> None:
    task_block = _window(RUN_AT, RUN_AT.replace(hour=11))
    block_window = _window(RUN_AT.replace(hour=11), RUN_AT.replace(hour=12))
    combined = combined_gap_blocker_windows((task_block,), (block_window,))
    placements = (
        BlockPlacementSnapshot(
            family="transit",
            window=block_window,
            source_plan_id=PlanID(uuid.UUID("00000000-0000-4000-8000-000000000001")),
        ),
    )
    gaps = eligible_free_time_gaps_for_activity(
        run_started_at=RUN_AT,
        master_horizon_end=HORIZON_END,
        week_start_day=FreeTimeWeekStartDay.MONDAY,
        local_timezone="UTC",
        combined_blockers=combined,
        task_blockers=(task_block,),
        allowed_families=(FREE_TIME_BLOCK_FAMILY, "transit"),
        placements=placements,
    )
    assert any(
        gap.start_time <= block_window.start_time and gap.end_time >= block_window.end_time
        for gap in gaps
    )


def test_eligible_gaps_free_time_only_excludes_default_regions() -> None:
    focus_block = _window(RUN_AT.replace(hour=12), RUN_AT.replace(hour=13))
    combined = combined_gap_blocker_windows((), (focus_block,))
    placements = (
        BlockPlacementSnapshot(
            family="focus",
            window=focus_block,
            source_plan_id=PlanID(uuid.UUID("00000000-0000-4000-8000-000000000002")),
        ),
    )
    gaps = eligible_free_time_gaps_for_activity(
        run_started_at=RUN_AT,
        master_horizon_end=HORIZON_END,
        week_start_day=FreeTimeWeekStartDay.MONDAY,
        local_timezone="UTC",
        combined_blockers=combined,
        task_blockers=(),
        allowed_families=(FREE_TIME_BLOCK_FAMILY,),
        placements=placements,
    )
    for gap in gaps:
        assert not (gap.start_time < focus_block.end_time and gap.end_time > focus_block.start_time)
