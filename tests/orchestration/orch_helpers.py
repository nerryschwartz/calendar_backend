"""Shared helpers for orchestration integration tests."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from calendar_backend.db.session import transaction
from calendar_backend.domain.enums import (
    CalendarEntryType,
    CloneStatus,
    PlanKind,
    RepeatMode,
)
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.orchestration import RefreshScheduleResult
from calendar_backend.domain.plan_create import (
    GoalCreatePayload,
    RepetitionCreatePayload,
    TaskCreatePayload,
)
from calendar_backend.domain.resolution import ResolvedTask
from calendar_backend.domain.time import TimeWindow
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.chains import GoalChildChain, GoalChildChainItem
from calendar_backend.models.plans import Plan, RepetitionPlan, TaskPlan
from calendar_backend.models.repetitions import RepetitionInstance
from calendar_backend.models.runs import ActiveCalendarState, CalendarRun
from calendar_backend.orchestration.refresh_schedule import OrchestrationService
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.free_time_activity import FreeTimeActivityService
from calendar_backend.services.goal import GoalService
from calendar_backend.services.master_horizon import MasterHorizonService
from calendar_backend.services.master_plan import MasterPlanService
from calendar_backend.services.repetition import RepetitionService
from calendar_backend.services.task import TaskService
from calendar_backend.services.time_constraint import TimeConstraintService
from sqlalchemy import func, select
from sqlalchemy.orm import Session

RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)


@dataclass(frozen=True)
class FakeClock:
    fixed: datetime

    def now_utc(self) -> datetime:
        return self.fixed


def utc(y: int, m: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def window(start: datetime, end: datetime) -> TimeWindow:
    return TimeWindow(start_time=start, end_time=end)


def clock() -> FakeClock:
    return FakeClock(RUN_AT)


def orchestration_service(session: Session) -> OrchestrationService:
    return OrchestrationService(session, clock())


def goal_service(session: Session) -> GoalService:
    return GoalService(session, clock())


def task_service(session: Session) -> TaskService:
    return TaskService(session, clock())


def repetition_service(session: Session) -> RepetitionService:
    return RepetitionService(session, clock())


def bootstrap_master_with_horizon(session: Session) -> PlanID:
    test_clock = clock()
    master = MasterPlanService(session, test_clock).ensure_master_exists()
    assert master.success and master.value is not None
    AppSettingsService(session, test_clock).get_settings()
    assert MasterHorizonService(session, test_clock).refresh_master_horizon(RUN_AT).success
    return master.value.plan_id


def create_task(session: Session, parent_id: PlanID, *, name: str = "task") -> PlanID:
    result = goal_service(session).create_child(
        parent_id,
        PlanKind.TASK,
        TaskCreatePayload(name, 30, False, None),
        is_critical=False,
    )
    assert result.success and result.value is not None
    return result.value.plan_id


def create_enabled_activity(session: Session, *, name: str = "reading") -> uuid.UUID:
    result = FreeTimeActivityService(session, clock()).create_activity(
        name,
        Decimal("1"),
        minimum_block_size_minutes=0,
    )
    assert result.success and result.value is not None
    return result.value.free_time_activity_id


def active_state(session: Session) -> ActiveCalendarState | None:
    return session.get(ActiveCalendarState, 1)


def calendar_entry_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(CalendarEntry)) or 0


def calendar_run_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(CalendarRun)) or 0


def future_task_entry_count(session: Session, task_id: PlanID) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(CalendarEntry)
            .where(
                CalendarEntry.entry_type == CalendarEntryType.TASK,
                CalendarEntry.source_plan_id == task_id,
                CalendarEntry.start_time >= RUN_AT,
            )
        )
        or 0
    )


def future_free_time_entry_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(CalendarEntry)
            .where(
                CalendarEntry.entry_type == CalendarEntryType.FREE_TIME,
                CalendarEntry.start_time >= RUN_AT,
            )
        )
        or 0
    )


def add_calendar_entry(
    session: Session,
    *,
    entry_type: CalendarEntryType,
    start_time: datetime,
    end_time: datetime,
    source_plan_id: PlanID | None = None,
    source_free_time_activity_id: uuid.UUID | None = None,
    calendar_run_id: uuid.UUID | None = None,
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    with transaction(session) as txn:
        txn.add(
            CalendarEntry(
                calendar_entry_id=entry_id,
                entry_type=entry_type,
                start_time=start_time,
                end_time=end_time,
                source_plan_id=source_plan_id,
                source_free_time_activity_id=source_free_time_activity_id,
                calendar_run_id=calendar_run_id,
                display_label="seed",
                created_at=RUN_AT,
                updated_at=RUN_AT,
            )
        )
        txn.flush()
    return entry_id


def bootstrap_assignable_task(session: Session) -> tuple[PlanID, PlanID]:
    master_id = bootstrap_master_with_horizon(session)
    task_id = create_task(session, master_id)
    TimeConstraintService(session, clock()).add_user_group(
        master_id,
        (window(RUN_AT, RUN_AT + timedelta(hours=2)),),
    )
    create_enabled_activity(session)
    return master_id, task_id


def invalid_incomplete_task() -> tuple[ResolvedTask, ...]:
    plan_id = uuid.uuid4()
    return (
        ResolvedTask(
            plan_id=PlanID(plan_id),
            name="bad",
            duration_minutes=0,
            divisible=False,
            minimum_chunk_size_minutes=None,
            user_completed=False,
            completed_at=None,
            effective_time_windows=(),
            constraint_sources=(),
            priority_path=(0,),
            criticality_path=(),
            parent_path=(PlanID(plan_id),),
            chain_path=(),
            validation_errors=(
                ServiceMessage(
                    code=MessageCode.INVALID_DURATION,
                    message="invalid duration",
                    details={},
                ),
            ),
        ),
    )


def repetition_payload(*, manual_count: int = 1) -> RepetitionCreatePayload:
    return RepetitionCreatePayload(
        name="weekly",
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=RUN_AT,
        repeat_interval_minutes=60,
        manual_count=manual_count,
        end_time=None,
        default_instance_critical=False,
        template_type=PlanKind.GOAL,
        template_payload=GoalCreatePayload(name="template"),
    )


def create_repetition(session: Session, master_id: PlanID, *, manual_count: int = 1) -> PlanID:
    result = goal_service(session).create_child(
        master_id,
        PlanKind.REPETITION,
        repetition_payload(manual_count=manual_count),
        is_critical=False,
    )
    assert result.success and result.value is not None
    return result.value.plan_id


def setup_goal_repetition_with_task_child(
    session: Session,
    master_id: PlanID,
    *,
    manual_count: int = 1,
) -> tuple[PlanID, PlanID, PlanID]:
    repetition_id = create_repetition(session, master_id, manual_count=manual_count)
    repetition = session.get(RepetitionPlan, repetition_id)
    assert repetition is not None
    template_goal_id = PlanID(repetition.template_root_id)
    child_result = goal_service(session).create_child(
        template_goal_id,
        PlanKind.TASK,
        TaskCreatePayload("template task", 30, False, None),
        is_critical=False,
    )
    assert child_result.success and child_result.value is not None
    return repetition_id, template_goal_id, child_result.value.plan_id


def generate_instances(session: Session, repetition_id: PlanID) -> None:
    assert repetition_service(session).generate_instances(repetition_id, RUN_AT).success


def instance_root_clone_id(session: Session, repetition_id: PlanID, instance_index: int) -> PlanID:
    instance = session.scalar(
        select(RepetitionInstance)
        .where(RepetitionInstance.repetition_plan_id == repetition_id)
        .where(RepetitionInstance.instance_index == instance_index)
    )
    assert instance is not None
    return PlanID(instance.root_clone_id)


def clone_for_template(
    session: Session,
    *,
    parent_clone_id: PlanID,
    template_plan_id: PlanID,
) -> PlanID:
    clone = session.scalar(
        select(Plan).where(
            Plan.parent_id == parent_clone_id,
            Plan.cloned_from_id == template_plan_id,
        )
    )
    assert clone is not None
    return PlanID(clone.plan_id)


def all_resolved_tasks(result_value: RefreshScheduleResult) -> tuple[ResolvedTask, ...]:
    resolved = result_value.resolved
    assert resolved is not None
    return (
        *resolved.valid_incomplete,
        *resolved.valid_completed,
        *resolved.invalid_incomplete,
        *resolved.invalid_completed,
    )


def calendar_source_plan_ids(session: Session) -> set[PlanID]:
    rows = session.scalars(
        select(CalendarEntry.source_plan_id).where(
            CalendarEntry.entry_type == CalendarEntryType.TASK,
            CalendarEntry.start_time >= RUN_AT,
        )
    ).all()
    return {PlanID(row) for row in rows if row is not None}


def set_instance_critical_flags(
    session: Session,
    repetition_id: PlanID,
    *,
    critical_by_index: dict[int, bool],
) -> None:
    with transaction(session) as txn:
        instances = list(
            txn.scalars(
                select(RepetitionInstance)
                .where(RepetitionInstance.repetition_plan_id == repetition_id)
                .order_by(RepetitionInstance.instance_index)
            ).all()
        )
        for instance in instances:
            if instance.instance_index in critical_by_index:
                instance.is_critical = critical_by_index[instance.instance_index]
        next_sort_order = {False: 0, True: 0}
        for instance in instances:
            instance.sort_order = next_sort_order[instance.is_critical]
            next_sort_order[instance.is_critical] += 1


def assert_linked_clone_child_exists(
    session: Session,
    *,
    root_clone_id: PlanID,
    template_child_id: PlanID,
) -> PlanID:
    clone_child = session.scalar(
        select(Plan).where(
            Plan.parent_id == root_clone_id,
            Plan.cloned_from_id == template_child_id,
        )
    )
    assert clone_child is not None
    assert clone_child.clone_status == CloneStatus.LINKED
    assert session.get(TaskPlan, clone_child.plan_id) is not None
    chain_item = session.scalar(
        select(GoalChildChainItem).where(GoalChildChainItem.child_plan_id == clone_child.plan_id)
    )
    assert chain_item is not None
    chain = session.get(GoalChildChain, chain_item.chain_id)
    assert chain is not None
    assert chain.parent_goal_id == root_clone_id
    return PlanID(clone_child.plan_id)
