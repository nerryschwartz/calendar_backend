"""Integration tests for BlockResolutionService."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from calendar_backend.domain.enums import PlanKind
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.plan_create import BlockCreatePayload
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.block_resolution import (
    BlockResolutionService,
    _resolve_from_current_tree,  # pyright: ignore[reportPrivateUsage]
)
from calendar_backend.services.goal import GoalService
from calendar_backend.services.master_horizon import MasterHorizonService
from calendar_backend.services.master_plan import MasterPlanService
from calendar_backend.services.task_resolution import load_plan_graph
from sqlalchemy.orm import Session

from .conftest import FakeClock

RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)


def _bootstrap_master_with_horizon(session: Session) -> PlanID:
    clock = FakeClock(RUN_AT)
    master = MasterPlanService(session, clock).ensure_master_exists()
    assert master.success and master.value is not None
    AppSettingsService(session, clock).get_settings()
    MasterHorizonService(session, clock).refresh_master_horizon(RUN_AT)
    return master.value.plan_id


def _goal_service(session: Session) -> GoalService:
    return GoalService(session, FakeClock(RUN_AT))


def _resolution_service(session: Session) -> BlockResolutionService:
    return BlockResolutionService(session, FakeClock(RUN_AT))


def _create_block(session: Session, parent_id: PlanID, *, name: str = "block") -> PlanID:
    result = _goal_service(session).create_child(
        parent_id,
        PlanKind.BLOCK,
        BlockCreatePayload(name, 30, False, None, "focus"),
        is_critical=False,
    )
    assert result.success and result.value is not None
    return result.value.plan_id


@pytest.mark.integration
def test_resolve_blocks_returns_valid_incomplete_block(service_db_session: Session) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    _create_block(service_db_session, master_id, name="focus block")

    result = _resolution_service(service_db_session).resolve_blocks(RUN_AT)

    assert result.success and result.value is not None
    assert len(result.value.valid_incomplete) == 1
    assert result.value.valid_incomplete[0].name == "focus block"
    assert result.value.run_started_at == RUN_AT


@pytest.mark.integration
def test_resolve_from_current_tree_seam(service_db_session: Session) -> None:
    master_id = _bootstrap_master_with_horizon(service_db_session)
    _create_block(service_db_session, master_id)

    plans = load_plan_graph(service_db_session)
    result = _resolve_from_current_tree(RUN_AT, plans=plans)

    assert len(result.valid_incomplete) == 1
