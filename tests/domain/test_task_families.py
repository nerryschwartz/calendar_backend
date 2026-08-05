from __future__ import annotations

import uuid
from datetime import UTC, datetime

from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.task_families import (
    DEFAULT_BLOCK_FAMILY,
    BlockPlacementSnapshot,
    DownstreamTaskFeasibilitySummary,
    block_placements_from_windows,
    effective_allowed_block_families,
    narrow_task_effective_windows,
    parse_allowed_block_families_json,
    serialize_allowed_block_families,
    total_downstream_feasible_minutes,
    validate_allowed_block_families_for_write,
)
from calendar_backend.domain.time import TimeWindow


def _utc(y: int, m: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def _window(start: datetime, end: datetime) -> TimeWindow:
    return TimeWindow(start_time=start, end_time=end)


def test_effective_allowed_block_families_null_means_default() -> None:
    assert effective_allowed_block_families(None) == (DEFAULT_BLOCK_FAMILY,)
    assert effective_allowed_block_families("") == (DEFAULT_BLOCK_FAMILY,)
    assert effective_allowed_block_families("   ") == (DEFAULT_BLOCK_FAMILY,)


def test_parse_allowed_block_families_json_dedupes_and_sorts() -> None:
    parsed = parse_allowed_block_families_json('["transit", "default", "transit"]')
    assert parsed == ("default", "transit")


def test_parse_rejects_free_time() -> None:
    result = parse_allowed_block_families_json('["free-time"]')
    assert isinstance(result, ServiceMessage)
    assert result.code == MessageCode.INVALID_ALLOWED_BLOCK_FAMILIES


def test_validate_allowed_block_families_for_write_rejects_free_time() -> None:
    error = validate_allowed_block_families_for_write(("free-time",))
    assert error is not None
    assert error.code == MessageCode.INVALID_ALLOWED_BLOCK_FAMILIES


def test_serialize_allowed_block_families_null_when_empty() -> None:
    assert serialize_allowed_block_families(()) is None


def test_narrow_default_only_subtracts_non_default_blocks() -> None:
    base = (_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),)
    placements = (
        BlockPlacementSnapshot(
            family="transit",
            window=_window(_utc(2026, 6, 7, 10, 0), _utc(2026, 6, 7, 11, 0)),
            source_plan_id=PlanID(uuid.UUID("00000000-0000-4000-8000-000000000001")),
        ),
    )
    narrowed = narrow_task_effective_windows(base, (DEFAULT_BLOCK_FAMILY,), placements)
    assert narrowed == (
        _window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 10, 0)),
        _window(_utc(2026, 6, 7, 11, 0), _utc(2026, 6, 7, 12, 0)),
    )


def test_narrow_transit_only_uses_transit_placements() -> None:
    base = (_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),)
    placements = (
        BlockPlacementSnapshot(
            family="transit",
            window=_window(_utc(2026, 6, 7, 10, 0), _utc(2026, 6, 7, 11, 0)),
            source_plan_id=PlanID(uuid.UUID("00000000-0000-4000-8000-000000000001")),
        ),
    )
    narrowed = narrow_task_effective_windows(base, ("transit",), placements)
    assert narrowed == (_window(_utc(2026, 6, 7, 10, 0), _utc(2026, 6, 7, 11, 0)),)


def test_narrow_transit_and_default_unions_regions() -> None:
    base = (_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),)
    placements = (
        BlockPlacementSnapshot(
            family="transit",
            window=_window(_utc(2026, 6, 7, 10, 0), _utc(2026, 6, 7, 11, 0)),
            source_plan_id=PlanID(uuid.UUID("00000000-0000-4000-8000-000000000001")),
        ),
    )
    narrowed = narrow_task_effective_windows(base, ("transit", DEFAULT_BLOCK_FAMILY), placements)
    assert narrowed == base


def test_total_downstream_feasible_minutes_counts_narrowed_volume() -> None:
    summary = DownstreamTaskFeasibilitySummary(
        plan_id=PlanID(uuid.UUID("00000000-0000-4000-8000-000000000002")),
        allowed_block_families=("transit",),
        base_effective_windows=(_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),),
    )
    placements = (
        BlockPlacementSnapshot(
            family="transit",
            window=_window(_utc(2026, 6, 7, 10, 0), _utc(2026, 6, 7, 11, 0)),
            source_plan_id=PlanID(uuid.UUID("00000000-0000-4000-8000-000000000001")),
        ),
    )
    assert total_downstream_feasible_minutes((summary,), placements) == 60


def test_block_placements_from_windows_is_deterministic() -> None:
    plan_a = PlanID(uuid.UUID("00000000-0000-4000-8000-000000000001"))
    plan_b = PlanID(uuid.UUID("00000000-0000-4000-8000-000000000002"))
    window = _window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 10, 0))
    placements = block_placements_from_windows(
        block_family_by_plan_id={plan_a: "transit", plan_b: "focus"},
        windows_by_plan_id={plan_b: (window,), plan_a: (window,)},
    )
    assert len(placements) == 2
    assert placements[0].family == "focus"
    assert placements[1].family == "transit"
