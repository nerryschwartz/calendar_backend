from __future__ import annotations

import uuid
from datetime import UTC, datetime
from uuid import UUID

import pytest
from calendar_backend.domain.enums import ConstraintKind
from calendar_backend.domain.errors import MessageCode
from calendar_backend.domain.ids import PlanID, TimeConstraintGroupID, TimeWindowID
from calendar_backend.domain.time import TimeWindow
from calendar_backend.models.constraints import TimeConstraintGroup
from calendar_backend.models.constraints import TimeWindow as TimeWindowRow
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.master_horizon import MasterHorizonService
from calendar_backend.services.master_plan import MasterPlanService
from calendar_backend.services.time_constraint import TimeConstraintService
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .conftest import FakeClock

RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)


def _utc(y: int, m: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def _window(start: datetime, end: datetime) -> TimeWindow:
    return TimeWindow(start_time=start, end_time=end)


def _user_group_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(TimeConstraintGroup)
            .where(TimeConstraintGroup.constraint_kind == ConstraintKind.USER)
        )
        or 0
    )


@pytest.fixture
def master_plan_id(service_db_session: Session) -> PlanID:
    clock = FakeClock(RUN_AT)
    MasterPlanService(service_db_session, clock).ensure_master_exists()
    AppSettingsService(service_db_session, clock).get_settings()
    result = MasterPlanService(service_db_session, clock).ensure_master_exists()
    assert result.success and result.value is not None
    return result.value.plan_id


def _user_window_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(TimeWindowRow)) or 0


@pytest.mark.integration
def test_add_user_group_merges_overlapping_windows_on_create(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    clock = FakeClock(RUN_AT)
    service = TimeConstraintService(service_db_session, clock)

    result = service.add_user_group(
        master_plan_id,
        (
            _window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),
            _window(_utc(2026, 6, 7, 11, 0), _utc(2026, 6, 7, 14, 0)),
        ),
    )

    assert result.success and result.value is not None
    assert len(result.value.windows) == 1
    assert result.value.windows[0].start_time.replace(tzinfo=UTC) == _utc(2026, 6, 7, 9, 0)
    assert result.value.windows[0].end_time.replace(tzinfo=UTC) == _utc(2026, 6, 7, 14, 0)


@pytest.mark.integration
def test_update_user_group_replaces_and_merges_windows(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    clock = FakeClock(RUN_AT)
    service = TimeConstraintService(service_db_session, clock)
    created = service.add_user_group(
        master_plan_id,
        (_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),),
    )
    assert created.success and created.value is not None

    result = service.update_user_group(
        created.value.constraint_group_id,
        (
            _window(_utc(2026, 6, 7, 13, 0), _utc(2026, 6, 7, 15, 0)),
            _window(_utc(2026, 6, 7, 15, 0), _utc(2026, 6, 7, 17, 0)),
        ),
    )

    assert result.success and result.value is not None
    assert len(result.value.windows) == 1
    assert result.value.windows[0].start_time.replace(tzinfo=UTC) == _utc(2026, 6, 7, 13, 0)
    assert result.value.windows[0].end_time.replace(tzinfo=UTC) == _utc(2026, 6, 7, 17, 0)


@pytest.mark.integration
def test_remove_user_group_deletes_group_and_windows(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    clock = FakeClock(RUN_AT)
    service = TimeConstraintService(service_db_session, clock)
    created = service.add_user_group(
        master_plan_id,
        (_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),),
    )
    assert created.success and created.value is not None
    group_id = created.value.constraint_group_id

    result = service.remove_user_group(group_id)

    assert result.success
    assert _user_group_count(service_db_session) == 0
    assert service_db_session.get(TimeConstraintGroup, group_id) is None


@pytest.mark.integration
def test_add_user_group_rejects_empty_windows(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    clock = FakeClock(RUN_AT)
    before_groups = _user_group_count(service_db_session)

    result = TimeConstraintService(service_db_session, clock).add_user_group(master_plan_id, ())

    assert not result.success
    assert result.errors[0].code == MessageCode.EMPTY_CONSTRAINT_GROUP
    assert _user_group_count(service_db_session) == before_groups


@pytest.mark.integration
def test_add_user_group_rejects_naive_datetime(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    clock = FakeClock(RUN_AT)
    before_groups = _user_group_count(service_db_session)
    naive = datetime(2026, 6, 7, 9, 0)

    result = TimeConstraintService(service_db_session, clock).add_user_group(
        master_plan_id,
        (_window(naive, _utc(2026, 6, 7, 12, 0)),),
    )

    assert not result.success
    assert result.errors[0].code == MessageCode.INVALID_TIME_WINDOW
    assert _user_group_count(service_db_session) == before_groups


@pytest.mark.integration
def test_add_user_group_rejects_inverted_window(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    clock = FakeClock(RUN_AT)
    before_groups = _user_group_count(service_db_session)

    result = TimeConstraintService(service_db_session, clock).add_user_group(
        master_plan_id,
        (_window(_utc(2026, 6, 7, 12, 0), _utc(2026, 6, 7, 9, 0)),),
    )

    assert not result.success
    assert result.errors[0].code == MessageCode.INVALID_TIME_WINDOW
    assert _user_group_count(service_db_session) == before_groups


@pytest.mark.integration
def test_add_user_group_no_partial_persistence_when_plan_missing(
    service_db_session: Session,
) -> None:
    clock = FakeClock(RUN_AT)
    missing_plan_id = PlanID(uuid.uuid4())
    before_groups = _user_group_count(service_db_session)
    before_windows = _user_window_count(service_db_session)

    result = TimeConstraintService(service_db_session, clock).add_user_group(
        missing_plan_id,
        (_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),),
    )

    assert not result.success
    assert result.errors[0].code == MessageCode.PLAN_NOT_FOUND
    assert _user_group_count(service_db_session) == before_groups
    assert _user_window_count(service_db_session) == before_windows


@pytest.mark.integration
def test_group_mutations_reject_system_horizon_group(service_db_session: Session) -> None:
    clock = FakeClock(RUN_AT)
    MasterPlanService(service_db_session, clock).ensure_master_exists()
    AppSettingsService(service_db_session, clock).get_settings()
    horizon = MasterHorizonService(service_db_session, clock).refresh_master_horizon(RUN_AT)
    assert horizon.success and horizon.value is not None

    service = TimeConstraintService(service_db_session, clock)
    group_id = TimeConstraintGroupID(horizon.value.constraint_group_id)

    update_result = service.update_user_group(
        group_id,
        (_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),),
    )
    remove_result = service.remove_user_group(group_id)

    assert not update_result.success
    assert update_result.errors[0].code == MessageCode.SYSTEM_CONSTRAINT_DIRECT_EDIT_FORBIDDEN
    assert not remove_result.success
    assert remove_result.errors[0].code == MessageCode.SYSTEM_CONSTRAINT_DIRECT_EDIT_FORBIDDEN


@pytest.mark.integration
def test_add_user_window_merges_with_existing_windows(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    clock = FakeClock(RUN_AT)
    service = TimeConstraintService(service_db_session, clock)
    initial = service.add_user_group(
        master_plan_id,
        (_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),),
    )
    assert initial.success and initial.value is not None

    result = service.add_user_window(
        initial.value.constraint_group_id,
        _window(_utc(2026, 6, 7, 12, 0), _utc(2026, 6, 7, 15, 0)),
    )

    assert result.success and result.value is not None
    assert len(result.value.windows) == 1
    assert result.value.windows[0].start_time.replace(tzinfo=UTC) == _utc(2026, 6, 7, 9, 0)
    assert result.value.windows[0].end_time.replace(tzinfo=UTC) == _utc(2026, 6, 7, 15, 0)


@pytest.mark.integration
def test_remove_user_window_returns_updated_group(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    clock = FakeClock(RUN_AT)
    service = TimeConstraintService(service_db_session, clock)
    created = service.add_user_group(
        master_plan_id,
        (
            _window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),
            _window(_utc(2026, 6, 7, 13, 0), _utc(2026, 6, 7, 15, 0)),
        ),
    )
    assert created.success and created.value is not None
    remove_id = created.value.windows[0].time_window_id

    result = service.remove_user_window(created.value.constraint_group_id, remove_id)

    assert result.success and result.value is not None
    assert len(result.value.windows) == 1
    assert result.value.windows[0].start_time.replace(tzinfo=UTC) == _utc(2026, 6, 7, 13, 0)


@pytest.mark.integration
def test_remove_user_window_deletes_group_when_last_window_removed(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    clock = FakeClock(RUN_AT)
    service = TimeConstraintService(service_db_session, clock)
    created = service.add_user_group(
        master_plan_id,
        (_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),),
    )
    assert created.success and created.value is not None
    group_id = created.value.constraint_group_id
    window_id = created.value.windows[0].time_window_id

    result = service.remove_user_window(group_id, window_id)

    assert result.success and result.value is None
    assert _user_group_count(service_db_session) == 0
    assert service_db_session.get(TimeConstraintGroup, group_id) is None


@pytest.mark.integration
def test_add_user_window_rejects_invalid_window(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    clock = FakeClock(RUN_AT)
    service = TimeConstraintService(service_db_session, clock)
    created = service.add_user_group(
        master_plan_id,
        (_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),),
    )
    assert created.success and created.value is not None

    result = service.add_user_window(
        created.value.constraint_group_id,
        _window(datetime(2026, 6, 7, 14, 0, 30, tzinfo=UTC), _utc(2026, 6, 7, 16, 0)),
    )

    assert not result.success
    assert result.errors[0].code == MessageCode.NON_MINUTE_ALIGNED_WINDOW


@pytest.mark.integration
def test_window_mutations_reject_system_horizon_group(service_db_session: Session) -> None:
    clock = FakeClock(RUN_AT)
    MasterPlanService(service_db_session, clock).ensure_master_exists()
    AppSettingsService(service_db_session, clock).get_settings()
    horizon = MasterHorizonService(service_db_session, clock).refresh_master_horizon(RUN_AT)
    assert horizon.success and horizon.value is not None

    service = TimeConstraintService(service_db_session, clock)
    group_id = horizon.value.constraint_group_id
    window_id = horizon.value.time_window_id

    add_result = service.add_user_window(
        group_id,
        _window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),
    )
    remove_result = service.remove_user_window(group_id, TimeWindowID(window_id))

    assert not add_result.success
    assert add_result.errors[0].code == MessageCode.SYSTEM_CONSTRAINT_DIRECT_EDIT_FORBIDDEN
    assert not remove_result.success
    assert remove_result.errors[0].code == MessageCode.SYSTEM_CONSTRAINT_DIRECT_EDIT_FORBIDDEN


@pytest.mark.integration
def test_remove_user_window_rejects_missing_window(
    service_db_session: Session,
    master_plan_id: PlanID,
) -> None:
    clock = FakeClock(RUN_AT)
    service = TimeConstraintService(service_db_session, clock)
    created = service.add_user_group(
        master_plan_id,
        (_window(_utc(2026, 6, 7, 9, 0), _utc(2026, 6, 7, 12, 0)),),
    )
    assert created.success and created.value is not None

    result = service.remove_user_window(
        created.value.constraint_group_id,
        TimeWindowID(UUID("00000000-0000-4000-8000-000000000001")),
    )

    assert not result.success
    assert result.errors[0].code == MessageCode.TIME_WINDOW_NOT_FOUND
