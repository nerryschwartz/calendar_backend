"""Frozen DTOs for orchestration workflow results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from calendar_backend.domain.assignment import AssignmentResult
from calendar_backend.domain.block_assignment import BlockAssignmentResult
from calendar_backend.domain.block_resolution import ResolveBlocksResult
from calendar_backend.domain.free_time import FreeTimeAssignmentResult
from calendar_backend.domain.resolution import ResolveTasksResult


@dataclass(frozen=True)
class RefreshScheduleResult:
    run_started_at: datetime
    resolved_blocks: ResolveBlocksResult | None
    block_assignment: BlockAssignmentResult | None
    resolved: ResolveTasksResult | None
    assignment: AssignmentResult | None
    free_time: FreeTimeAssignmentResult | None
