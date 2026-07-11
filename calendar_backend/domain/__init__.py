from __future__ import annotations

from typing import TYPE_CHECKING

from calendar_backend.domain.enums import (
    CalendarEntryType,
    CalendarRunStatus,
    CloneStatus,
    ConstraintKind,
    FreeTimeWeekStartDay,
    LastFailureReason,
    PlanKind,
    RepeatMode,
    RepetitionTimestampField,
    SolverStatus,
)
from calendar_backend.domain.errors import (
    MessageCode,
    ServiceMessage,
    ServiceTransactionAborted,
    WrongPlanTypeError,
)
from calendar_backend.domain.ids import (
    CalendarEntryID,
    CalendarRunID,
    FreeTimeActivityID,
    FreeTimeActivityPrerequisiteID,
    GoalChildChainID,
    GoalChildChainItemID,
    PlanID,
    RepetitionInstanceID,
    TimeConstraintGroupID,
    TimeWindowID,
    new_id,
)
from calendar_backend.domain.resolution import (
    ChainPathStep,
    ConstraintSource,
    ResolutionIndexes,
    ResolvedPrecedenceConstraint,
    ResolvedTask,
    ResolveTasksResult,
    build_resolution_indexes,
    constraint_errors_for_plan,
    resolve_tasks_from_graph,
)
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import (
    Clock,
    SystemClock,
    TimeWindow,
    is_minute_aligned,
    require_utc,
    truncate_to_minute,
    validate_time_window,
)

if TYPE_CHECKING:
    from calendar_backend.domain.orchestration import RefreshScheduleResult


def __getattr__(name: str) -> object:
    if name == "RefreshScheduleResult":
        from calendar_backend.domain.orchestration import (  # noqa: PLC0415
            RefreshScheduleResult,
        )

        return RefreshScheduleResult
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CalendarEntryID",
    "CalendarEntryType",
    "CalendarRunID",
    "CalendarRunStatus",
    "ChainPathStep",
    "Clock",
    "CloneStatus",
    "ConstraintKind",
    "ConstraintSource",
    "FreeTimeActivityID",
    "FreeTimeActivityPrerequisiteID",
    "FreeTimeWeekStartDay",
    "GoalChildChainID",
    "GoalChildChainItemID",
    "LastFailureReason",
    "MessageCode",
    "PlanID",
    "PlanKind",
    "RefreshScheduleResult",
    "RepeatMode",
    "RepetitionInstanceID",
    "RepetitionTimestampField",
    "ResolutionIndexes",
    "ResolveTasksResult",
    "ResolvedPrecedenceConstraint",
    "ResolvedTask",
    "ServiceMessage",
    "ServiceResult",
    "ServiceTransactionAborted",
    "SolverStatus",
    "SystemClock",
    "TimeConstraintGroupID",
    "TimeWindow",
    "TimeWindowID",
    "WrongPlanTypeError",
    "build_resolution_indexes",
    "constraint_errors_for_plan",
    "fail",
    "is_minute_aligned",
    "new_id",
    "ok",
    "require_utc",
    "resolve_tasks_from_graph",
    "truncate_to_minute",
    "validate_time_window",
]
