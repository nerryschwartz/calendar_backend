from __future__ import annotations

from enum import StrEnum


class PlanKind(StrEnum):
    GOAL = "GOAL"
    TASK = "TASK"
    REPETITION = "REPETITION"


class CloneStatus(StrEnum):
    TEMPLATE = "TEMPLATE"
    LINKED = "LINKED"
    DETACHED = "DETACHED"
    NOT_CLONED = "NOT_CLONED"


class RepeatMode(StrEnum):
    MANUAL_COUNT = "MANUAL_COUNT"
    DATE_RANGE = "DATE_RANGE"


class RepetitionTimestampField(StrEnum):
    START_TIME = "START_TIME"
    END_TIME = "END_TIME"


class ConstraintKind(StrEnum):
    USER = "USER"
    SYSTEM_REPETITION_WINDOW = "SYSTEM_REPETITION_WINDOW"
    SYSTEM_MASTER_HORIZON = "SYSTEM_MASTER_HORIZON"


class CalendarEntryType(StrEnum):
    TASK = "TASK"
    FREE_TIME = "FREE_TIME"


class CalendarRunStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class SolverStatus(StrEnum):
    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"


class LastFailureReason(StrEnum):
    ASSIGNMENT_FAILED = "ASSIGNMENT_FAILED"
    ASSIGNMENT_PRECONDITION_FAILED = "ASSIGNMENT_PRECONDITION_FAILED"
    FREE_TIME_ASSIGNMENT_FAILED = "FREE_TIME_ASSIGNMENT_FAILED"


class FreeTimeWeekStartDay(StrEnum):
    MONDAY = "MONDAY"
    TUESDAY = "TUESDAY"
    WEDNESDAY = "WEDNESDAY"
    THURSDAY = "THURSDAY"
    FRIDAY = "FRIDAY"
    SATURDAY = "SATURDAY"
    SUNDAY = "SUNDAY"
