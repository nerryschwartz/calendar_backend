"""Task resolution service: refresh horizon/repetitions and resolve the master tree."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from calendar_backend.db.session import transaction
from calendar_backend.domain.errors import ServiceTransactionAborted
from calendar_backend.domain.resolution import ResolveTasksResult, resolve_tasks_from_graph
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.chains import GoalChildChain
from calendar_backend.models.constraints import TimeConstraintGroup
from calendar_backend.models.plans import GoalPlan, Plan, RepetitionPlan
from calendar_backend.services.master_horizon import (
    MasterHorizonService,
    validate_run_started_at,
)
from calendar_backend.services.plan_tree_invariant import PlanTreeInvariantService
from calendar_backend.services.repetition import RepetitionService


class TaskResolutionService:
    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def resolve_tasks(self, run_started_at: datetime) -> ServiceResult[ResolveTasksResult]:
        validation_error = validate_run_started_at(run_started_at)
        if validation_error is not None:
            return fail(validation_error)

        try:
            with transaction(self._session) as txn:
                horizon_result = MasterHorizonService(txn, self._clock).refresh_master_horizon(
                    run_started_at
                )
                if not horizon_result.success:
                    raise ServiceTransactionAborted(horizon_result.errors)

                repetition_result = RepetitionService(txn, self._clock).refresh_all_repetitions(
                    run_started_at
                )
                if not repetition_result.success:
                    raise ServiceTransactionAborted(repetition_result.errors)

                invariant_result = PlanTreeInvariantService(txn).validate_master_tree()
                if not invariant_result.success:
                    raise ServiceTransactionAborted(invariant_result.errors)

                plans = _load_plan_graph(txn)
                result = _resolve_from_current_tree(run_started_at, plans=plans)
                return ok(result)
        except ServiceTransactionAborted as exc:
            return fail(*exc.errors)


def _resolve_from_current_tree(
    run_started_at: datetime,
    *,
    plans: tuple[Plan, ...],
) -> ResolveTasksResult:
    return resolve_tasks_from_graph(run_started_at, plans)


def _load_plan_graph(session: Session) -> tuple[Plan, ...]:
    return tuple(
        session.scalars(
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
