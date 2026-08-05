"""Block family validation and task window narrowing per V2 design §7."""

from __future__ import annotations

import json
from dataclasses import dataclass

from calendar_backend.domain.constraints import intersect_time_windows, merge_or_windows
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.time import TimeWindow, gaps_in_window

DEFAULT_BLOCK_FAMILY = "default"
FREE_TIME_BLOCK_FAMILY = "free-time"


@dataclass(frozen=True)
class BlockPlacementSnapshot:
    family: str
    window: TimeWindow
    source_plan_id: PlanID


@dataclass(frozen=True)
class DownstreamTaskFeasibilitySummary:
    plan_id: PlanID
    allowed_block_families: tuple[str, ...]
    base_effective_windows: tuple[TimeWindow, ...]


def effective_allowed_block_families(stored: str | None) -> tuple[str, ...]:
    """Return effective families for narrowing; null/empty stored means default-only."""
    if stored is None or stored.strip() == "":
        return (DEFAULT_BLOCK_FAMILY,)
    parsed = parse_allowed_block_families_json(stored)
    if isinstance(parsed, ServiceMessage):
        return (DEFAULT_BLOCK_FAMILY,)
    return parsed


def parse_allowed_block_families_json(
    stored: str | None,
) -> tuple[str, ...] | ServiceMessage:
    if stored is None or stored.strip() == "":
        return ()
    try:
        raw = json.loads(stored)
    except json.JSONDecodeError:
        return _invalid_allowed_families(
            "allowed_block_families must be a JSON array of strings", stored
        )

    if not isinstance(raw, list):
        return _invalid_allowed_families(
            "allowed_block_families must be a JSON array of strings", stored
        )

    normalized: list[str] = []
    seen: set[str] = set()
    error: ServiceMessage | None = None
    for index, item in enumerate(raw):
        if not isinstance(item, str):
            error = _invalid_allowed_families(
                "allowed_block_families entries must be strings",
                stored,
                index=index,
            )
            break
        family = item.strip()
        if not family:
            error = _invalid_allowed_families(
                "allowed_block_families entries must be non-empty strings",
                stored,
                index=index,
            )
            break
        if family == FREE_TIME_BLOCK_FAMILY:
            error = _invalid_allowed_families(
                'allowed_block_families must not contain "free-time"',
                stored,
                index=index,
            )
            break
        if family not in seen:
            seen.add(family)
            normalized.append(family)
    if error is not None:
        return error
    return tuple(sorted(normalized, key=lambda value: (value != DEFAULT_BLOCK_FAMILY, value)))


def _invalid_allowed_families(
    message: str,
    stored: str,
    *,
    index: int | None = None,
) -> ServiceMessage:
    details: dict[str, str] = {"stored": stored}
    if index is not None:
        details["index"] = str(index)
    return ServiceMessage(
        code=MessageCode.INVALID_ALLOWED_BLOCK_FAMILIES,
        message=message,
        details=details,
    )


def validate_allowed_block_families_for_write(
    families: tuple[str, ...],
) -> ServiceMessage | None:
    if not families:
        return None
    for index, family in enumerate(families):
        if not family.strip():
            return ServiceMessage(
                code=MessageCode.INVALID_ALLOWED_BLOCK_FAMILIES,
                message="allowed_block_families entries must be non-empty strings",
                details={"index": str(index)},
            )
        if family == FREE_TIME_BLOCK_FAMILY:
            return ServiceMessage(
                code=MessageCode.INVALID_ALLOWED_BLOCK_FAMILIES,
                message='allowed_block_families must not contain "free-time"',
                details={"index": str(index)},
            )
    return None


def serialize_allowed_block_families(families: tuple[str, ...]) -> str | None:
    validation_error = validate_allowed_block_families_for_write(families)
    if validation_error is not None:
        raise ValueError(validation_error.message)
    if not families:
        return None
    deduped = tuple(
        sorted(
            {family.strip() for family in families},
            key=lambda value: (value != DEFAULT_BLOCK_FAMILY, value),
        )
    )
    return json.dumps(list(deduped))


def _window_minutes(window: TimeWindow) -> int:
    return int((window.end_time - window.start_time).total_seconds() // 60)


def narrow_task_effective_windows(
    base_windows: tuple[TimeWindow, ...],
    allowed_families: tuple[str, ...],
    placements: tuple[BlockPlacementSnapshot, ...],
) -> tuple[TimeWindow, ...]:
    if not base_windows:
        return ()

    non_default_blockers = tuple(
        placement.window for placement in placements if placement.family != DEFAULT_BLOCK_FAMILY
    )
    family_regions: list[TimeWindow] = []

    for family in allowed_families:
        if family == DEFAULT_BLOCK_FAMILY:
            for window in base_windows:
                for gap_start, gap_end in gaps_in_window(window, non_default_blockers):
                    family_regions.append(TimeWindow(start_time=gap_start, end_time=gap_end))
        else:
            family_placements = tuple(
                placement.window for placement in placements if placement.family == family
            )
            if family_placements:
                merged = merge_or_windows(family_placements)
                family_regions.extend(intersect_time_windows(base_windows, merged))

    return merge_or_windows(tuple(family_regions))


def block_placements_from_windows(
    *,
    block_family_by_plan_id: dict[PlanID, str],
    windows_by_plan_id: dict[PlanID, tuple[TimeWindow, ...]],
) -> tuple[BlockPlacementSnapshot, ...]:
    snapshots: list[BlockPlacementSnapshot] = []
    for plan_id in sorted(windows_by_plan_id, key=str):
        family = block_family_by_plan_id.get(plan_id)
        if family is None:
            continue
        for window in windows_by_plan_id[plan_id]:
            snapshots.append(
                BlockPlacementSnapshot(family=family, window=window, source_plan_id=plan_id)
            )
    return tuple(
        sorted(
            snapshots,
            key=lambda snapshot: (
                snapshot.window.start_time,
                snapshot.window.end_time,
                snapshot.family,
                str(snapshot.source_plan_id),
            ),
        )
    )


def total_downstream_feasible_minutes(
    summaries: tuple[DownstreamTaskFeasibilitySummary, ...],
    placements: tuple[BlockPlacementSnapshot, ...],
) -> int:
    total = 0
    for summary in summaries:
        narrowed = narrow_task_effective_windows(
            summary.base_effective_windows,
            summary.allowed_block_families,
            placements,
        )
        total += sum(_window_minutes(window) for window in narrowed)
    return total


def demand_windows_for_block_family(
    summaries: tuple[DownstreamTaskFeasibilitySummary, ...],
    block_family: str,
) -> tuple[TimeWindow, ...]:
    regions: list[TimeWindow] = []
    for summary in summaries:
        if block_family not in summary.allowed_block_families:
            continue
        regions.extend(summary.base_effective_windows)
    return merge_or_windows(tuple(regions))
