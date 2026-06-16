from __future__ import annotations

from datetime import UTC, datetime

import pytest
from calendar_backend.domain.dtos import GoalPlanDTO
from calendar_backend.domain.enums import PlanKind
from calendar_backend.models.plans import GoalPlan, Plan
from calendar_backend.services.master_plan import MASTER_PLAN_NAME, MasterPlanService
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .conftest import FakeClock


@pytest.mark.integration
def test_ensure_master_exists_creates_master_on_empty_db(service_db_session: Session) -> None:
    clock = FakeClock(datetime(2026, 6, 7, 12, 0, tzinfo=UTC))
    result = MasterPlanService(service_db_session, clock).ensure_master_exists()

    assert result.success is True
    assert result.value is not None
    assert isinstance(result.value, GoalPlanDTO)


@pytest.mark.integration
def test_ensure_master_exists_is_idempotent(service_db_session: Session) -> None:
    clock = FakeClock(datetime(2026, 6, 7, 12, 0, tzinfo=UTC))
    service = MasterPlanService(service_db_session, clock)

    first = service.ensure_master_exists()
    second = service.ensure_master_exists()

    assert first.success and first.value is not None
    assert second.success and second.value is not None
    assert first.value.plan_id == second.value.plan_id

    master_count = service_db_session.scalar(
        select(func.count()).select_from(Plan).where(Plan.is_master.is_(True))
    )
    assert master_count == 1


@pytest.mark.integration
def test_ensure_master_exists_master_field_invariants(service_db_session: Session) -> None:
    clock = FakeClock(datetime(2026, 6, 7, 12, 0, tzinfo=UTC))
    result = MasterPlanService(service_db_session, clock).ensure_master_exists()

    assert result.success and result.value is not None
    dto = result.value
    assert dto.name == MASTER_PLAN_NAME
    assert dto.is_master is True
    assert dto.parent_id is None
    assert dto.created_at == clock.now_utc()
    assert dto.updated_at == clock.now_utc()

    plan = service_db_session.get(Plan, dto.plan_id)
    assert plan is not None
    assert plan.plan_kind == PlanKind.GOAL
    goal_plan = service_db_session.get(GoalPlan, dto.plan_id)
    assert goal_plan is not None
