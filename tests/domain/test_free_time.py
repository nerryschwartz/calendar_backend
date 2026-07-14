"""Pure tests for free-time domain helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from calendar_backend.domain.assignment import future_task_blocker_intervals_from_calendar_entries
from calendar_backend.domain.enums import (
    CalendarEntryType,
    CloneStatus,
    FreeTimeWeekStartDay,
    PlanKind,
    RepeatMode,
)
from calendar_backend.domain.errors import MessageCode
from calendar_backend.domain.free_time import (
    FreeTimeActivityDTO,
    FreeTimeGap,
    assign_free_time_to_gaps,
    blocked_activity_ids,
    compute_effective_fractions,
    discover_free_time_gaps,
    free_time_plan_graph_from_plans,
    is_plan_logically_complete,
    validate_activity_fields,
    validate_enabled_fractions_sum_to_one,
)
from calendar_backend.domain.ids import FreeTimeActivityID, PlanID
from calendar_backend.domain.time import TimeWindow
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.chains import GoalChildChain, GoalChildChainItem
from calendar_backend.models.free_time import FreeTimeActivity
from calendar_backend.models.plans import GoalPlan, Plan, RepetitionPlan, TaskPlan
from calendar_backend.models.repetitions import RepetitionInstance

_NOW = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
_RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)


def _utc(y: int, m: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def _window(start: datetime, end: datetime) -> TimeWindow:
    return TimeWindow(start_time=start, end_time=end)


def _plan(
    plan_id: uuid.UUID,
    *,
    plan_kind: PlanKind,
    is_master: bool = False,
    parent_id: uuid.UUID | None = None,
    clone_status: CloneStatus = CloneStatus.NOT_CLONED,
) -> Plan:
    return Plan(
        plan_id=plan_id,
        plan_kind=plan_kind,
        name="plan",
        parent_id=parent_id,
        is_master=is_master,
        cloned_from_id=None,
        clone_status=clone_status,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _attach_goal(plan: Plan) -> None:
    plan.goal_plan = GoalPlan(plan_id=plan.plan_id)


def _attach_task(plan: Plan, *, user_completed: bool = False) -> None:
    plan.task_plan = TaskPlan(
        plan_id=plan.plan_id,
        duration_minutes=30,
        divisible=False,
        minimum_chunk_size_minutes=None,
        user_completed=user_completed,
        completed_at=_NOW if user_completed else None,
    )


def _attach_critical_chain_item(
    goal: Plan,
    *,
    child_plan_id: uuid.UUID,
    position: int,
) -> None:
    chain_id = uuid.uuid4()
    chain = GoalChildChain(
        goal_child_chain_id=chain_id,
        parent_goal_id=goal.plan_id,
        is_critical=True,
        sort_order=0,
        created_at=_NOW,
        updated_at=_NOW,
    )
    chain.items = [
        GoalChildChainItem(
            goal_child_chain_item_id=uuid.uuid4(),
            chain_id=chain_id,
            child_plan_id=child_plan_id,
            position=position,
        )
    ]
    assert goal.goal_plan is not None
    goal.goal_plan.chains = [chain]


def _attach_noncritical_chain_item(goal: Plan, *, child_plan_id: uuid.UUID) -> None:
    chain_id = uuid.uuid4()
    chain = GoalChildChain(
        goal_child_chain_id=chain_id,
        parent_goal_id=goal.plan_id,
        is_critical=False,
        sort_order=0,
        created_at=_NOW,
        updated_at=_NOW,
    )
    chain.items = [
        GoalChildChainItem(
            goal_child_chain_item_id=uuid.uuid4(),
            chain_id=chain_id,
            child_plan_id=child_plan_id,
            position=0,
        )
    ]
    assert goal.goal_plan is not None
    goal.goal_plan.chains = [chain]


def _activity_dto(
    activity_id: uuid.UUID,
    *,
    real_fraction: Decimal = Decimal("1"),
    enabled: bool = True,
    minimum_block_size_minutes: int = 0,
    prerequisite_plan_ids: tuple[uuid.UUID, ...] = (),
) -> FreeTimeActivityDTO:
    return FreeTimeActivityDTO(
        free_time_activity_id=FreeTimeActivityID(activity_id),
        name="reading",
        enabled=enabled,
        real_fraction=real_fraction,
        minimum_block_size_minutes=minimum_block_size_minutes,
        prerequisite_plan_ids=tuple(PlanID(plan_id) for plan_id in prerequisite_plan_ids),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _activity_row(
    activity_id: uuid.UUID,
    *,
    real_fraction: Decimal,
    enabled: bool = True,
) -> FreeTimeActivity:
    return FreeTimeActivity(
        free_time_activity_id=activity_id,
        name="reading",
        enabled=enabled,
        real_fraction=real_fraction,
        minimum_block_size_minutes=0,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _calendar_entry(
    *,
    entry_type: CalendarEntryType,
    start_time: datetime,
    end_time: datetime,
) -> CalendarEntry:
    return CalendarEntry(
        calendar_entry_id=uuid.uuid4(),
        entry_type=entry_type,
        start_time=start_time,
        end_time=end_time,
        source_plan_id=None,
        source_free_time_activity_id=None,
        calendar_run_id=None,
        display_label="seed",
        created_at=_NOW,
        updated_at=_NOW,
    )


def test_validate_activity_fields_rejects_empty_name() -> None:
    error = validate_activity_fields(
        name="   ",
        real_fraction=Decimal("1"),
        minimum_block_size_minutes=0,
        enabled=True,
    )

    assert error is not None
    assert error.code == MessageCode.INVALID_CREATE_PAYLOAD


def test_validate_activity_fields_rejects_negative_minimum_block_size() -> None:
    error = validate_activity_fields(
        name="reading",
        real_fraction=Decimal("1"),
        minimum_block_size_minutes=-1,
        enabled=True,
    )

    assert error is not None
    assert error.code == MessageCode.INVALID_MINIMUM_BLOCK_SIZE


def test_validate_activity_fields_rejects_enabled_zero_fraction() -> None:
    error = validate_activity_fields(
        name="reading",
        real_fraction=Decimal("0"),
        minimum_block_size_minutes=0,
        enabled=True,
    )

    assert error is not None
    assert error.code == MessageCode.INVALID_FREE_TIME_FRACTIONS


def test_validate_enabled_fractions_sum_to_one_accepts_valid_sum() -> None:
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    error = validate_enabled_fractions_sum_to_one(
        (
            _activity_row(first_id, real_fraction=Decimal("0.6")),
            _activity_row(second_id, real_fraction=Decimal("0.4")),
        )
    )

    assert error is None


def test_validate_enabled_fractions_sum_to_one_rejects_invalid_sum() -> None:
    error = validate_enabled_fractions_sum_to_one(
        (_activity_row(uuid.uuid4(), real_fraction=Decimal("0.6")),)
    )

    assert error is not None
    assert error.code == MessageCode.INVALID_FREE_TIME_FRACTIONS


def test_validate_enabled_fractions_sum_to_one_ignores_disabled_activities() -> None:
    error = validate_enabled_fractions_sum_to_one(
        (
            _activity_row(uuid.uuid4(), real_fraction=Decimal("1"), enabled=True),
            _activity_row(uuid.uuid4(), real_fraction=Decimal("0.5"), enabled=False),
        )
    )

    assert error is None


def test_validate_enabled_fractions_sum_to_one_allows_no_enabled_positive_activities() -> None:
    error = validate_enabled_fractions_sum_to_one(
        (
            _activity_row(uuid.uuid4(), real_fraction=Decimal("1"), enabled=False),
            _activity_row(uuid.uuid4(), real_fraction=Decimal("1"), enabled=False),
        )
    )

    assert error is None


def test_is_plan_logically_complete_task_requires_user_completed() -> None:
    task_id = uuid.uuid4()
    complete_task = _plan(task_id, plan_kind=PlanKind.TASK)
    _attach_task(complete_task, user_completed=True)
    graph = free_time_plan_graph_from_plans((complete_task,))

    assert is_plan_logically_complete(PlanID(task_id), graph)

    incomplete_task = _plan(uuid.uuid4(), plan_kind=PlanKind.TASK)
    _attach_task(incomplete_task, user_completed=False)
    incomplete_graph = free_time_plan_graph_from_plans((incomplete_task,))

    assert not is_plan_logically_complete(PlanID(incomplete_task.plan_id), incomplete_graph)


def test_is_plan_logically_complete_goal_requires_critical_chain_children() -> None:
    master_id = uuid.uuid4()
    goal_id = uuid.uuid4()
    task_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    goal = _plan(goal_id, plan_kind=PlanKind.GOAL, parent_id=master_id)
    _attach_goal(goal)
    task = _plan(task_id, plan_kind=PlanKind.TASK, parent_id=goal_id)
    _attach_task(task, user_completed=False)
    _attach_critical_chain_item(master, child_plan_id=goal_id, position=0)
    _attach_critical_chain_item(goal, child_plan_id=task_id, position=0)

    graph = free_time_plan_graph_from_plans((master, goal, task))
    assert not is_plan_logically_complete(PlanID(master_id), graph)

    completed_task = _plan(task_id, plan_kind=PlanKind.TASK, parent_id=goal_id)
    _attach_task(completed_task, user_completed=True)
    completed_graph = free_time_plan_graph_from_plans((master, goal, completed_task))
    assert is_plan_logically_complete(PlanID(master_id), completed_graph)


def test_is_plan_logically_complete_goal_ignores_noncritical_chain() -> None:
    master_id = uuid.uuid4()
    task_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    task = _plan(task_id, plan_kind=PlanKind.TASK, parent_id=master_id)
    _attach_task(task, user_completed=False)
    _attach_noncritical_chain_item(master, child_plan_id=task_id)

    graph = free_time_plan_graph_from_plans((master, task))
    assert is_plan_logically_complete(PlanID(master_id), graph)


def test_is_plan_logically_complete_repetition_requires_critical_instance_subtree() -> None:
    master_id = uuid.uuid4()
    repetition_id = uuid.uuid4()
    template_id = uuid.uuid4()
    clone_id = uuid.uuid4()
    clone_task_id = uuid.uuid4()

    master = _plan(master_id, plan_kind=PlanKind.GOAL, is_master=True)
    _attach_goal(master)
    repetition = _plan(repetition_id, plan_kind=PlanKind.REPETITION, parent_id=master_id)
    repetition_plan = RepetitionPlan(
        plan_id=repetition_id,
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=_RUN_AT,
        repeat_interval_minutes=60,
        manual_count=1,
        end_time=None,
        template_root_id=template_id,
        default_instance_critical=False,
        generated_at=_RUN_AT,
    )
    repetition.repetition_plan = repetition_plan

    template = _plan(
        template_id,
        plan_kind=PlanKind.GOAL,
        parent_id=repetition_id,
        clone_status=CloneStatus.TEMPLATE,
    )
    _attach_goal(template)
    clone = _plan(
        clone_id,
        plan_kind=PlanKind.GOAL,
        parent_id=repetition_id,
        clone_status=CloneStatus.LINKED,
    )
    _attach_goal(clone)
    clone_task = _plan(clone_task_id, plan_kind=PlanKind.TASK, parent_id=clone_id)
    _attach_task(clone_task, user_completed=False)
    _attach_critical_chain_item(clone, child_plan_id=clone_task_id, position=0)

    repetition_plan.instances = [
        RepetitionInstance(
            repetition_instance_id=uuid.uuid4(),
            repetition_plan_id=repetition_id,
            instance_index=0,
            root_clone_id=clone_id,
            instance_start_time=_RUN_AT,
            is_critical=True,
            sort_order=0,
        )
    ]

    graph = free_time_plan_graph_from_plans((master, repetition, template, clone, clone_task))
    assert not is_plan_logically_complete(PlanID(repetition_id), graph)

    completed_clone_task = _plan(clone_task_id, plan_kind=PlanKind.TASK, parent_id=clone_id)
    _attach_task(completed_clone_task, user_completed=True)
    completed_graph = free_time_plan_graph_from_plans(
        (master, repetition, template, clone, completed_clone_task)
    )
    assert is_plan_logically_complete(PlanID(repetition_id), completed_graph)


def test_is_plan_logically_complete_template_subtree_is_incomplete() -> None:
    repetition_id = uuid.uuid4()
    template_id = uuid.uuid4()

    repetition = _plan(repetition_id, plan_kind=PlanKind.REPETITION)
    repetition_plan = RepetitionPlan(
        plan_id=repetition_id,
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=_RUN_AT,
        repeat_interval_minutes=60,
        manual_count=1,
        end_time=None,
        template_root_id=template_id,
        default_instance_critical=False,
        generated_at=_RUN_AT,
    )
    repetition.repetition_plan = repetition_plan

    template = _plan(
        template_id,
        plan_kind=PlanKind.GOAL,
        parent_id=repetition_id,
        clone_status=CloneStatus.TEMPLATE,
    )
    _attach_goal(template)
    template_task_id = uuid.uuid4()
    template_task = _plan(
        template_task_id,
        plan_kind=PlanKind.TASK,
        parent_id=template_id,
        clone_status=CloneStatus.TEMPLATE,
    )
    _attach_task(template_task, user_completed=True)

    graph = free_time_plan_graph_from_plans((repetition, template, template_task))

    assert not is_plan_logically_complete(PlanID(template_id), graph)
    assert not is_plan_logically_complete(PlanID(template_task_id), graph)


def test_blocked_activity_ids_blocks_template_subtree_prerequisite() -> None:
    repetition_id = uuid.uuid4()
    template_id = uuid.uuid4()
    template_task_id = uuid.uuid4()
    activity_id = uuid.uuid4()

    repetition = _plan(repetition_id, plan_kind=PlanKind.REPETITION)
    repetition_plan = RepetitionPlan(
        plan_id=repetition_id,
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=_RUN_AT,
        repeat_interval_minutes=60,
        manual_count=1,
        end_time=None,
        template_root_id=template_id,
        default_instance_critical=False,
        generated_at=_RUN_AT,
    )
    repetition.repetition_plan = repetition_plan

    template = _plan(
        template_id,
        plan_kind=PlanKind.GOAL,
        parent_id=repetition_id,
        clone_status=CloneStatus.TEMPLATE,
    )
    _attach_goal(template)
    template_task = _plan(
        template_task_id,
        plan_kind=PlanKind.TASK,
        parent_id=template_id,
        clone_status=CloneStatus.TEMPLATE,
    )
    _attach_task(template_task, user_completed=True)

    graph = free_time_plan_graph_from_plans((repetition, template, template_task))
    activities = (_activity_dto(activity_id, prerequisite_plan_ids=(template_task_id,)),)

    assert blocked_activity_ids(activities, graph) == frozenset({FreeTimeActivityID(activity_id)})


def test_compute_effective_fractions_renormalizes_when_template_prerequisite_blocks_partner() -> (
    None
):
    repetition_id = uuid.uuid4()
    template_id = uuid.uuid4()
    template_task_id = uuid.uuid4()
    blocked_id = uuid.uuid4()
    survivor_id = uuid.uuid4()

    repetition = _plan(repetition_id, plan_kind=PlanKind.REPETITION)
    repetition_plan = RepetitionPlan(
        plan_id=repetition_id,
        repeat_mode=RepeatMode.MANUAL_COUNT,
        start_time=_RUN_AT,
        repeat_interval_minutes=60,
        manual_count=1,
        end_time=None,
        template_root_id=template_id,
        default_instance_critical=False,
        generated_at=_RUN_AT,
    )
    repetition.repetition_plan = repetition_plan

    template = _plan(
        template_id,
        plan_kind=PlanKind.GOAL,
        parent_id=repetition_id,
        clone_status=CloneStatus.TEMPLATE,
    )
    _attach_goal(template)
    template_task = _plan(
        template_task_id,
        plan_kind=PlanKind.TASK,
        parent_id=template_id,
        clone_status=CloneStatus.TEMPLATE,
    )
    _attach_task(template_task, user_completed=True)

    graph = free_time_plan_graph_from_plans((repetition, template, template_task))
    activities = (
        _activity_dto(
            blocked_id,
            real_fraction=Decimal("0.5"),
            prerequisite_plan_ids=(template_task_id,),
        ),
        _activity_dto(survivor_id, real_fraction=Decimal("0.5")),
    )
    blocked = blocked_activity_ids(activities, graph)

    effective = compute_effective_fractions(activities, blocked)

    assert effective == ((FreeTimeActivityID(survivor_id), Decimal("1")),)


def test_blocked_activity_ids_blocks_incomplete_prerequisite() -> None:
    task_id = uuid.uuid4()
    activity_id = uuid.uuid4()
    task = _plan(task_id, plan_kind=PlanKind.TASK)
    _attach_task(task, user_completed=False)
    graph = free_time_plan_graph_from_plans((task,))
    activities = (_activity_dto(activity_id, prerequisite_plan_ids=(task_id,)),)

    blocked = blocked_activity_ids(activities, graph)

    assert blocked == frozenset({FreeTimeActivityID(activity_id)})


def test_blocked_activity_ids_unblocks_when_prerequisite_complete() -> None:
    task_id = uuid.uuid4()
    activity_id = uuid.uuid4()
    task = _plan(task_id, plan_kind=PlanKind.TASK)
    _attach_task(task, user_completed=True)
    graph = free_time_plan_graph_from_plans((task,))
    activities = (_activity_dto(activity_id, prerequisite_plan_ids=(task_id,)),)

    assert blocked_activity_ids(activities, graph) == frozenset()


def test_compute_effective_fractions_renormalizes_after_blocking() -> None:
    blocked_id = uuid.uuid4()
    survivor_id = uuid.uuid4()
    activities = (
        _activity_dto(blocked_id, real_fraction=Decimal("0.5")),
        _activity_dto(survivor_id, real_fraction=Decimal("0.5")),
    )

    effective = compute_effective_fractions(activities, frozenset({FreeTimeActivityID(blocked_id)}))

    assert effective == ((FreeTimeActivityID(survivor_id), Decimal("1")),)


def test_compute_effective_fractions_excludes_disabled_activities() -> None:
    enabled_id = uuid.uuid4()
    disabled_id = uuid.uuid4()
    activities = (
        _activity_dto(enabled_id, real_fraction=Decimal("0.4")),
        _activity_dto(disabled_id, real_fraction=Decimal("0.6"), enabled=False),
    )

    effective = compute_effective_fractions(activities, frozenset())

    assert effective == ((FreeTimeActivityID(enabled_id), Decimal("1")),)


def test_compute_effective_fractions_returns_empty_when_all_blocked() -> None:
    activity_id = uuid.uuid4()
    activities = (_activity_dto(activity_id, real_fraction=Decimal("1")),)

    assert (
        compute_effective_fractions(activities, frozenset({FreeTimeActivityID(activity_id)})) == ()
    )


def test_discover_free_time_gaps_without_blockers() -> None:
    run_started_at = _utc(2026, 6, 7, 10, 0)
    master_horizon_end = _utc(2026, 6, 7, 14, 0)

    gaps = discover_free_time_gaps(
        run_started_at=run_started_at,
        master_horizon_end=master_horizon_end,
        task_blockers=(),
        week_start_day=FreeTimeWeekStartDay.MONDAY,
        local_timezone="UTC",
    )

    assert len(gaps) == 1
    assert gaps[0].start_time == run_started_at
    assert gaps[0].end_time == master_horizon_end


def test_discover_free_time_gaps_splits_around_task_blocker() -> None:
    run_started_at = _utc(2026, 6, 7, 10, 0)
    master_horizon_end = _utc(2026, 6, 7, 14, 0)
    blocker = _window(_utc(2026, 6, 7, 11, 0), _utc(2026, 6, 7, 12, 0))

    gaps = discover_free_time_gaps(
        run_started_at=run_started_at,
        master_horizon_end=master_horizon_end,
        task_blockers=(blocker,),
        week_start_day=FreeTimeWeekStartDay.MONDAY,
        local_timezone="UTC",
    )

    assert [(gap.start_time, gap.end_time) for gap in gaps] == [
        (_utc(2026, 6, 7, 10, 0), _utc(2026, 6, 7, 11, 0)),
        (_utc(2026, 6, 7, 12, 0), _utc(2026, 6, 7, 14, 0)),
    ]


def test_discover_free_time_gaps_splits_at_week_boundary() -> None:
    run_started_at = _utc(2026, 6, 6, 22, 0)
    master_horizon_end = _utc(2026, 6, 8, 2, 0)

    gaps = discover_free_time_gaps(
        run_started_at=run_started_at,
        master_horizon_end=master_horizon_end,
        task_blockers=(),
        week_start_day=FreeTimeWeekStartDay.MONDAY,
        local_timezone="UTC",
    )

    assert len(gaps) == 2
    assert gaps[0].start_time == run_started_at
    assert gaps[0].end_time == _utc(2026, 6, 8, 0, 0)
    assert gaps[1].start_time == _utc(2026, 6, 8, 0, 0)
    assert gaps[1].end_time == master_horizon_end


def test_discover_free_time_gaps_truncates_at_master_horizon_end() -> None:
    run_started_at = _utc(2026, 6, 7, 10, 0)
    master_horizon_end = _utc(2026, 6, 7, 11, 30)

    gaps = discover_free_time_gaps(
        run_started_at=run_started_at,
        master_horizon_end=master_horizon_end,
        task_blockers=(),
        week_start_day=FreeTimeWeekStartDay.MONDAY,
        local_timezone="UTC",
    )

    assert len(gaps) == 1
    assert gaps[0].end_time == master_horizon_end


def test_future_task_blocker_intervals_include_future_task_only() -> None:
    past_task = _calendar_entry(
        entry_type=CalendarEntryType.TASK,
        start_time=_utc(2026, 6, 7, 8, 0),
        end_time=_utc(2026, 6, 7, 9, 0),
    )
    future_task = _calendar_entry(
        entry_type=CalendarEntryType.TASK,
        start_time=_utc(2026, 6, 7, 11, 0),
        end_time=_utc(2026, 6, 7, 12, 0),
    )
    free_time = _calendar_entry(
        entry_type=CalendarEntryType.FREE_TIME,
        start_time=_utc(2026, 6, 7, 13, 0),
        end_time=_utc(2026, 6, 7, 14, 0),
    )

    blockers = future_task_blocker_intervals_from_calendar_entries(
        (past_task, future_task, free_time),
        _RUN_AT,
    )

    assert blockers == (_window(_utc(2026, 6, 7, 11, 0), _utc(2026, 6, 7, 12, 0)),)


def test_assign_free_time_to_gaps_allocates_proportionally_within_week() -> None:
    reading_id = uuid.uuid4()
    gaming_id = uuid.uuid4()
    week_start = _utc(2026, 6, 2, 0, 0)
    gaps = (
        FreeTimeGap(
            start_time=_utc(2026, 6, 7, 10, 0),
            end_time=_utc(2026, 6, 7, 12, 0),
            week_start=week_start,
        ),
    )
    activities_by_id = {
        FreeTimeActivityID(reading_id): _activity_dto(
            reading_id,
            real_fraction=Decimal("0.5"),
            minimum_block_size_minutes=0,
        ),
        FreeTimeActivityID(gaming_id): _activity_dto(
            gaming_id,
            real_fraction=Decimal("0.5"),
            minimum_block_size_minutes=0,
        ),
    }
    effective = (
        (FreeTimeActivityID(reading_id), Decimal("0.5")),
        (FreeTimeActivityID(gaming_id), Decimal("0.5")),
    )

    specs = assign_free_time_to_gaps(
        gaps=gaps,
        effective_fractions=effective,
        activities_by_id=activities_by_id,
    )

    assert len(specs) == 2
    assert sum((spec.end_time - spec.start_time for spec in specs), timedelta()) == timedelta(
        hours=2
    )
    assert {spec.source_free_time_activity_id for spec in specs} == {
        FreeTimeActivityID(reading_id),
        FreeTimeActivityID(gaming_id),
    }


def test_assign_free_time_to_gaps_leaves_sub_minimum_remainder_empty() -> None:
    activity_id = uuid.uuid4()
    week_start = _utc(2026, 6, 2, 0, 0)
    gaps = (
        FreeTimeGap(
            start_time=_utc(2026, 6, 7, 10, 0),
            end_time=_utc(2026, 6, 7, 10, 20),
            week_start=week_start,
        ),
    )
    activities_by_id = {
        FreeTimeActivityID(activity_id): _activity_dto(
            activity_id,
            minimum_block_size_minutes=25,
        ),
    }

    specs = assign_free_time_to_gaps(
        gaps=gaps,
        effective_fractions=((FreeTimeActivityID(activity_id), Decimal("1")),),
        activities_by_id=activities_by_id,
    )

    assert specs == ()
