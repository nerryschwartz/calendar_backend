"""Master plan system horizon constraint refresh."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from calendar_backend.db.session import transaction
from calendar_backend.domain.dtos import MasterHorizonDTO
from calendar_backend.domain.enums import ConstraintKind
from calendar_backend.domain.errors import MessageCode, ServiceMessage, ServiceTransactionAborted
from calendar_backend.domain.ids import (
    PlanID,
    TimeConstraintGroupID,
    TimeWindowID,
    new_id,
)
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock, is_minute_aligned, require_utc
from calendar_backend.models.constraints import TimeConstraintGroup, TimeWindow
from calendar_backend.models.plans import Plan
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.master_plan import MasterPlanService


class MasterHorizonService:
    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def refresh_master_horizon(self, run_started_at: datetime) -> ServiceResult[MasterHorizonDTO]:
        validation_error = validate_run_started_at(run_started_at)
        if validation_error is not None:
            return fail(validation_error)

        try:
            with transaction(self._session) as txn:
                master_result = MasterPlanService(txn, self._clock).ensure_master_exists()
                if not master_result.success or master_result.value is None:
                    raise ServiceTransactionAborted(master_result.errors)

                settings_result = AppSettingsService(txn, self._clock).get_settings()
                if not settings_result.success or settings_result.value is None:
                    raise ServiceTransactionAborted(settings_result.errors)

                master_plan_id = master_result.value.plan_id
                duration_minutes = settings_result.value.master_horizon_duration_minutes
                horizon_end = run_started_at + timedelta(minutes=duration_minutes)

                group, window = _upsert_master_horizon_window(
                    txn,
                    master_plan_id=master_plan_id,
                    horizon_start=run_started_at,
                    horizon_end=horizon_end,
                )
                txn.flush()
                return ok(
                    MasterHorizonDTO(
                        horizon_start=window.start_time,
                        horizon_end=window.end_time,
                        constraint_group_id=TimeConstraintGroupID(group.time_constraint_group_id),
                        time_window_id=TimeWindowID(window.time_window_id),
                    )
                )
        except ServiceTransactionAborted as exc:
            return fail(*exc.errors)


def validate_run_started_at(run_started_at: datetime) -> ServiceMessage | None:
    try:
        require_utc(run_started_at)
    except ValueError:
        return ServiceMessage(
            code=MessageCode.INVALID_TIME_WINDOW,
            message="run_started_at must be timezone-aware UTC",
            details={"run_started_at": run_started_at.isoformat()},
        )

    if not is_minute_aligned(run_started_at):
        return ServiceMessage(
            code=MessageCode.NON_MINUTE_ALIGNED_WINDOW,
            message="run_started_at must be minute-aligned",
            details={"run_started_at": run_started_at.isoformat()},
        )

    return None


def get_master_horizon_end(session: Session) -> datetime | None:
    master = session.scalar(select(Plan).where(Plan.is_master))
    if master is None:
        return None
    group = session.scalar(
        select(TimeConstraintGroup)
        .where(TimeConstraintGroup.plan_id == master.plan_id)
        .where(TimeConstraintGroup.constraint_kind == ConstraintKind.SYSTEM_MASTER_HORIZON)
    )
    if group is None:
        return None
    window = session.scalar(
        select(TimeWindow).where(TimeWindow.group_id == group.time_constraint_group_id)
    )
    if window is None:
        return None
    return window.end_time


def _upsert_master_horizon_window(
    session: Session,
    *,
    master_plan_id: PlanID,
    horizon_start: datetime,
    horizon_end: datetime,
) -> tuple[TimeConstraintGroup, TimeWindow]:
    group = session.scalar(
        select(TimeConstraintGroup)
        .where(TimeConstraintGroup.plan_id == master_plan_id)
        .where(TimeConstraintGroup.constraint_kind == ConstraintKind.SYSTEM_MASTER_HORIZON)
    )

    if group is None:
        group = TimeConstraintGroup(
            time_constraint_group_id=new_id(TimeConstraintGroupID),
            plan_id=master_plan_id,
            constraint_kind=ConstraintKind.SYSTEM_MASTER_HORIZON,
        )
        session.add(group)

    session.execute(delete(TimeWindow).where(TimeWindow.group_id == group.time_constraint_group_id))

    window = TimeWindow(
        time_window_id=new_id(TimeWindowID),
        group_id=group.time_constraint_group_id,
        start_time=horizon_start,
        end_time=horizon_end,
    )
    session.add(window)
    return group, window
