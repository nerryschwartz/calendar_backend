from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from calendar_backend.db.base import Base
from calendar_backend.domain.enums import CloneStatus, PlanKind
from calendar_backend.domain.time import Clock
from calendar_backend.models.plans import GoalPlan, Plan
from calendar_backend.models.settings import AppSettings
from sqlalchemy import func, insert, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .conftest import SERVICE_SCHEMA_TABLE_NAMES, service_transaction


@pytest.mark.integration
def test_service_db_engine_connects(service_db_engine: Engine) -> None:
    with service_db_engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1


@pytest.mark.integration
def test_service_db_schema_includes_all_v1_tables(service_db_engine: Engine) -> None:
    table_names = set(Base.metadata.tables)
    assert table_names >= SERVICE_SCHEMA_TABLE_NAMES


@pytest.mark.integration
def test_service_db_foreign_keys_enabled(service_db_engine: Engine) -> None:
    with service_db_engine.connect() as connection:
        foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
    assert foreign_keys == 1


@pytest.mark.integration
def test_empty_service_db_has_no_master_or_settings(service_db_session: Session) -> None:
    master_count = service_db_session.scalar(
        select(func.count()).select_from(Plan).where(Plan.is_master.is_(True))
    )
    settings_count = service_db_session.scalar(select(func.count()).select_from(AppSettings))
    assert master_count == 0
    assert settings_count == 0


@pytest.mark.integration
def test_service_transaction_commits(service_db_session: Session) -> None:
    plan_id = uuid.uuid4()
    now = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)

    with service_transaction(service_db_session) as txn:
        txn.execute(
            insert(Plan).values(
                plan_id=plan_id,
                plan_kind=PlanKind.GOAL,
                name="smoke",
                parent_id=None,
                is_master=False,
                cloned_from_id=None,
                clone_status=CloneStatus.NOT_CLONED,
                created_at=now,
                updated_at=now,
            )
        )
        txn.execute(insert(GoalPlan).values(plan_id=plan_id))

    count = service_db_session.scalar(select(func.count()).select_from(Plan))
    assert count == 1


@pytest.mark.integration
def test_fake_clock_fixture_returns_fixed_instant(fake_clock: Clock) -> None:
    fixed = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
    assert fake_clock.now_utc() == fixed
