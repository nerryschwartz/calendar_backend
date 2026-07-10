"""Free-time activity CRUD and prerequisite management."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from calendar_backend.db.session import transaction
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.free_time import (
    FreeTimeActivityDTO,
    free_time_activity_dto_from_row,
    validate_activity_fields,
    validate_enabled_fractions_sum_to_one,
)
from calendar_backend.domain.ids import (
    FreeTimeActivityID,
    FreeTimeActivityPrerequisiteID,
    PlanID,
    new_id,
)
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.free_time import FreeTimeActivity, FreeTimeActivityPrerequisite
from calendar_backend.models.plans import Plan


class FreeTimeActivityService:
    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def create_activity(
        self,
        name: str,
        real_fraction: Decimal,
        minimum_block_size_minutes: int,
        *,
        enabled: bool = True,
    ) -> ServiceResult[FreeTimeActivityDTO]:
        validation_error = validate_activity_fields(
            name=name,
            real_fraction=real_fraction,
            minimum_block_size_minutes=minimum_block_size_minutes,
            enabled=enabled,
        )
        if validation_error is not None:
            return fail(validation_error)

        with transaction(self._session) as txn:
            now = self._clock.now_utc()
            activity = FreeTimeActivity(
                free_time_activity_id=new_id(FreeTimeActivityID),
                name=name,
                enabled=enabled,
                real_fraction=real_fraction,
                minimum_block_size_minutes=minimum_block_size_minutes,
                created_at=now,
                updated_at=now,
            )
            fraction_error = _validate_global_enabled_fractions(txn, prospective=(activity,))
            if fraction_error is not None:
                return fail(fraction_error)

            txn.add(activity)
            txn.flush()
            loaded = _load_activity(txn, FreeTimeActivityID(activity.free_time_activity_id))
            assert loaded is not None
            return ok(free_time_activity_dto_from_row(loaded))

    def update_activity(
        self,
        activity_id: FreeTimeActivityID,
        *,
        name: str | None = None,
        real_fraction: Decimal | None = None,
        minimum_block_size_minutes: int | None = None,
    ) -> ServiceResult[FreeTimeActivityDTO]:
        with transaction(self._session) as txn:
            loaded = _load_activity(txn, activity_id)
            if loaded is None:
                return fail(_activity_not_found(activity_id))

            next_name = name if name is not None else loaded.name
            next_fraction = real_fraction if real_fraction is not None else loaded.real_fraction
            next_minimum_block = (
                minimum_block_size_minutes
                if minimum_block_size_minutes is not None
                else loaded.minimum_block_size_minutes
            )
            validation_error = validate_activity_fields(
                name=next_name,
                real_fraction=next_fraction,
                minimum_block_size_minutes=next_minimum_block,
                enabled=loaded.enabled,
            )
            if validation_error is not None:
                return fail(validation_error)

            loaded.name = next_name
            loaded.real_fraction = next_fraction
            loaded.minimum_block_size_minutes = next_minimum_block
            fraction_error = _validate_global_enabled_fractions(txn)
            if fraction_error is not None:
                return fail(fraction_error)

            loaded.updated_at = self._clock.now_utc()
            txn.flush()
            return ok(free_time_activity_dto_from_row(loaded))

    def set_enabled(
        self,
        activity_id: FreeTimeActivityID,
        enabled: bool,
    ) -> ServiceResult[FreeTimeActivityDTO]:
        with transaction(self._session) as txn:
            loaded = _load_activity(txn, activity_id)
            if loaded is None:
                return fail(_activity_not_found(activity_id))

            validation_error = validate_activity_fields(
                name=loaded.name,
                real_fraction=loaded.real_fraction,
                minimum_block_size_minutes=loaded.minimum_block_size_minutes,
                enabled=enabled,
            )
            if validation_error is not None:
                return fail(validation_error)

            loaded.enabled = enabled
            fraction_error = _validate_global_enabled_fractions(txn)
            if fraction_error is not None:
                return fail(fraction_error)

            loaded.updated_at = self._clock.now_utc()
            txn.flush()
            return ok(free_time_activity_dto_from_row(loaded))

    def add_prerequisite(
        self,
        activity_id: FreeTimeActivityID,
        source_plan_id: PlanID,
    ) -> ServiceResult[FreeTimeActivityDTO]:
        with transaction(self._session) as txn:
            loaded = _load_activity(txn, activity_id)
            if loaded is None:
                return fail(_activity_not_found(activity_id))

            if txn.get(Plan, source_plan_id) is None:
                return fail(
                    ServiceMessage(
                        code=MessageCode.PLAN_NOT_FOUND,
                        message="Plan not found",
                        details={"plan_id": str(source_plan_id)},
                    )
                )

            for prerequisite in loaded.prerequisites:
                if prerequisite.source_plan_id == source_plan_id:
                    return fail(
                        ServiceMessage(
                            code=MessageCode.DUPLICATE_FREE_TIME_PREREQUISITE,
                            message="Prerequisite already exists for this activity and plan",
                            details={
                                "free_time_activity_id": str(activity_id),
                                "source_plan_id": str(source_plan_id),
                            },
                        )
                    )

            txn.add(
                FreeTimeActivityPrerequisite(
                    prerequisite_id=new_id(FreeTimeActivityPrerequisiteID),
                    free_time_activity_id=activity_id,
                    source_plan_id=source_plan_id,
                )
            )
            loaded.updated_at = self._clock.now_utc()
            txn.flush()
            reloaded = _load_activity(txn, activity_id)
            assert reloaded is not None
            return ok(free_time_activity_dto_from_row(reloaded))

    def remove_prerequisite(
        self,
        activity_id: FreeTimeActivityID,
        prerequisite_id: FreeTimeActivityPrerequisiteID,
    ) -> ServiceResult[FreeTimeActivityDTO]:
        with transaction(self._session) as txn:
            loaded = _load_activity(txn, activity_id)
            if loaded is None:
                return fail(_activity_not_found(activity_id))

            prerequisite = txn.get(FreeTimeActivityPrerequisite, prerequisite_id)
            if prerequisite is None or prerequisite.free_time_activity_id != activity_id:
                return fail(
                    ServiceMessage(
                        code=MessageCode.FREE_TIME_PREREQUISITE_NOT_FOUND,
                        message="Free-time activity prerequisite not found",
                        details={
                            "free_time_activity_id": str(activity_id),
                            "prerequisite_id": str(prerequisite_id),
                        },
                    )
                )

            txn.delete(prerequisite)
            loaded.updated_at = self._clock.now_utc()
            txn.flush()
            reloaded = _load_activity(txn, activity_id)
            assert reloaded is not None
            return ok(free_time_activity_dto_from_row(reloaded))

    def get_activity(
        self,
        activity_id: FreeTimeActivityID,
    ) -> ServiceResult[FreeTimeActivityDTO]:
        with transaction(self._session) as txn:
            loaded = _load_activity(txn, activity_id)
            if loaded is None:
                return fail(_activity_not_found(activity_id))
            return ok(free_time_activity_dto_from_row(loaded))

    def list_activities(self) -> ServiceResult[tuple[FreeTimeActivityDTO, ...]]:
        with transaction(self._session) as txn:
            activities = _load_all_activities(txn)
            return ok(
                tuple(
                    free_time_activity_dto_from_row(activity)
                    for activity in sorted(
                        activities, key=lambda row: str(row.free_time_activity_id)
                    )
                )
            )


def cleanup_orphaned_activities_after_plan_delete(
    txn: Session,
    affected_plan_ids: tuple[PlanID, ...],
    *,
    updated_at: datetime,
) -> None:
    """Disable or delete activities that lost all prerequisites during plan delete."""
    if not affected_plan_ids:
        return

    candidate_activity_ids = tuple(
        txn.scalars(
            select(FreeTimeActivityPrerequisite.free_time_activity_id)
            .where(FreeTimeActivityPrerequisite.source_plan_id.in_(affected_plan_ids))
            .distinct()
        ).all()
    )
    if not candidate_activity_ids:
        return

    for activity_id in candidate_activity_ids:
        remaining_prerequisite_count = (
            txn.scalar(
                select(func.count())
                .select_from(FreeTimeActivityPrerequisite)
                .where(FreeTimeActivityPrerequisite.free_time_activity_id == activity_id)
            )
            or 0
        )
        if remaining_prerequisite_count > 0:
            continue

        calendar_reference_count = (
            txn.scalar(
                select(func.count())
                .select_from(CalendarEntry)
                .where(CalendarEntry.source_free_time_activity_id == activity_id)
            )
            or 0
        )
        activity = txn.get(FreeTimeActivity, activity_id)
        if activity is None:
            continue

        if calendar_reference_count == 0:
            txn.delete(activity)
            continue

        activity.enabled = False
        activity.updated_at = updated_at


def _load_activity(txn: Session, activity_id: FreeTimeActivityID) -> FreeTimeActivity | None:
    return txn.scalar(
        select(FreeTimeActivity)
        .where(FreeTimeActivity.free_time_activity_id == activity_id)
        .options(selectinload(FreeTimeActivity.prerequisites))
    )


def _load_all_activities(txn: Session) -> tuple[FreeTimeActivity, ...]:
    return tuple(
        txn.scalars(
            select(FreeTimeActivity)
            .options(selectinload(FreeTimeActivity.prerequisites))
            .order_by(FreeTimeActivity.name, FreeTimeActivity.free_time_activity_id)
        ).all()
    )


def _validate_global_enabled_fractions(
    txn: Session,
    *,
    prospective: tuple[FreeTimeActivity, ...] = (),
) -> ServiceMessage | None:
    existing = list(_load_all_activities(txn))
    existing_ids = {activity.free_time_activity_id for activity in existing}
    for activity in prospective:
        if activity.free_time_activity_id not in existing_ids:
            existing.append(activity)
    return validate_enabled_fractions_sum_to_one(tuple(existing))


def _activity_not_found(activity_id: FreeTimeActivityID) -> ServiceMessage:
    return ServiceMessage(
        code=MessageCode.FREE_TIME_ACTIVITY_NOT_FOUND,
        message="Free-time activity not found",
        details={"free_time_activity_id": str(activity_id)},
    )
