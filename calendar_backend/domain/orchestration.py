"""Frozen DTOs for orchestration workflow results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from calendar_backend.domain.assignment import AssignmentResult
from calendar_backend.domain.free_time import FreeTimeAssignmentResult
from calendar_backend.domain.resolution import ResolveTasksResult


@dataclass(frozen=True)
class RefreshScheduleResult:
    run_started_at: datetime
    resolved: ResolveTasksResult | None
    assignment: AssignmentResult | None
    free_time: FreeTimeAssignmentResult | None
