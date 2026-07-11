"""Orchestration service: compose resolution, task assignment, and free-time assignment."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from calendar_backend.domain.orchestration import RefreshScheduleResult
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.services.free_time_assignment import FreeTimeAssignmentService
from calendar_backend.services.task_assignment import TaskAssignmentService
from calendar_backend.services.task_resolution import TaskResolutionService


class OrchestrationService:
    """Compose refresh_schedule from task resolution, assignment, and free-time services.

    Manual invocation only in V1; other services do not call this automatically.
    ``run_started_at`` is validated at the resolution boundary (first pipeline stage).
    """

    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def refresh_schedule(
        self,
        run_started_at: datetime,
    ) -> ServiceResult[RefreshScheduleResult]:
        """Run the full refresh pipeline: resolve, assign tasks, assign free time."""
        resolve_result = TaskResolutionService(self._session, self._clock).resolve_tasks(
            run_started_at
        )
        if not resolve_result.success or resolve_result.value is None:
            return fail(*resolve_result.errors)

        resolved = resolve_result.value
        assign_result = TaskAssignmentService(self._session, self._clock).assign_tasks(
            resolved,
            run_started_at,
        )
        if not assign_result.success:
            return fail(
                *assign_result.errors,
                _value=RefreshScheduleResult(
                    run_started_at=run_started_at,
                    resolved=resolved,
                    assignment=assign_result.value,
                    free_time=None,
                ),
            )

        assignment = assign_result.value
        assert assignment is not None

        free_time_result = FreeTimeAssignmentService(self._session, self._clock).assign_free_time(
            run_started_at
        )
        if not free_time_result.success or free_time_result.value is None:
            return fail(
                *free_time_result.errors,
                _value=RefreshScheduleResult(
                    run_started_at=run_started_at,
                    resolved=resolved,
                    assignment=assignment,
                    free_time=None,
                ),
            )

        return ok(
            RefreshScheduleResult(
                run_started_at=run_started_at,
                resolved=resolved,
                assignment=assignment,
                free_time=free_time_result.value,
            )
        )
