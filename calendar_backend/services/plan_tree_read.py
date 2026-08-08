"""Read-only plan tree queries."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from calendar_backend.domain.dtos import (
    TimeConstraintGroupDTO,
    block_plan_dto_from_rows,
    goal_plan_dto_from_plan,
    repetition_plan_dto_from_rows,
    task_plan_dto_from_rows,
    time_constraint_group_dto_from_rows,
)
from calendar_backend.domain.enums import PlanKind
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.plan_read import (
    PlanAncestryItemDTO,
    PlanChildSummaryDTO,
    PlanDetailDTO,
    PlanPrerequisiteSummaryDTO,
    PlanSearchResultDTO,
)
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.constraints import TimeConstraintGroup
from calendar_backend.models.plans import Plan
from calendar_backend.models.prerequisites import PlanPrerequisite
from calendar_backend.services.master_plan import MasterPlanService
from calendar_backend.services.task_resolution import load_plan_graph


class PlanTreeReadService:
    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def ensure_master_and_get_id(self) -> ServiceResult[PlanID]:
        master_result = MasterPlanService(self._session, self._clock).ensure_master_exists()
        if not master_result.success or master_result.value is None:
            return fail(*master_result.errors)
        return ok(master_result.value.plan_id)

    def get_plan_detail(self, plan_id: PlanID) -> ServiceResult[PlanDetailDTO]:
        plan = self._session.scalar(
            select(Plan)
            .where(Plan.plan_id == plan_id)
            .options(
                selectinload(Plan.task_plan),
                selectinload(Plan.block_plan),
                selectinload(Plan.repetition_plan),
                selectinload(Plan.goal_plan),
            )
        )
        if plan is None:
            return fail(
                ServiceMessage(
                    code=MessageCode.PLAN_NOT_FOUND,
                    message="Plan not found",
                    details={"plan_id": str(plan_id)},
                )
            )

        plans_by_id = {PlanID(row.plan_id): row for row in load_plan_graph(self._session)}
        ancestry = _build_ancestry(plan, plans_by_id)
        children = _load_children(self._session, plan_id)
        prerequisites = _load_prerequisites(self._session, plan_id, plans_by_id)
        constraint_groups = _load_time_constraints(self._session, plan_id)

        goal_detail = goal_plan_dto_from_plan(plan) if plan.plan_kind == PlanKind.GOAL else None
        task_detail = (
            task_plan_dto_from_rows(plan, plan.task_plan)
            if plan.plan_kind == PlanKind.TASK and plan.task_plan is not None
            else None
        )
        block_detail = (
            block_plan_dto_from_rows(plan, plan.block_plan)
            if plan.plan_kind == PlanKind.BLOCK and plan.block_plan is not None
            else None
        )
        repetition_detail = (
            repetition_plan_dto_from_rows(plan, plan.repetition_plan)
            if plan.plan_kind == PlanKind.REPETITION and plan.repetition_plan is not None
            else None
        )

        return ok(
            PlanDetailDTO(
                plan_id=PlanID(plan.plan_id),
                name=plan.name,
                plan_kind=plan.plan_kind,
                is_master=plan.is_master,
                parent_id=PlanID(plan.parent_id) if plan.parent_id is not None else None,
                goal_is_critical=plan.goal_is_critical,
                goal_sort_order=plan.goal_sort_order,
                created_at=plan.created_at,
                updated_at=plan.updated_at,
                ancestry=ancestry,
                children=children,
                prerequisite_plan_ids=tuple(item.prerequisite_plan_id for item in prerequisites),
                prerequisites=prerequisites,
                time_constraint_groups=constraint_groups,
                goal_detail=goal_detail,
                task_detail=task_detail,
                block_detail=block_detail,
                repetition_detail=repetition_detail,
            )
        )

    def search_plans(self, query: str) -> ServiceResult[tuple[PlanSearchResultDTO, ...]]:
        normalized = query.strip().lower()
        if not normalized:
            return ok(())

        rows = self._session.scalars(select(Plan)).all()
        results: list[PlanSearchResultDTO] = []
        for row in rows:
            if normalized in row.name.lower() or normalized in str(row.plan_id).lower():
                results.append(
                    PlanSearchResultDTO(
                        plan_id=PlanID(row.plan_id),
                        name=row.name,
                        plan_kind=row.plan_kind,
                        parent_id=PlanID(row.parent_id) if row.parent_id is not None else None,
                    )
                )
        results.sort(key=lambda item: item.name.lower())
        return ok(tuple(results))


def _build_ancestry(
    plan: Plan,
    plans_by_id: dict[PlanID, Plan],
) -> tuple[PlanAncestryItemDTO, ...]:
    chain: list[PlanAncestryItemDTO] = []
    current: Plan | None = plan
    while current is not None:
        chain.append(
            PlanAncestryItemDTO(
                plan_id=PlanID(current.plan_id),
                name=current.name,
                plan_kind=current.plan_kind,
            )
        )
        if current.parent_id is None:
            break
        current = plans_by_id.get(PlanID(current.parent_id))
    chain.reverse()
    return tuple(chain)


def _load_children(session: Session, plan_id: PlanID) -> tuple[PlanChildSummaryDTO, ...]:
    rows = session.scalars(
        select(Plan)
        .where(Plan.parent_id == plan_id)
        .order_by(
            Plan.goal_is_critical.desc(),
            Plan.goal_sort_order.asc(),
            Plan.name.asc(),
        )
    ).all()
    return tuple(
        PlanChildSummaryDTO(
            plan_id=PlanID(row.plan_id),
            name=row.name,
            plan_kind=row.plan_kind,
            goal_is_critical=row.goal_is_critical,
            goal_sort_order=row.goal_sort_order,
        )
        for row in rows
    )


def _load_prerequisites(
    session: Session,
    plan_id: PlanID,
    plans_by_id: dict[PlanID, Plan],
) -> tuple[PlanPrerequisiteSummaryDTO, ...]:
    edges = session.scalars(
        select(PlanPrerequisite).where(PlanPrerequisite.plan_id == plan_id)
    ).all()
    results: list[PlanPrerequisiteSummaryDTO] = []
    for edge in edges:
        prereq = plans_by_id.get(PlanID(edge.prerequisite_plan_id))
        if prereq is None:
            continue
        results.append(
            PlanPrerequisiteSummaryDTO(
                prerequisite_plan_id=PlanID(prereq.plan_id),
                name=prereq.name,
                plan_kind=prereq.plan_kind,
            )
        )
    return tuple(results)


def _load_time_constraints(
    session: Session,
    plan_id: PlanID,
) -> tuple[TimeConstraintGroupDTO, ...]:
    groups = session.scalars(
        select(TimeConstraintGroup)
        .where(TimeConstraintGroup.plan_id == plan_id)
        .options(selectinload(TimeConstraintGroup.windows))
    ).all()
    dtos: list[TimeConstraintGroupDTO] = []
    for group in groups:
        dtos.append(time_constraint_group_dto_from_rows(group, tuple(group.windows)))
    return tuple(dtos)
