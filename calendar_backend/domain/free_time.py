"""Pure DTOs and validation for free-time activity management."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import FreeTimeActivityID, PlanID
from calendar_backend.models.free_time import FreeTimeActivity

_DECIMAL_ONE = Decimal("1")


@dataclass(frozen=True)
class FreeTimeActivityDTO:
    free_time_activity_id: FreeTimeActivityID
    name: str
    enabled: bool
    real_fraction: Decimal
    minimum_block_size_minutes: int
    prerequisite_plan_ids: tuple[PlanID, ...]
    created_at: datetime
    updated_at: datetime


def free_time_activity_dto_from_row(activity: FreeTimeActivity) -> FreeTimeActivityDTO:
    prerequisite_plan_ids = tuple(
        sorted(
            (PlanID(prerequisite.source_plan_id) for prerequisite in activity.prerequisites),
            key=str,
        )
    )
    return FreeTimeActivityDTO(
        free_time_activity_id=FreeTimeActivityID(activity.free_time_activity_id),
        name=activity.name,
        enabled=activity.enabled,
        real_fraction=activity.real_fraction,
        minimum_block_size_minutes=activity.minimum_block_size_minutes,
        prerequisite_plan_ids=prerequisite_plan_ids,
        created_at=activity.created_at,
        updated_at=activity.updated_at,
    )


def validate_activity_fields(
    *,
    name: str,
    real_fraction: Decimal,
    minimum_block_size_minutes: int,
    enabled: bool,
) -> ServiceMessage | None:
    if not name.strip():
        return ServiceMessage(
            code=MessageCode.INVALID_CREATE_PAYLOAD,
            message="Free-time activity name must be non-empty",
            details={},
        )

    if minimum_block_size_minutes < 0:
        return ServiceMessage(
            code=MessageCode.INVALID_MINIMUM_BLOCK_SIZE,
            message="minimum_block_size_minutes must be non-negative",
            details={"minimum_block_size_minutes": str(minimum_block_size_minutes)},
        )

    if real_fraction < 0:
        return ServiceMessage(
            code=MessageCode.INVALID_FREE_TIME_FRACTIONS,
            message="real_fraction must be non-negative",
            details={"real_fraction": str(real_fraction)},
        )

    if enabled and real_fraction <= 0:
        return ServiceMessage(
            code=MessageCode.INVALID_FREE_TIME_FRACTIONS,
            message="Enabled free-time activities must have a positive real_fraction",
            details={"real_fraction": str(real_fraction)},
        )

    return None


def validate_enabled_fractions_sum_to_one(
    activities: tuple[FreeTimeActivity, ...],
) -> ServiceMessage | None:
    total = Decimal("0")
    contributing: list[str] = []
    for activity in activities:
        if not activity.enabled or activity.real_fraction <= 0:
            continue
        total += activity.real_fraction
        contributing.append(str(activity.free_time_activity_id))

    if total == _DECIMAL_ONE:
        return None

    return ServiceMessage(
        code=MessageCode.INVALID_FREE_TIME_FRACTIONS,
        message="Enabled positive free-time fractions must sum to 1",
        details={
            "sum": str(total),
            "activity_ids": ",".join(sorted(contributing)),
        },
    )
