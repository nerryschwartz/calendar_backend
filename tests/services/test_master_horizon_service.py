from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from calendar_backend.domain.dtos import MasterHorizonDTO
from calendar_backend.domain.enums import ConstraintKind
from calendar_backend.domain.errors import MessageCode
from calendar_backend.models.constraints import TimeConstraintGroup, TimeWindow
from calendar_backend.services.app_settings import (
    DEFAULT_MASTER_HORIZON_DURATION_MINUTES,
    AppSettingsService,
)
from calendar_backend.services.master_horizon import MasterHorizonService
from calendar_backend.services.master_plan import MasterPlanService
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .conftest import FakeClock

RUN_STARTED_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)


def _horizon_group_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(TimeConstraintGroup)
            .where(TimeConstraintGroup.constraint_kind == ConstraintKind.SYSTEM_MASTER_HORIZON)
        )
        or 0
    )


def _horizon_window_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(TimeWindow)
            .join(TimeConstraintGroup)
            .where(TimeConstraintGroup.constraint_kind == ConstraintKind.SYSTEM_MASTER_HORIZON)
        )
        or 0
    )


@pytest.mark.integration
def test_refresh_master_horizon_creates_single_group_and_window(
    service_db_session: Session,
) -> None:
    clock = FakeClock(RUN_STARTED_AT)
    MasterPlanService(service_db_session, clock).ensure_master_exists()
    AppSettingsService(service_db_session, clock).get_settings()

    result = MasterHorizonService(service_db_session, clock).refresh_master_horizon(RUN_STARTED_AT)

    assert result.success and result.value is not None
    assert isinstance(result.value, MasterHorizonDTO)
    assert _horizon_group_count(service_db_session) == 1
    assert _horizon_window_count(service_db_session) == 1


@pytest.mark.integration
def test_refresh_master_horizon_window_bounds(service_db_session: Session) -> None:
    clock = FakeClock(RUN_STARTED_AT)
    MasterPlanService(service_db_session, clock).ensure_master_exists()
    AppSettingsService(service_db_session, clock).get_settings()

    result = MasterHorizonService(service_db_session, clock).refresh_master_horizon(RUN_STARTED_AT)

    assert result.success and result.value is not None
    expected_end = RUN_STARTED_AT + timedelta(minutes=DEFAULT_MASTER_HORIZON_DURATION_MINUTES)
    assert result.value.horizon_start == RUN_STARTED_AT
    assert result.value.horizon_end == expected_end


@pytest.mark.integration
def test_refresh_master_horizon_second_refresh_replaces_bounds(service_db_session: Session) -> None:
    clock = FakeClock(RUN_STARTED_AT)
    MasterPlanService(service_db_session, clock).ensure_master_exists()
    AppSettingsService(service_db_session, clock).get_settings()
    service = MasterHorizonService(service_db_session, clock)

    first = service.refresh_master_horizon(RUN_STARTED_AT)
    second_run = datetime(2026, 6, 8, 14, 30, tzinfo=UTC)
    second = service.refresh_master_horizon(second_run)

    assert first.success and first.value is not None
    assert second.success and second.value is not None
    assert _horizon_group_count(service_db_session) == 1
    assert _horizon_window_count(service_db_session) == 1
    assert second.value.constraint_group_id == first.value.constraint_group_id
    assert second.value.time_window_id != first.value.time_window_id
    assert second.value.horizon_start == second_run
    assert second.value.horizon_end == second_run + timedelta(
        minutes=DEFAULT_MASTER_HORIZON_DURATION_MINUTES
    )


@pytest.mark.integration
def test_refresh_master_horizon_rejects_naive_run_started_at(service_db_session: Session) -> None:
    clock = FakeClock(RUN_STARTED_AT)
    naive = datetime(2026, 6, 7, 10, 0)

    result = MasterHorizonService(service_db_session, clock).refresh_master_horizon(naive)

    assert result.success is False
    assert result.errors
    assert result.errors[0].code == MessageCode.INVALID_TIME_WINDOW
    assert _horizon_group_count(service_db_session) == 0


@pytest.mark.integration
def test_refresh_master_horizon_rejects_non_utc_run_started_at(service_db_session: Session) -> None:
    clock = FakeClock(RUN_STARTED_AT)
    eastern = datetime(2026, 6, 7, 10, 0, tzinfo=ZoneInfo("America/New_York"))

    result = MasterHorizonService(service_db_session, clock).refresh_master_horizon(eastern)

    assert result.success is False
    assert result.errors
    assert result.errors[0].code == MessageCode.INVALID_TIME_WINDOW
    assert _horizon_group_count(service_db_session) == 0


@pytest.mark.integration
def test_refresh_master_horizon_rejects_non_minute_aligned_run_started_at(
    service_db_session: Session,
) -> None:
    clock = FakeClock(RUN_STARTED_AT)
    non_minute = datetime(2026, 6, 7, 10, 0, 30, tzinfo=UTC)

    result = MasterHorizonService(service_db_session, clock).refresh_master_horizon(non_minute)

    assert result.success is False
    assert result.errors
    assert result.errors[0].code == MessageCode.NON_MINUTE_ALIGNED_WINDOW
    assert _horizon_group_count(service_db_session) == 0


@pytest.mark.integration
def test_refresh_master_horizon_end_tracks_updated_duration(service_db_session: Session) -> None:
    clock = FakeClock(RUN_STARTED_AT)
    MasterPlanService(service_db_session, clock).ensure_master_exists()
    AppSettingsService(service_db_session, clock).get_settings()
    horizon_service = MasterHorizonService(service_db_session, clock)

    first = horizon_service.refresh_master_horizon(RUN_STARTED_AT)
    assert first.success

    AppSettingsService(service_db_session, clock).update_settings(
        master_horizon_duration_minutes=90
    )
    second_run = datetime(2026, 6, 9, 9, 0, tzinfo=UTC)
    second = horizon_service.refresh_master_horizon(second_run)

    assert second.success and second.value is not None
    assert second.value.horizon_end == second_run + timedelta(minutes=90)
