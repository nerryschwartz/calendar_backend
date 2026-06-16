"""Cross-service bootstrap and master horizon invariants.

Prompt 7 will add TimeConstraintService rejection of direct user edits to system
constraints; this module covers local master/settings/horizon flows only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from calendar_backend.domain.enums import ConstraintKind
from calendar_backend.models.constraints import TimeConstraintGroup
from calendar_backend.models.plans import Plan
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.master_horizon import MasterHorizonService
from calendar_backend.services.master_plan import MasterPlanService
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .conftest import FakeClock

RUN_STARTED_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)


@pytest.mark.integration
def test_empty_db_bootstrap_master_settings_and_refresh_horizon(
    service_db_session: Session,
) -> None:
    clock = FakeClock(RUN_STARTED_AT)

    master_result = MasterPlanService(service_db_session, clock).ensure_master_exists()
    settings_result = AppSettingsService(service_db_session, clock).get_settings()
    horizon_result = MasterHorizonService(service_db_session, clock).refresh_master_horizon(
        RUN_STARTED_AT
    )

    assert master_result.success and master_result.value is not None
    assert settings_result.success and settings_result.value is not None
    assert horizon_result.success and horizon_result.value is not None
    assert horizon_result.value.horizon_start == RUN_STARTED_AT
    assert horizon_result.value.horizon_end == RUN_STARTED_AT + timedelta(
        minutes=settings_result.value.master_horizon_duration_minutes
    )


@pytest.mark.integration
def test_master_plan_exposes_system_horizon_via_orm_navigation(service_db_session: Session) -> None:
    clock = FakeClock(RUN_STARTED_AT)
    master = MasterPlanService(service_db_session, clock).ensure_master_exists()
    assert master.success and master.value is not None

    AppSettingsService(service_db_session, clock).get_settings()
    MasterHorizonService(service_db_session, clock).refresh_master_horizon(RUN_STARTED_AT)

    plan = service_db_session.scalar(
        select(Plan)
        .where(Plan.plan_id == master.value.plan_id)
        .options(selectinload(Plan.constraint_groups).selectinload(TimeConstraintGroup.windows))
    )
    assert plan is not None

    horizon_groups = [
        group
        for group in plan.constraint_groups
        if group.constraint_kind == ConstraintKind.SYSTEM_MASTER_HORIZON
    ]
    assert len(horizon_groups) == 1
    assert len(horizon_groups[0].windows) == 1
    window = horizon_groups[0].windows[0]
    assert window.start_time.replace(tzinfo=UTC) == RUN_STARTED_AT
