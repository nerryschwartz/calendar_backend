from __future__ import annotations

from calendar_backend.services.master_plan import MasterPlanService
from calendar_backend.services.plan_tree_read import PlanTreeReadService


def test_plan_tree_read_master_and_detail(service_db_session, fake_clock) -> None:
    master = MasterPlanService(service_db_session, fake_clock).ensure_master_exists()
    assert master.success
    assert master.value is not None

    read = PlanTreeReadService(service_db_session, fake_clock)
    master_id = read.ensure_master_and_get_id()
    assert master_id.success

    detail = read.get_plan_detail(master.value.plan_id)
    assert detail.success
    assert detail.value is not None
    assert detail.value.is_master is True
    assert detail.value.ancestry[0].plan_id == master.value.plan_id


def test_plan_tree_search(service_db_session, fake_clock) -> None:
    master = MasterPlanService(service_db_session, fake_clock).ensure_master_exists()
    assert master.success and master.value is not None

    read = PlanTreeReadService(service_db_session, fake_clock)
    results = read.search_plans(master.value.name[:3].lower())
    assert results.success
    assert results.value is not None
    assert len(results.value) >= 1
