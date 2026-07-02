"""Plan tree insert/attach primitives and plan-wide rename/delete service."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, selectinload

from calendar_backend.db.session import transaction
from calendar_backend.domain.deletion import compute_deletion_impact
from calendar_backend.domain.dtos import PlanDeletionPreviewDTO
from calendar_backend.domain.enums import CloneStatus, PlanKind, RepeatMode
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID, new_id
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.chains import GoalChildChain, GoalChildChainItem
from calendar_backend.models.constraints import TimeConstraintGroup
from calendar_backend.models.constraints import TimeWindow as TimeWindowRow
from calendar_backend.models.free_time import FreeTimeActivityPrerequisite
from calendar_backend.models.plans import GoalPlan, Plan, RepetitionPlan, TaskPlan
from calendar_backend.models.repetitions import RepetitionInstance


class PlanTreeService:
    """Plan-wide identity/existence mutations and repo-internal insert/attach primitives.

    Sibling services (for example ``GoalService``) may call ``make_*`` and
    ``attach_under_parent``; those methods are not part of the external API.
    """

    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def rename_plan(self, plan_id: PlanID, name: str) -> ServiceResult[None]:
        with transaction(self._session) as txn:
            plan = txn.get(Plan, plan_id)
            if plan is None:
                return fail(
                    ServiceMessage(
                        code=MessageCode.PLAN_NOT_FOUND,
                        message="Plan not found",
                        details={"plan_id": str(plan_id)},
                    )
                )
            if plan.is_master:
                return fail(
                    ServiceMessage(
                        code=MessageCode.MASTER_MUTATION_FORBIDDEN,
                        message="Master plan cannot be renamed",
                        details={"plan_id": str(plan_id)},
                    )
                )

            now = self._clock.now_utc()
            plan.name = name
            plan.updated_at = now
            txn.flush()
            return ok(None)

    def preview_delete(self, plan_id: PlanID) -> ServiceResult[PlanDeletionPreviewDTO]:
        with transaction(self._session) as txn:
            root_plan = txn.get(Plan, plan_id)
            if root_plan is None:
                return fail(
                    ServiceMessage(
                        code=MessageCode.PLAN_NOT_FOUND,
                        message="Plan not found",
                        details={"plan_id": str(plan_id)},
                    )
                )
            if root_plan.is_master:
                return fail(
                    ServiceMessage(
                        code=MessageCode.MASTER_DELETE_FORBIDDEN,
                        message="Master plan cannot be deleted",
                        details={"plan_id": str(plan_id)},
                    )
                )

            plans, calendar_entries = _load_deletion_graph(txn)
            preview = compute_deletion_impact(plan_id, plans, calendar_entries)
            return ok(preview)

    def delete_plan(self, plan_id: PlanID) -> ServiceResult[None]:
        preview_result = self.preview_delete(plan_id)
        if not preview_result.success:
            return fail(*preview_result.errors)

        assert preview_result.value is not None
        preview = preview_result.value

        with transaction(self._session) as txn:
            master = txn.scalar(select(Plan).where(Plan.is_master))
            if master is not None and PlanID(master.plan_id) in preview.affected_plan_ids:
                return fail(
                    ServiceMessage(
                        code=MessageCode.MASTER_DELETE_FORBIDDEN,
                        message="Master plan cannot be deleted",
                        details={"plan_id": str(plan_id)},
                    )
                )

            plans, _calendar_entries = _load_deletion_graph(txn)
            _execute_plan_deletes(txn, preview, plans)
            txn.flush()
            return ok(None)

    def make_goal(
        self,
        txn: Session,
        *,
        name: str,
        clone_status: CloneStatus = CloneStatus.NOT_CLONED,
        now: datetime,
    ) -> Plan:
        plan_id = new_id(PlanID)
        plan = Plan(
            plan_id=plan_id,
            plan_kind=PlanKind.GOAL,
            name=name,
            parent_id=None,
            is_master=False,
            cloned_from_id=None,
            clone_status=clone_status,
            created_at=now,
            updated_at=now,
        )
        txn.add(plan)
        txn.add(GoalPlan(plan_id=plan_id))
        return plan

    def make_task(
        self,
        txn: Session,
        *,
        name: str,
        duration_minutes: int,
        divisible: bool,
        minimum_chunk_size_minutes: int | None,
        now: datetime,
    ) -> tuple[Plan, TaskPlan]:
        plan_id = new_id(PlanID)
        plan = Plan(
            plan_id=plan_id,
            plan_kind=PlanKind.TASK,
            name=name,
            parent_id=None,
            is_master=False,
            cloned_from_id=None,
            clone_status=CloneStatus.NOT_CLONED,
            created_at=now,
            updated_at=now,
        )
        txn.add(plan)
        task_plan = TaskPlan(
            plan_id=plan_id,
            duration_minutes=duration_minutes,
            divisible=divisible,
            minimum_chunk_size_minutes=minimum_chunk_size_minutes,
            user_completed=False,
            completed_at=None,
        )
        txn.add(task_plan)
        return plan, task_plan

    def make_repetition(
        self,
        txn: Session,
        *,
        name: str,
        repeat_mode: RepeatMode,
        start_time: datetime,
        repeat_interval_minutes: int,
        manual_count: int | None,
        end_time: datetime | None,
        template_root_id: PlanID,
        default_instance_critical: bool,
        now: datetime,
    ) -> tuple[Plan, RepetitionPlan]:
        plan_id = new_id(PlanID)
        plan = Plan(
            plan_id=plan_id,
            plan_kind=PlanKind.REPETITION,
            name=name,
            parent_id=None,
            is_master=False,
            cloned_from_id=None,
            clone_status=CloneStatus.NOT_CLONED,
            created_at=now,
            updated_at=now,
        )
        txn.add(plan)
        repetition_detail = RepetitionPlan(
            plan_id=plan_id,
            repeat_mode=repeat_mode,
            start_time=start_time,
            repeat_interval_minutes=repeat_interval_minutes,
            manual_count=manual_count,
            end_time=end_time,
            template_root_id=template_root_id,
            default_instance_critical=default_instance_critical,
            generated_at=None,
        )
        txn.add(repetition_detail)
        return plan, repetition_detail

    def attach_under_parent(
        self,
        txn: Session,
        *,
        child_plan_id: PlanID,
        parent_id: PlanID,
        now: datetime,
    ) -> None:
        child_plan = txn.get(Plan, child_plan_id)
        child_plan.parent_id = parent_id  # pyright: ignore[reportOptionalMemberAccess]  # type checker: trusted internal caller
        child_plan.updated_at = now  # pyright: ignore[reportOptionalMemberAccess]


def _load_deletion_graph(
    txn: Session,
) -> tuple[tuple[Plan, ...], tuple[CalendarEntry, ...]]:
    plans = tuple(
        txn.scalars(
            select(Plan).options(
                selectinload(Plan.goal_plan)
                .selectinload(GoalPlan.chains)
                .selectinload(GoalChildChain.items),
                selectinload(Plan.task_plan),
                selectinload(Plan.repetition_plan).selectinload(RepetitionPlan.instances),
                selectinload(Plan.constraint_groups).selectinload(TimeConstraintGroup.windows),
            )
        ).all()
    )
    plan_ids = [plan.plan_id for plan in plans]
    if not plan_ids:
        return plans, ()

    calendar_entries = tuple(
        txn.scalars(select(CalendarEntry).where(CalendarEntry.source_plan_id.in_(plan_ids))).all()
    )
    return plans, calendar_entries


def _execute_plan_deletes(
    txn: Session,
    preview: PlanDeletionPreviewDTO,
    plans: tuple[Plan, ...],
) -> None:
    affected_plan_ids = preview.affected_plan_ids
    if not affected_plan_ids:
        return

    affected_set = set(affected_plan_ids)
    plans_by_id = {plan.plan_id: plan for plan in plans}

    if preview.affected_calendar_entry_ids:
        txn.execute(
            delete(CalendarEntry).where(
                CalendarEntry.calendar_entry_id.in_(preview.affected_calendar_entry_ids)
            )
        )

    txn.execute(
        # TODO(Prompt 15): FreeTimeActivityService should delete or disable orphan activities
        # when plan-backed prerequisites are removed; rows deleted here for FK safety only.
        delete(FreeTimeActivityPrerequisite).where(
            FreeTimeActivityPrerequisite.source_plan_id.in_(affected_plan_ids)
        )
    )

    group_ids = [
        group.time_constraint_group_id
        for plan in plans
        if plan.plan_id in affected_set
        for group in plan.constraint_groups
    ]
    if group_ids:
        txn.execute(delete(TimeWindowRow).where(TimeWindowRow.group_id.in_(group_ids)))
        txn.execute(
            delete(TimeConstraintGroup).where(
                TimeConstraintGroup.time_constraint_group_id.in_(group_ids)
            )
        )

    txn.execute(
        delete(GoalChildChainItem).where(GoalChildChainItem.child_plan_id.in_(affected_plan_ids))
    )
    txn.execute(delete(GoalChildChain).where(GoalChildChain.parent_goal_id.in_(affected_plan_ids)))

    txn.execute(
        delete(RepetitionInstance).where(
            or_(
                RepetitionInstance.repetition_plan_id.in_(affected_plan_ids),
                RepetitionInstance.root_clone_id.in_(affected_plan_ids),
            )
        )
    )

    txn.execute(delete(TaskPlan).where(TaskPlan.plan_id.in_(affected_plan_ids)))
    txn.execute(delete(RepetitionPlan).where(RepetitionPlan.plan_id.in_(affected_plan_ids)))
    txn.execute(delete(GoalPlan).where(GoalPlan.plan_id.in_(affected_plan_ids)))

    for wave in _plan_deletion_waves(affected_set, plans_by_id):
        txn.execute(delete(Plan).where(Plan.plan_id.in_(wave)))


def _plan_deletion_waves(
    affected: set[PlanID],
    plans_by_id: dict[uuid.UUID, Plan],
) -> tuple[tuple[PlanID, ...], ...]:
    """Deletion waves: children and clones before parents and clone referents."""
    dependents: dict[PlanID, int] = dict.fromkeys(affected, 0)
    for plan_id in affected:
        plan = plans_by_id[plan_id]
        if plan.parent_id is not None:
            parent_id = PlanID(plan.parent_id)
            if parent_id in affected:
                dependents[parent_id] += 1
        if plan.cloned_from_id is not None:
            referent_id = PlanID(plan.cloned_from_id)
            if referent_id in affected:
                dependents[referent_id] += 1

    remaining = set(affected)
    waves: list[tuple[PlanID, ...]] = []

    while remaining:
        ready = tuple(sorted(plan_id for plan_id in remaining if dependents[plan_id] == 0))
        if not ready:
            msg = "Plan deletion order could not be resolved for affected set"
            raise RuntimeError(msg)

        for plan_id in ready:
            remaining.discard(plan_id)
            plan = plans_by_id[plan_id]
            if plan.parent_id is not None:
                parent_id = PlanID(plan.parent_id)
                if parent_id in remaining:
                    dependents[parent_id] -= 1
            if plan.cloned_from_id is not None:
                referent_id = PlanID(plan.cloned_from_id)
                if referent_id in remaining:
                    dependents[referent_id] -= 1

        waves.append(ready)

    return tuple(waves)
