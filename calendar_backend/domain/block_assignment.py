"""Frozen DTOs for block assignment service results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from calendar_backend.domain.assignment import occupied_intervals_from_calendar_entries
from calendar_backend.domain.block_resolution import ResolvedBlock
from calendar_backend.domain.enums import SolverStatus
from calendar_backend.domain.errors import ServiceMessage
from calendar_backend.domain.ids import BlockCalendarEntryID, CalendarRunID, PlanID
from calendar_backend.models.blocks import BlockCalendarEntry
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.scheduling.input import OccupiedInterval
from calendar_backend.scheduling.types import TaskAssignment


@dataclass(frozen=True)
class BlockCalendarEntryInsertSpec:
    source_plan_id: PlanID
    start_time: datetime
    end_time: datetime
    display_label: str


@dataclass(frozen=True)
class BlockCalendarEntryDTO:
    block_calendar_entry_id: BlockCalendarEntryID
    start_time: datetime
    end_time: datetime
    source_plan_id: PlanID
    display_label: str
    calendar_run_id: CalendarRunID | None


@dataclass(frozen=True)
class BlockAssignmentResult:
    run_started_at: datetime
    optimization_status: SolverStatus
    block_calendar_entries: tuple[BlockCalendarEntryDTO, ...]
    warnings: tuple[ServiceMessage, ...]
    runtime_ms: int
    calendar_run_id: CalendarRunID | None


def block_calendar_entry_dto_from_row(entry: BlockCalendarEntry) -> BlockCalendarEntryDTO:
    return BlockCalendarEntryDTO(
        block_calendar_entry_id=BlockCalendarEntryID(entry.block_calendar_entry_id),
        start_time=entry.start_time,
        end_time=entry.end_time,
        source_plan_id=PlanID(entry.source_plan_id),
        display_label=entry.display_label,
        calendar_run_id=(
            CalendarRunID(entry.calendar_run_id) if entry.calendar_run_id is not None else None
        ),
    )


def block_calendar_entry_insert_specs_from_assignments(
    assignments: tuple[TaskAssignment, ...],
    resolved_blocks_by_id: dict[PlanID, ResolvedBlock],
) -> tuple[BlockCalendarEntryInsertSpec, ...]:
    specs: list[BlockCalendarEntryInsertSpec] = []
    for assignment in assignments:
        block = resolved_blocks_by_id[assignment.plan_id]
        for segment in assignment.segments:
            specs.append(
                BlockCalendarEntryInsertSpec(
                    source_plan_id=assignment.plan_id,
                    start_time=segment.start_time,
                    end_time=segment.end_time,
                    display_label=block.name,
                )
            )
    return tuple(
        sorted(
            specs,
            key=lambda spec: (spec.start_time, spec.end_time, str(spec.source_plan_id)),
        )
    )


def occupied_intervals_from_task_calendar_entries_for_blocks(
    entries: tuple[CalendarEntry, ...],
    run_started_at: datetime,
) -> tuple[OccupiedInterval, ...]:
    """Reuse TASK calendar rows as hard occupied intervals for block assignment."""
    return occupied_intervals_from_calendar_entries(entries, run_started_at)


def sqlite_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
