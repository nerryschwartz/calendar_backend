"""Application settings bootstrap, read, and update."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from calendar_backend.db.session import transaction
from calendar_backend.domain.dtos import AppSettingsDTO, app_settings_dto_from_row
from calendar_backend.domain.enums import FreeTimeWeekStartDay
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock
from calendar_backend.models.settings import AppSettings

DEFAULT_LOCAL_TIMEZONE = "UTC"
DEFAULT_MASTER_HORIZON_DURATION_MINUTES = 1_051_200
DEFAULT_EXACT_SOLVER_TIME_LIMIT_SECONDS = 30
DEFAULT_EXACT_SOLVER_MODEL_SIZE_LIMIT = 1000
DEFAULT_HEURISTIC_ENABLED = True
DEFAULT_FREE_TIME_WEEK_START_DAY = FreeTimeWeekStartDay.MONDAY


class AppSettingsService:
    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def get_settings(self) -> ServiceResult[AppSettingsDTO]:
        with transaction(self._session) as txn:
            row = _load_or_create_settings(txn, self._clock)
            return ok(app_settings_dto_from_row(row))

    def update_settings(
        self,
        *,
        local_timezone: str | None = None,
        master_horizon_duration_minutes: int | None = None,
        exact_solver_time_limit_seconds: int | None = None,
        exact_solver_model_size_limit: int | None = None,
        heuristic_enabled: bool | None = None,
        free_time_week_start_day: FreeTimeWeekStartDay | None = None,
    ) -> ServiceResult[AppSettingsDTO]:
        validation_error = _validate_settings_update(
            local_timezone=local_timezone,
            master_horizon_duration_minutes=master_horizon_duration_minutes,
            exact_solver_time_limit_seconds=exact_solver_time_limit_seconds,
            exact_solver_model_size_limit=exact_solver_model_size_limit,
        )
        if validation_error is not None:
            return fail(validation_error)

        with transaction(self._session) as txn:
            row = _load_or_create_settings(txn, self._clock)
            updates = {
                "local_timezone": local_timezone,
                "master_horizon_duration_minutes": master_horizon_duration_minutes,
                "exact_solver_time_limit_seconds": exact_solver_time_limit_seconds,
                "exact_solver_model_size_limit": exact_solver_model_size_limit,
                "heuristic_enabled": heuristic_enabled,
                "free_time_week_start_day": free_time_week_start_day,
            }
            changed = False
            for field_name, value in updates.items():
                if value is not None:
                    setattr(row, field_name, value)
                    changed = True

            if changed:
                row.updated_at = self._clock.now_utc()

            txn.flush()
            return ok(app_settings_dto_from_row(row))


def _load_or_create_settings(session: Session, clock: Clock) -> AppSettings:
    row = session.get(AppSettings, 1)
    if row is not None:
        return row

    now = clock.now_utc()
    row = AppSettings(
        singleton_id=1,
        local_timezone=DEFAULT_LOCAL_TIMEZONE,
        master_horizon_duration_minutes=DEFAULT_MASTER_HORIZON_DURATION_MINUTES,
        exact_solver_time_limit_seconds=DEFAULT_EXACT_SOLVER_TIME_LIMIT_SECONDS,
        exact_solver_model_size_limit=DEFAULT_EXACT_SOLVER_MODEL_SIZE_LIMIT,
        heuristic_enabled=DEFAULT_HEURISTIC_ENABLED,
        free_time_week_start_day=DEFAULT_FREE_TIME_WEEK_START_DAY,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


def _validate_settings_update(
    *,
    local_timezone: str | None,
    master_horizon_duration_minutes: int | None,
    exact_solver_time_limit_seconds: int | None,
    exact_solver_model_size_limit: int | None,
) -> ServiceMessage | None:
    if local_timezone is not None:
        try:
            ZoneInfo(local_timezone)
        except ZoneInfoNotFoundError:
            return ServiceMessage(
                code=MessageCode.INVALID_TIME_WINDOW,
                message="Invalid local_timezone",
                details={"local_timezone": local_timezone},
            )

    for field_name, value in (
        ("master_horizon_duration_minutes", master_horizon_duration_minutes),
        ("exact_solver_time_limit_seconds", exact_solver_time_limit_seconds),
        ("exact_solver_model_size_limit", exact_solver_model_size_limit),
    ):
        if value is not None and value <= 0:
            return ServiceMessage(
                code=MessageCode.INVALID_DURATION,
                message=f"{field_name} must be positive",
                details={field_name: str(value)},
            )

    return None
