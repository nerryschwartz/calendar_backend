"""Conflict deletion suggestion service: rank legal delete operations for assignment conflicts."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from calendar_backend.db.session import transaction
from calendar_backend.deletion.preview_service import DeletionPreviewService
from calendar_backend.domain.deletion import (
    AssignmentConflict,
    DeletionCandidate,
    build_deletion_candidate,
    generate_deletion_operations,
    rank_deletion_candidates,
)
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.results import ServiceResult, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.plans import Plan


class ConflictDeletionSuggestionService:
    """Generate ranked deletion candidates for an assignment conflict."""

    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def suggest_for_conflict(
        self,
        conflict: AssignmentConflict,
    ) -> ServiceResult[tuple[DeletionCandidate, ...]]:
        """Preview and rank legal single-root delete operations for conflicting plans.

        # TODO(Prompt 14 / ConflictAnalysisService): accept fully analyzed conflicts
        # from ConflictAnalysisService instead of minimal AssignmentConflict input.
        """
        with transaction(self._session) as txn:
            master_plan_id_value = txn.scalar(select(Plan.plan_id).where(Plan.is_master.is_(True)))
            master_plan_id = (
                PlanID(master_plan_id_value) if master_plan_id_value is not None else None
            )

        operations = generate_deletion_operations(
            conflict,
            master_plan_id=master_plan_id,
        )
        preview_service = DeletionPreviewService(self._session, self._clock)
        candidates: list[DeletionCandidate] = []
        for operation in operations:
            preview_result = preview_service.preview_delete(operation)
            if not preview_result.success:
                continue
            assert preview_result.value is not None
            candidates.append(build_deletion_candidate(operation, preview_result.value, conflict))

        return ok(rank_deletion_candidates(tuple(candidates)))
