"""Orchestration service: compose resolution, task assignment, and free-time assignment."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from calendar_backend.domain.orchestration import RefreshScheduleResult
from calendar_backend.domain.results import ServiceResult
from calendar_backend.domain.time import Clock, SystemClock


class OrchestrationService:
    """Compose refresh_schedule from task resolution, assignment, and free-time services."""

    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def refresh_schedule(
        self,
        run_started_at: datetime,
    ) -> ServiceResult[RefreshScheduleResult]:
        """Run the full refresh pipeline: resolve, assign tasks, assign free time."""
        raise NotImplementedError
