"""Pure DTOs and validation for free-time activity management."""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from calendar_backend.domain.constraints import merge_or_windows
from calendar_backend.domain.enums import FreeTimeWeekStartDay, PlanKind
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import FreeTimeActivityID, PlanID
from calendar_backend.domain.time import TimeWindow
from calendar_backend.models.chains import GoalChildChain, GoalChildChainItem
from calendar_backend.models.free_time import FreeTimeActivity
from calendar_backend.models.plans import GoalPlan, Plan, RepetitionPlan
from calendar_backend.models.repetitions import RepetitionInstance

_DECIMAL_ONE = Decimal("1")

_WEEKDAY_BY_START_DAY: dict[FreeTimeWeekStartDay, int] = {
    FreeTimeWeekStartDay.MONDAY: 0,
    FreeTimeWeekStartDay.TUESDAY: 1,
    FreeTimeWeekStartDay.WEDNESDAY: 2,
    FreeTimeWeekStartDay.THURSDAY: 3,
    FreeTimeWeekStartDay.FRIDAY: 4,
    FreeTimeWeekStartDay.SATURDAY: 5,
    FreeTimeWeekStartDay.SUNDAY: 6,
}


@dataclass(frozen=True)
class FreeTimeActivityDTO:
    free_time_activity_id: FreeTimeActivityID
    name: str
    enabled: bool
    real_fraction: Decimal
    minimum_block_size_minutes: int
    prerequisite_plan_ids: tuple[PlanID, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class FreeTimeGap:
    start_time: datetime
    end_time: datetime
    week_start: datetime


@dataclass(frozen=True)
class FreeTimePlanGraph:
    plans_by_id: dict[uuid.UUID, Plan]
    template_subtree_ids: frozenset[uuid.UUID]


def free_time_plan_graph_from_plans(plans: tuple[Plan, ...]) -> FreeTimePlanGraph:
    plans_by_id = {plan.plan_id: plan for plan in plans}
    children_by_parent: dict[uuid.UUID, list[uuid.UUID]] = {}
    for plan in plans:
        if plan.parent_id is not None:
            children_by_parent.setdefault(plan.parent_id, []).append(plan.plan_id)

    template_subtree_ids: set[uuid.UUID] = set()
    for plan in plans:
        if plan.repetition_plan is None:
            continue
        template_subtree_ids.update(
            _collect_descendant_ids(
                plan.repetition_plan.template_root_id,
                children_by_parent,
                include_root=True,
            )
        )

    return FreeTimePlanGraph(
        plans_by_id=plans_by_id,
        template_subtree_ids=frozenset(template_subtree_ids),
    )


def is_plan_logically_complete(plan_id: PlanID, graph: FreeTimePlanGraph) -> bool:
    memo: dict[PlanID, bool] = {}
    visiting: set[PlanID] = set()
    return _is_plan_logically_complete(plan_id, graph, memo=memo, visiting=visiting)


def blocked_activity_ids(
    activities: tuple[FreeTimeActivityDTO, ...],
    graph: FreeTimePlanGraph,
) -> frozenset[FreeTimeActivityID]:
    blocked: list[FreeTimeActivityID] = []
    for activity in activities:
        if any(
            not is_plan_logically_complete(prerequisite_id, graph)
            for prerequisite_id in activity.prerequisite_plan_ids
        ):
            blocked.append(activity.free_time_activity_id)
    return frozenset(blocked)


def compute_effective_fractions(
    activities: tuple[FreeTimeActivityDTO, ...],
    blocked_activity_ids: frozenset[FreeTimeActivityID],
) -> tuple[tuple[FreeTimeActivityID, Decimal], ...]:
    survivors: list[FreeTimeActivityDTO] = []
    for activity in activities:
        if not activity.enabled:
            continue
        if activity.real_fraction <= 0:
            continue
        if activity.free_time_activity_id in blocked_activity_ids:
            continue
        survivors.append(activity)

    total = sum((activity.real_fraction for activity in survivors), Decimal("0"))
    if total <= 0:
        return ()

    effective = [
        (activity.free_time_activity_id, activity.real_fraction / total) for activity in survivors
    ]
    return tuple(sorted(effective, key=lambda pair: str(pair[0])))


def discover_free_time_gaps(
    *,
    run_started_at: datetime,
    master_horizon_end: datetime,
    task_blockers: tuple[TimeWindow, ...],
    week_start_day: FreeTimeWeekStartDay,
    local_timezone: str,
) -> tuple[FreeTimeGap, ...]:
    if run_started_at >= master_horizon_end:
        return ()

    clipped_blockers: list[TimeWindow] = []
    for blocker in task_blockers:
        start_time = max(blocker.start_time, run_started_at)
        end_time = min(blocker.end_time, master_horizon_end)
        if start_time < end_time:
            clipped_blockers.append(TimeWindow(start_time=start_time, end_time=end_time))
    merged_blockers = merge_or_windows(tuple(clipped_blockers))

    gaps: list[FreeTimeGap] = []
    for week_anchor_utc, bucket_start, bucket_end in _week_buckets(
        run_started_at,
        master_horizon_end,
        week_start_day,
        local_timezone,
    ):
        bucket = TimeWindow(start_time=bucket_start, end_time=bucket_end)
        bucket_blockers = tuple(
            TimeWindow(
                start_time=max(blocker.start_time, bucket_start),
                end_time=min(blocker.end_time, bucket_end),
            )
            for blocker in merged_blockers
            if blocker.start_time < bucket_end and blocker.end_time > bucket_start
        )
        for gap_start, gap_end in _gaps_in_window(bucket, bucket_blockers):
            gaps.append(
                FreeTimeGap(
                    start_time=gap_start,
                    end_time=gap_end,
                    week_start=week_anchor_utc,
                )
            )

    return tuple(sorted(gaps, key=lambda gap: (gap.week_start, gap.start_time, gap.end_time)))


def _is_plan_logically_complete(
    plan_id: PlanID,
    graph: FreeTimePlanGraph,
    *,
    memo: dict[PlanID, bool],
    visiting: set[PlanID],
) -> bool:
    if plan_id in memo:
        return memo[plan_id]
    if plan_id in visiting:
        return False
    visiting.add(plan_id)

    try:
        if plan_id in graph.template_subtree_ids:
            result = False
        else:
            plan = graph.plans_by_id.get(plan_id)
            if plan is None:
                result = False
            elif plan.plan_kind == PlanKind.TASK:
                result = plan.task_plan is not None and plan.task_plan.user_completed
            elif plan.plan_kind == PlanKind.GOAL:
                result = _goal_is_logically_complete(plan, graph, memo=memo, visiting=visiting)
            elif plan.plan_kind == PlanKind.REPETITION:
                result = _repetition_is_logically_complete(
                    plan, graph, memo=memo, visiting=visiting
                )
            else:
                result = False
    finally:
        visiting.discard(plan_id)

    memo[plan_id] = result
    return result


def _goal_is_logically_complete(
    plan: Plan,
    graph: FreeTimePlanGraph,
    *,
    memo: dict[PlanID, bool],
    visiting: set[PlanID],
) -> bool:
    goal_plan = plan.goal_plan
    if goal_plan is None:
        return False

    for chain in _ordered_chains(goal_plan):
        if not chain.is_critical:
            continue
        for item in _sorted_chain_items(chain):
            child_id = PlanID(item.child_plan_id)
            if not _is_plan_logically_complete(child_id, graph, memo=memo, visiting=visiting):
                return False
    return True


def _repetition_is_logically_complete(
    plan: Plan,
    graph: FreeTimePlanGraph,
    *,
    memo: dict[PlanID, bool],
    visiting: set[PlanID],
) -> bool:
    repetition_plan = plan.repetition_plan
    if repetition_plan is None or repetition_plan.generated_at is None:
        return False

    for instance in _ordered_repetition_instances(repetition_plan):
        if not instance.is_critical:
            continue
        root_id = PlanID(instance.root_clone_id)
        if not _is_plan_logically_complete(root_id, graph, memo=memo, visiting=visiting):
            return False
    return True


def free_time_activity_dto_from_row(activity: FreeTimeActivity) -> FreeTimeActivityDTO:
    prerequisite_plan_ids = tuple(
        sorted(
            (PlanID(prerequisite.source_plan_id) for prerequisite in activity.prerequisites),
            key=str,
        )
    )
    return FreeTimeActivityDTO(
        free_time_activity_id=FreeTimeActivityID(activity.free_time_activity_id),
        name=activity.name,
        enabled=activity.enabled,
        real_fraction=activity.real_fraction,
        minimum_block_size_minutes=activity.minimum_block_size_minutes,
        prerequisite_plan_ids=prerequisite_plan_ids,
        created_at=activity.created_at,
        updated_at=activity.updated_at,
    )


def validate_activity_fields(
    *,
    name: str,
    real_fraction: Decimal,
    minimum_block_size_minutes: int,
    enabled: bool,
) -> ServiceMessage | None:
    if not name.strip():
        return ServiceMessage(
            code=MessageCode.INVALID_CREATE_PAYLOAD,
            message="Free-time activity name must be non-empty",
            details={},
        )

    if minimum_block_size_minutes < 0:
        return ServiceMessage(
            code=MessageCode.INVALID_MINIMUM_BLOCK_SIZE,
            message="minimum_block_size_minutes must be non-negative",
            details={"minimum_block_size_minutes": str(minimum_block_size_minutes)},
        )

    if real_fraction < 0:
        return ServiceMessage(
            code=MessageCode.INVALID_FREE_TIME_FRACTIONS,
            message="real_fraction must be non-negative",
            details={"real_fraction": str(real_fraction)},
        )

    if enabled and real_fraction <= 0:
        return ServiceMessage(
            code=MessageCode.INVALID_FREE_TIME_FRACTIONS,
            message="Enabled free-time activities must have a positive real_fraction",
            details={"real_fraction": str(real_fraction)},
        )

    return None


def validate_enabled_fractions_sum_to_one(
    activities: tuple[FreeTimeActivity, ...],
) -> ServiceMessage | None:
    total = Decimal("0")
    contributing: list[str] = []
    for activity in activities:
        if not activity.enabled or activity.real_fraction <= 0:
            continue
        total += activity.real_fraction
        contributing.append(str(activity.free_time_activity_id))

    if total == _DECIMAL_ONE:
        return None

    return ServiceMessage(
        code=MessageCode.INVALID_FREE_TIME_FRACTIONS,
        message="Enabled positive free-time fractions must sum to 1",
        details={
            "sum": str(total),
            "activity_ids": ",".join(sorted(contributing)),
        },
    )


def _week_buckets(
    run_started_at: datetime,
    master_horizon_end: datetime,
    week_start_day: FreeTimeWeekStartDay,
    local_timezone: str,
) -> tuple[tuple[datetime, datetime, datetime], ...]:
    """Return week_anchor_utc, bucket_start, bucket_end per local week in universe."""
    tz = ZoneInfo(local_timezone)
    local_run = run_started_at.astimezone(tz)
    week_anchor_local = _local_week_start(local_run, week_start_day)
    week_anchor_utc = week_anchor_local.astimezone(UTC)

    buckets: list[tuple[datetime, datetime, datetime]] = []
    while week_anchor_utc < master_horizon_end:
        next_week_anchor_local = week_anchor_local + timedelta(days=7)
        next_week_anchor_utc = next_week_anchor_local.astimezone(UTC)
        bucket_start = max(week_anchor_utc, run_started_at)
        bucket_end = min(next_week_anchor_utc, master_horizon_end)
        if bucket_start < bucket_end:
            buckets.append((week_anchor_utc, bucket_start, bucket_end))
        week_anchor_local = next_week_anchor_local
        week_anchor_utc = next_week_anchor_utc

    return tuple(buckets)


def _local_week_start(local_dt: datetime, week_start_day: FreeTimeWeekStartDay) -> datetime:
    target_weekday = _WEEKDAY_BY_START_DAY[week_start_day]
    days_since_start = (local_dt.weekday() - target_weekday) % 7
    week_start_date = local_dt.date() - timedelta(days=days_since_start)
    return datetime.combine(week_start_date, time.min, tzinfo=local_dt.tzinfo)


def _gaps_in_window(
    window: TimeWindow,
    blockers: tuple[TimeWindow, ...],
) -> tuple[tuple[datetime, datetime], ...]:
    blocking = sorted(
        (
            segment
            for segment in blockers
            if segment.start_time < window.end_time and segment.end_time > window.start_time
        ),
        key=lambda segment: segment.start_time,
    )

    gaps: list[tuple[datetime, datetime]] = []
    cursor = window.start_time
    for segment in blocking:
        gap_end = min(segment.start_time, window.end_time)
        if cursor < gap_end:
            gaps.append((cursor, gap_end))
        cursor = max(cursor, segment.end_time)
        if cursor >= window.end_time:
            break

    if cursor < window.end_time:
        gaps.append((cursor, window.end_time))

    return tuple(gaps)


def _collect_descendant_ids(
    root_id: uuid.UUID,
    children_by_parent: dict[uuid.UUID, list[uuid.UUID]],
    *,
    include_root: bool,
) -> set[uuid.UUID]:
    collected: set[uuid.UUID] = set()
    queue: deque[uuid.UUID] = deque([root_id])
    while queue:
        plan_id = queue.popleft()
        if plan_id in collected:
            continue
        collected.add(plan_id)
        queue.extend(children_by_parent.get(plan_id, ()))
    if not include_root:
        collected.discard(root_id)
    return collected


def _ordered_chains(goal_plan: GoalPlan) -> tuple[GoalChildChain, ...]:
    return tuple(
        sorted(
            goal_plan.chains,
            key=lambda chain: (
                not chain.is_critical,
                chain.sort_order,
                str(chain.goal_child_chain_id),
            ),
        )
    )


def _sorted_chain_items(chain: GoalChildChain) -> tuple[GoalChildChainItem, ...]:
    return tuple(
        sorted(
            chain.items,
            key=lambda item: (item.position, str(item.goal_child_chain_item_id)),
        )
    )


def _ordered_repetition_instances(
    repetition_plan: RepetitionPlan,
) -> tuple[RepetitionInstance, ...]:
    return tuple(
        sorted(
            repetition_plan.instances,
            key=lambda instance: (
                not instance.is_critical,
                instance.sort_order,
                str(instance.repetition_instance_id),
            ),
        )
    )
