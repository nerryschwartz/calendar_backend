"""Read-only master plan tree invariant diagnostics.

Loads the full committed plan graph and validates ideal persisted shape per
repo conventions §7-§9 via ``validate_master_tree_graph``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from calendar_backend.db.session import transaction
from calendar_backend.domain.invariant_validation import validate_master_tree_graph
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.models.chains import GoalChildChain
from calendar_backend.models.constraints import TimeConstraintGroup
from calendar_backend.models.plans import GoalPlan, Plan


class PlanTreeInvariantService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def validate_master_tree(self) -> ServiceResult[None]:
        with transaction(self._session) as txn:
            plans = tuple(
                txn.scalars(
                    select(Plan).options(
                        selectinload(Plan.goal_plan)
                        .selectinload(GoalPlan.chains)
                        .selectinload(GoalChildChain.items),
                        selectinload(Plan.task_plan),
                        selectinload(Plan.repetition_plan),
                        selectinload(Plan.constraint_groups).selectinload(
                            TimeConstraintGroup.windows
                        ),
                    )
                ).all()
            )
            violations = validate_master_tree_graph(plans)
            if violations:
                return fail(*violations)
            return ok(None)
