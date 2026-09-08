"""Apply ordered free-time edits in one transaction, validating the final fractions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from calendar_backend.db.session import transaction
from calendar_backend.domain.errors import MessageCode, ServiceMessage, ServiceTransactionAborted
from calendar_backend.domain.free_time import (
    free_time_activity_dto_from_row,
    serialize_activity_block_families,
    validate_activity_block_families_for_write,
    validate_activity_fields,
    validate_enabled_fractions_sum_to_one,
)
from calendar_backend.domain.free_time_draft import (
    CreateActivityEdit,
    FreeTimeDraftEdit,
    FreeTimeDraftResult,
    PrerequisiteEdit,
)
from calendar_backend.domain.ids import FreeTimeActivityID, FreeTimeActivityPrerequisiteID, new_id
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.free_time import FreeTimeActivity, FreeTimeActivityPrerequisite
from calendar_backend.models.plans import Plan
from calendar_backend.services.free_time_activity import delete_activity_rows, load_all_activities


class FreeTimeDraftService:
    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def apply_edits(
        self, edits: tuple[FreeTimeDraftEdit, ...]
    ) -> ServiceResult[FreeTimeDraftResult]:
        refs: dict[str, UUID] = {}
        try:
            with transaction(self._session) as txn:
                now = self._clock.now_utc()
                for index, edit in enumerate(edits):
                    try:
                        _apply_edit(txn, edit, refs, now)
                    except ServiceTransactionAborted as exc:
                        raise ServiceTransactionAborted(
                            tuple(
                                ServiceMessage(
                                    error.code,
                                    error.message,
                                    {**error.details, "edit_index": str(index)},
                                )
                                for error in exc.errors
                            )
                        ) from exc
                activities = load_all_activities(txn)
                for activity in activities:
                    error = validate_activity_fields(
                        name=activity.name,
                        real_fraction=activity.real_fraction,
                        minimum_block_size_minutes=activity.minimum_block_size_minutes,
                        enabled=activity.enabled,
                    )
                    if error is not None:
                        raise ServiceTransactionAborted((error,))
                error = validate_enabled_fractions_sum_to_one(activities)
                if error is not None:
                    raise ServiceTransactionAborted((error,))
                return ok(
                    FreeTimeDraftResult(
                        len(edits),
                        tuple(free_time_activity_dto_from_row(row) for row in activities),
                    )
                )
        except ServiceTransactionAborted as exc:
            return fail(*exc.errors, metadata={"applied_count": 0})


def _apply_edit(  # noqa: PLR0912
    txn: Session, edit: FreeTimeDraftEdit, refs: dict[str, UUID], now: datetime
) -> None:
    if isinstance(edit, CreateActivityEdit):
        try:
            UUID(edit.draft_ref)
        except ValueError:
            is_uuid = False
        else:
            is_uuid = True
        if is_uuid or not edit.draft_ref.strip() or edit.draft_ref in refs:
            raise ServiceTransactionAborted(
                (
                    ServiceMessage(
                        MessageCode.INVALID_FREE_TIME_DRAFT,
                        "draft_ref must be a unique non-empty reference, not an existing UUID",
                        {"draft_ref": edit.draft_ref},
                    ),
                )
            )
        activity_id = new_id(FreeTimeActivityID)
        refs[edit.draft_ref] = activity_id
        txn.add(
            FreeTimeActivity(
                free_time_activity_id=activity_id,
                name=edit.name,
                real_fraction=edit.real_fraction,
                enabled=edit.enabled,
                minimum_block_size_minutes=edit.minimum_block_size_minutes,
                created_at=now,
                updated_at=now,
            )
        )
        return

    activity_id = refs.get(edit.activity_ref)
    if activity_id is None:
        try:
            activity_id = UUID(edit.activity_ref)
        except ValueError:
            if edit.op == "delete":
                return
    activity = txn.get(FreeTimeActivity, activity_id) if activity_id is not None else None
    if activity is None:
        raise ServiceTransactionAborted(
            (
                ServiceMessage(
                    MessageCode.FREE_TIME_ACTIVITY_NOT_FOUND,
                    "Free-time activity reference not found",
                    {"activity_ref": edit.activity_ref},
                ),
            )
        )
    activity.updated_at = now
    if edit.op == "update":
        for field in ("name", "real_fraction", "minimum_block_size_minutes"):
            value = getattr(edit, field)
            if value is not None:
                setattr(activity, field, value)
    elif edit.op == "set_enabled":
        activity.enabled = edit.enabled
    elif edit.op == "set_block_families":
        error = validate_activity_block_families_for_write(edit.families)
        if error is not None:
            raise ServiceTransactionAborted((error,))
        activity.allowed_block_families = serialize_activity_block_families(edit.families)
    elif edit.op == "clear_block_families":
        activity.allowed_block_families = None
    elif edit.op == "delete":
        delete_activity_rows(txn, activity, now=now)
    else:
        _apply_prerequisite(txn, activity, edit)


def _apply_prerequisite(txn: Session, activity: FreeTimeActivity, edit: PrerequisiteEdit) -> None:
    existing = next(
        (row for row in activity.prerequisites if row.source_plan_id == edit.prerequisite_plan_id),
        None,
    )
    if edit.op == "remove_prerequisite":
        if existing is None:
            raise ServiceTransactionAborted(
                (
                    ServiceMessage(
                        MessageCode.FREE_TIME_PREREQUISITE_NOT_FOUND,
                        "Free-time activity prerequisite not found",
                        {"prerequisite_plan_id": str(edit.prerequisite_plan_id)},
                    ),
                )
            )
        activity.prerequisites.remove(existing)
        txn.delete(existing)
    else:
        if existing is not None:
            raise ServiceTransactionAborted(
                (
                    ServiceMessage(
                        MessageCode.DUPLICATE_FREE_TIME_PREREQUISITE,
                        "Prerequisite already exists for this activity and plan",
                        {"prerequisite_plan_id": str(edit.prerequisite_plan_id)},
                    ),
                )
            )
        if txn.get(Plan, edit.prerequisite_plan_id) is None:
            raise ServiceTransactionAborted(
                (
                    ServiceMessage(
                        MessageCode.PLAN_NOT_FOUND,
                        "Plan not found",
                        {"plan_id": str(edit.prerequisite_plan_id)},
                    ),
                )
            )
        activity.prerequisites.append(
            FreeTimeActivityPrerequisite(
                prerequisite_id=new_id(FreeTimeActivityPrerequisiteID),
                free_time_activity_id=activity.free_time_activity_id,
                source_plan_id=edit.prerequisite_plan_id,
            )
        )
