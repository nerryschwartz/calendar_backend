from __future__ import annotations

from datetime import UTC, datetime

import pytest
from calendar_backend.domain.dtos import AppSettingsDTO
from calendar_backend.domain.enums import FreeTimeWeekStartDay
from calendar_backend.domain.errors import MessageCode
from calendar_backend.models.settings import AppSettings
from calendar_backend.services.app_settings import (
    DEFAULT_EXACT_SOLVER_MODEL_SIZE_LIMIT,
    DEFAULT_EXACT_SOLVER_TIME_LIMIT_SECONDS,
    DEFAULT_FREE_TIME_WEEK_START_DAY,
    DEFAULT_HEURISTIC_ENABLED,
    DEFAULT_LOCAL_TIMEZONE,
    DEFAULT_MASTER_HORIZON_DURATION_MINUTES,
    AppSettingsService,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .conftest import FakeClock


@pytest.mark.integration
def test_get_settings_bootstraps_defaults(service_db_session: Session) -> None:
    clock = FakeClock(datetime(2026, 6, 7, 12, 0, tzinfo=UTC))
    result = AppSettingsService(service_db_session, clock).get_settings()

    assert result.success and result.value is not None
    dto = result.value
    assert dto.local_timezone == DEFAULT_LOCAL_TIMEZONE
    assert dto.master_horizon_duration_minutes == DEFAULT_MASTER_HORIZON_DURATION_MINUTES
    assert dto.exact_solver_time_limit_seconds == DEFAULT_EXACT_SOLVER_TIME_LIMIT_SECONDS
    assert dto.exact_solver_model_size_limit == DEFAULT_EXACT_SOLVER_MODEL_SIZE_LIMIT
    assert dto.heuristic_enabled == DEFAULT_HEURISTIC_ENABLED
    assert dto.free_time_week_start_day == DEFAULT_FREE_TIME_WEEK_START_DAY
    assert dto.updated_at == clock.now_utc()

    row_count = service_db_session.scalar(select(func.count()).select_from(AppSettings))
    assert row_count == 1


@pytest.mark.integration
def test_get_settings_returns_dto_after_bootstrap(service_db_session: Session) -> None:
    clock = FakeClock(datetime(2026, 6, 7, 12, 0, tzinfo=UTC))
    service = AppSettingsService(service_db_session, clock)
    bootstrap = service.get_settings()
    second = service.get_settings()

    assert bootstrap.success and bootstrap.value is not None
    assert second.success and second.value is not None
    assert isinstance(second.value, AppSettingsDTO)
    assert second.value.local_timezone == bootstrap.value.local_timezone
    assert (
        second.value.master_horizon_duration_minutes
        == bootstrap.value.master_horizon_duration_minutes
    )
    assert (
        second.value.exact_solver_time_limit_seconds
        == bootstrap.value.exact_solver_time_limit_seconds
    )
    assert (
        second.value.exact_solver_model_size_limit == bootstrap.value.exact_solver_model_size_limit
    )
    assert second.value.heuristic_enabled == bootstrap.value.heuristic_enabled
    assert second.value.free_time_week_start_day == bootstrap.value.free_time_week_start_day


@pytest.mark.integration
def test_update_settings_updates_each_field(service_db_session: Session) -> None:
    clock = FakeClock(datetime(2026, 6, 7, 12, 0, tzinfo=UTC))
    service = AppSettingsService(service_db_session, clock)
    assert service.get_settings().success

    later = FakeClock(datetime(2026, 6, 8, 12, 0, tzinfo=UTC))
    service = AppSettingsService(service_db_session, later)
    result = service.update_settings(
        local_timezone="America/New_York",
        master_horizon_duration_minutes=120,
        exact_solver_time_limit_seconds=45,
        exact_solver_model_size_limit=500,
        heuristic_enabled=False,
        free_time_week_start_day=FreeTimeWeekStartDay.SUNDAY,
    )

    assert result.success and result.value is not None
    dto = result.value
    assert dto.local_timezone == "America/New_York"
    assert dto.master_horizon_duration_minutes == 120
    assert dto.exact_solver_time_limit_seconds == 45
    assert dto.exact_solver_model_size_limit == 500
    assert dto.heuristic_enabled is False
    assert dto.free_time_week_start_day == FreeTimeWeekStartDay.SUNDAY
    assert dto.updated_at == later.now_utc()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("master_horizon_duration_minutes", 0),
        ("master_horizon_duration_minutes", -1),
        ("exact_solver_time_limit_seconds", 0),
        ("exact_solver_model_size_limit", -5),
    ],
)
def test_update_settings_rejects_non_positive_limits(
    service_db_session: Session,
    field_name: str,
    value: int,
) -> None:
    clock = FakeClock(datetime(2026, 6, 7, 12, 0, tzinfo=UTC))
    service = AppSettingsService(service_db_session, clock)
    assert service.get_settings().success

    match field_name:
        case "master_horizon_duration_minutes":
            result = service.update_settings(master_horizon_duration_minutes=value)
        case "exact_solver_time_limit_seconds":
            result = service.update_settings(exact_solver_time_limit_seconds=value)
        case "exact_solver_model_size_limit":
            result = service.update_settings(exact_solver_model_size_limit=value)
        case _:
            raise AssertionError(f"unexpected field: {field_name}")

    assert result.success is False
    assert result.errors
    assert result.errors[0].code == MessageCode.INVALID_DURATION


@pytest.mark.integration
def test_update_settings_rejects_invalid_timezone(service_db_session: Session) -> None:
    clock = FakeClock(datetime(2026, 6, 7, 12, 0, tzinfo=UTC))
    service = AppSettingsService(service_db_session, clock)
    assert service.get_settings().success

    result = service.update_settings(local_timezone="Not/A_Real_Zone")

    assert result.success is False
    assert result.errors
    assert result.errors[0].code == MessageCode.INVALID_TIME_WINDOW


@pytest.mark.integration
def test_update_settings_advances_updated_at_with_fake_clock(service_db_session: Session) -> None:
    bootstrap_clock = FakeClock(datetime(2026, 6, 7, 12, 0, tzinfo=UTC))
    service = AppSettingsService(service_db_session, bootstrap_clock)
    bootstrap = service.get_settings()
    assert bootstrap.success and bootstrap.value is not None
    assert bootstrap.value.updated_at == bootstrap_clock.now_utc()

    update_clock = FakeClock(datetime(2026, 6, 9, 8, 30, tzinfo=UTC))
    service = AppSettingsService(service_db_session, update_clock)
    updated = service.update_settings(heuristic_enabled=False)

    assert updated.success and updated.value is not None
    assert updated.value.updated_at == update_clock.now_utc()
    assert updated.value.updated_at > bootstrap.value.updated_at
