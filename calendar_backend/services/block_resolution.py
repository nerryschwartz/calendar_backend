"""Block resolution service: refresh horizon/repetitions and resolve block leaves.

Resolution does not write block calendar entries. Downstream assignment
``assign_blocks`` must refuse when ``ResolveBlocksResult.invalid_incomplete`` is
non-empty (``MessageCode.INVALID_INCOMPLETE_BLOCKS_BLOCK_ASSIGNMENT``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from calendar_backend.db.session import transaction
from calendar_backend.domain.block_resolution import ResolveBlocksResult, resolve_blocks_from_graph
from calendar_backend.domain.errors import ServiceTransactionAborted
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.plans import Plan
from calendar_backend.services.master_horizon import (
    MasterHorizonService,
    validate_run_started_at,
)
from calendar_backend.services.plan_tree_invariant import PlanTreeInvariantService
from calendar_backend.services.repetition import RepetitionService
from calendar_backend.services.task_resolution import load_plan_graph


class BlockResolutionService:
    """Resolve the master plan tree into block buckets for scheduling."""

    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def resolve_blocks(self, run_started_at: datetime) -> ServiceResult[ResolveBlocksResult]:
        """Refresh horizon/repetitions, validate the tree, and return resolved blocks."""
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

                plans = load_plan_graph(txn)
                result = _resolve_from_current_tree(run_started_at, plans=plans)
                return ok(result)
        except ServiceTransactionAborted as exc:
            return fail(*exc.errors)


def _resolve_from_current_tree(
    run_started_at: datetime,
    *,
    plans: tuple[Plan, ...],
) -> ResolveBlocksResult:
    """Read-only resolution test seam: graph load without refresh side effects."""
    return resolve_blocks_from_graph(run_started_at, plans)
