"""Repetition plan subtype self-edit service."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy.orm import Session

from calendar_backend.db.session import transaction
from calendar_backend.domain.dtos import RepetitionPlanDTO, repetition_plan_dto_from_rows
from calendar_backend.domain.enums import PlanKind, RepeatMode
from calendar_backend.domain.errors import ServiceMessage
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.repetitions import (
    RepetitionSettingsState,
    validate_repetition_settings_update,
)
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.services.plan_tree import load_plan_with_subtype


class _UnsetType:
    __slots__ = ()


_UNSET = _UnsetType()


class RepetitionService:
    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def update_settings(
        self,
        repetition_plan_id: PlanID,
        *,
        repeat_mode: RepeatMode | None = None,
        start_time: datetime | None = None,
        repeat_interval_minutes: int | None = None,
        manual_count: int | None | _UnsetType = _UNSET,
        end_time: datetime | None | _UnsetType = _UNSET,
        default_instance_critical: bool | None = None,
    ) -> ServiceResult[RepetitionPlanDTO]:
        with transaction(self._session) as txn:
            loaded = load_plan_with_subtype(
                txn, repetition_plan_id, expected_kind=PlanKind.REPETITION
            )
            if isinstance(loaded, ServiceMessage):
                return fail(loaded)
            plan, repetition_plan = loaded

            current = RepetitionSettingsState(
                repeat_mode=repetition_plan.repeat_mode,
                start_time=repetition_plan.start_time,
                repeat_interval_minutes=repetition_plan.repeat_interval_minutes,
                manual_count=repetition_plan.manual_count,
                end_time=repetition_plan.end_time,
                default_instance_critical=repetition_plan.default_instance_critical,
                generated_at=repetition_plan.generated_at,
            )
            if manual_count is _UNSET:
                merged_manual_count = current.manual_count
            else:
                merged_manual_count = cast(int | None, manual_count)
            if end_time is _UNSET:
                merged_end_time = current.end_time
            else:
                merged_end_time = cast(datetime | None, end_time)
            proposed = RepetitionSettingsState(
                repeat_mode=repeat_mode if repeat_mode is not None else current.repeat_mode,
                start_time=start_time if start_time is not None else current.start_time,
                repeat_interval_minutes=(
                    repeat_interval_minutes
                    if repeat_interval_minutes is not None
                    else current.repeat_interval_minutes
                ),
                manual_count=merged_manual_count,
                end_time=merged_end_time,
                default_instance_critical=(
                    default_instance_critical
                    if default_instance_critical is not None
                    else current.default_instance_critical
                ),
                generated_at=current.generated_at,
            )

            validation_error = validate_repetition_settings_update(current, proposed)
            if validation_error is not None:
                return fail(validation_error)

            now = self._clock.now_utc()
            repetition_plan.repeat_mode = proposed.repeat_mode
            repetition_plan.start_time = proposed.start_time
            repetition_plan.repeat_interval_minutes = proposed.repeat_interval_minutes
            repetition_plan.manual_count = proposed.manual_count
            repetition_plan.end_time = proposed.end_time
            repetition_plan.default_instance_critical = proposed.default_instance_critical
            plan.updated_at = now
            txn.flush()
            return ok(repetition_plan_dto_from_rows(plan, repetition_plan))
