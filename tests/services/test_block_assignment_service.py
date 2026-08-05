"""Integration tests for BlockAssignmentService."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from calendar_backend.db.session import transaction
from calendar_backend.domain.block_resolution import ResolveBlocksResult, ResolvedBlock
from calendar_backend.domain.enums import CalendarRunStatus, PlanKind, SolverStatus
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import CalendarRunID, PlanID
from calendar_backend.domain.plan_create import BlockCreatePayload
from calendar_backend.models.blocks import BlockCalendarEntry
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.runs import CalendarRun
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.block import BlockService
from calendar_backend.services.block_assignment import BlockAssignmentService
from calendar_backend.services.block_resolution import (
    BlockResolutionService,
    _resolve_from_current_tree,  # pyright: ignore[reportPrivateUsage]
)
from calendar_backend.services.goal import GoalService
from calendar_backend.services.master_horizon import MasterHorizonService
from calendar_backend.services.master_plan import MasterPlanService
from calendar_backend.services.task_resolution import load_plan_graph
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .conftest import FakeClock

RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)


def _utc(y: int, m: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def _clock() -> FakeClock:
    return FakeClock(RUN_AT)


def _assignment_service(session: Session) -> BlockAssignmentService:
    return BlockAssignmentService(session, _clock())


def _goal_service(session: Session) -> GoalService:
    return GoalService(session, _clock())


def _bootstrap_master_with_horizon(session: Session) -> PlanID:
    clock = _clock()
    master = MasterPlanService(session, clock).ensure_master_exists()
    assert master.success and master.value is not None
    AppSettingsService(session, clock).get_settings()
    MasterHorizonService(session, clock).refresh_master_horizon(RUN_AT)
    return master.value.plan_id


def _create_block(session: Session, parent_id: PlanID, *, name: str = "block") -> PlanID:
    result = _goal_service(session).create_child(
        parent_id,
        PlanKind.BLOCK,
        BlockCreatePayload(name, 30, False, None, "focus"),
        is_critical=False,
    )
    assert result.success and result.value is not None
    return result.value.plan_id


def _block_calendar_entry_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(BlockCalendarEntry)) or 0


def _calendar_run_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(CalendarRun)) or 0


def _invalid_incomplete_block() -> tuple[ResolvedBlock, ...]:
    return (
        ResolvedBlock(
            plan_id=PlanID(uuid.uuid4()),
            name="bad",
            duration_minutes=0,
            divisible=False,
            minimum_chunk_size_minutes=None,
            block_family="focus",
            user_completed=False,
            completed_at=None,
            effective_time_windows=(),
            constraint_sources=(),
            priority_path=(0,),
            criticality_path=(),
            parent_path=(),
            validation_errors=(
                ServiceMessage(code=MessageCode.INVALID_DURATION, message="bad", details={}),
            ),
        ),
    )


def _empty_resolve_result(
    *,
    run_started_at: datetime = RUN_AT,
    invalid_incomplete: tuple[ResolvedBlock, ...] = (),
) -> ResolveBlocksResult:
    return ResolveBlocksResult(
        run_started_at=run_started_at,
        valid_incomplete=(),
        valid_completed=(),
        invalid_incomplete=invalid_incomplete,
        invalid_completed=(),
        precedence_constraints=(),
        warnings=(),
    )


def _resolve_seam(session: Session) -> ResolveBlocksResult:
    plans = load_plan_graph(session)
    return _resolve_from_current_tree(RUN_AT, plans=plans)


@pytest.mark.integration
def test_assign_blocks_invalid_incomplete_blocks_without_db_mutation(
    service_db_session: Session,
) -> None:
    _bootstrap_master_with_horizon(service_db_session)
    entries_before = _block_calendar_entry_count(service_db_session)
    runs_before = _calendar_run_count(service_db_session)

    result = _assignment_service(service_db_session).assign_blocks(
        _empty_resolve_result(invalid_incomplete=_invalid_incomplete_block()),
        RUN_AT,
    )

    assert not result.success
    assert result.errors[0].code == MessageCode.INVALID_INCOMPLETE_BLOCKS_BLOCK_ASSIGNMENT
    assert _block_calendar_entry_count(service_db_session) == entries_before
    assert _calendar_run_count(service_db_session) == runs_before


@pytest.mark.integration
def test_assign_blocks_run_started_at_mismatch_blocks_without_db_mutation(
    service_db_session: Session,
) -> None:
    _bootstrap_master_with_horizon(service_db_session)
    entries_before = _block_calendar_entry_count(service_db_session)

    result = _assignment_service(service_db_session).assign_blocks(
        _empty_resolve_result(run_started_at=_utc(2026, 6, 7, 11, 0)),
        RUN_AT,
    )

    assert not result.success
    assert result.errors[0].code == MessageCode.RUN_STARTED_AT_MISMATCH
    assert _block_calendar_entry_count(service_db_session) == entries_before


@pytest.mark.integration
def test_assign_blocks_success_persists_block_calendar_entry(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    _create_block(service_db_session, master_id, name="focus block")

    result = _assignment_service(service_db_session).assign_blocks(
        _resolve_seam(service_db_session),
        RUN_AT,
    )

    assert result.success and result.value is not None
    assert len(result.value.block_calendar_entries) == 1
    assert result.value.optimization_status in (SolverStatus.FEASIBLE, SolverStatus.OPTIMAL)
    assert _block_calendar_entry_count(service_db_session) == 1
    task_entries = service_db_session.scalars(select(CalendarEntry)).all()
    assert task_entries == []


@pytest.mark.integration
def test_assign_blocks_success_replaces_future_block_entries_only(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    block_id = _create_block(service_db_session, master_id)
    stale_id = uuid.uuid4()
    with transaction(service_db_session) as txn:
        txn.add(
            BlockCalendarEntry(
                block_calendar_entry_id=stale_id,
                start_time=_utc(2026, 6, 7, 11, 0),
                end_time=_utc(2026, 6, 7, 11, 30),
                source_plan_id=block_id,
                calendar_run_id=None,
                display_label="stale",
                created_at=RUN_AT,
                updated_at=RUN_AT,
            )
        )
        txn.flush()

    result = _assignment_service(service_db_session).assign_blocks(
        _resolve_seam(service_db_session),
        RUN_AT,
    )

    assert result.success
    assert service_db_session.get(BlockCalendarEntry, stale_id) is None
    assert _block_calendar_entry_count(service_db_session) == 1


@pytest.mark.integration
def test_assign_blocks_success_preserves_past_block_entries(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    block_id = _create_block(service_db_session, master_id)
    past_id = uuid.uuid4()
    with transaction(service_db_session) as txn:
        txn.add(
            BlockCalendarEntry(
                block_calendar_entry_id=past_id,
                start_time=_utc(2026, 6, 7, 9, 0),
                end_time=_utc(2026, 6, 7, 9, 30),
                source_plan_id=block_id,
                calendar_run_id=None,
                display_label="past",
                created_at=RUN_AT,
                updated_at=RUN_AT,
            )
        )
        txn.flush()

    result = _assignment_service(service_db_session).assign_blocks(
        _resolve_seam(service_db_session),
        RUN_AT,
    )

    assert result.success
    assert service_db_session.get(BlockCalendarEntry, past_id) is not None
    assert _block_calendar_entry_count(service_db_session) == 2


@pytest.mark.integration
def test_resolve_and_assign_with_immediate_precedence(service_db_session: Session) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    first_id = _create_block(service_db_session, master_id, name="first")
    second_id = _create_block(service_db_session, master_id, name="second")

    link = BlockService(service_db_session, _clock()).set_immediate_prerequisite(
        second_id,
        first_id,
    )
    assert link.success

    resolved = BlockResolutionService(service_db_session, _clock()).resolve_blocks(RUN_AT)
    assert resolved.success and resolved.value is not None
    assert len(resolved.value.precedence_constraints) == 1

    assigned = _assignment_service(service_db_session).assign_blocks(resolved.value, RUN_AT)
    assert assigned.success and assigned.value is not None
    entries = assigned.value.block_calendar_entries
    assert len(entries) == 2
    starts = sorted(entry.start_time for entry in entries)
    assert starts[0] < starts[1]


@pytest.mark.integration
def test_assign_blocks_with_explicit_calendar_run_id_reuses_run(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    _create_block(service_db_session, master_id)
    existing_run_id = uuid.uuid4()
    with transaction(service_db_session) as txn:
        txn.add(
            CalendarRun(
                calendar_run_id=existing_run_id,
                run_started_at=RUN_AT,
                run_finished_at=RUN_AT,
                status=CalendarRunStatus.SUCCESS,
                solver_status=SolverStatus.FEASIBLE,
                conflict_count=0,
                warning_count=0,
                runtime_ms=1,
                created_at=RUN_AT,
            )
        )
        txn.flush()

    result = _assignment_service(service_db_session).assign_blocks(
        _resolve_seam(service_db_session),
        RUN_AT,
        calendar_run_id=CalendarRunID(existing_run_id),
    )

    assert result.success and result.value is not None
    assert result.value.calendar_run_id == CalendarRunID(existing_run_id)
    assert _calendar_run_count(service_db_session) == 1
    entry = service_db_session.scalars(select(BlockCalendarEntry)).one()
    assert entry.calendar_run_id == existing_run_id
