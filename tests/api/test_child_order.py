"""The goal owns a complete, atomic ordering of its children."""

from uuid import UUID, uuid4

import pytest
from calendar_backend.domain.enums import CloneStatus
from calendar_backend.models.plans import Plan
from sqlalchemy.orm import Session


def create_goal(client, parent_id, name, critical=False):
    response = client.post(
        f"/api/plans/{parent_id}/children",
        json={
            "kind": "GOAL",
            "name": name,
            "is_critical": critical,
        },
    )
    assert response.status_code == 200, response.json()
    return response.json()["plan_id"]


def test_master_name_and_immutable_rules(api_client):
    master = api_client.get("/api/plans/master").json()
    assert master["plan"]["name"] == "Master"
    master_id = master["master_plan_id"]
    assert (
        api_client.patch(f"/api/plans/{master_id}/rename", json={"name": "Other"}).status_code
        == 422
    )
    child = create_goal(api_client, master_id, "Child")
    assert (
        api_client.put(
            f"/api/plans/{master_id}/children/order",
            json={"critical_child_ids": [child], "non_critical_child_ids": []},
        ).status_code
        == 422
    )
    assert (
        api_client.post(
            f"/api/plans/{master_id}/children",
            json={"kind": "GOAL", "name": "Bad", "is_critical": True},
        ).status_code
        == 422
    )


def test_reorder_across_groups_returns_detail_and_is_dense(api_client):
    master = api_client.get("/api/plans/master").json()["master_plan_id"]
    parent = create_goal(api_client, master, "Parent")
    a = create_goal(api_client, parent, "A", True)
    b = create_goal(api_client, parent, "B")
    c = create_goal(api_client, parent, "C")
    response = api_client.put(
        f"/api/plans/{parent}/children/order",
        json={"critical_child_ids": [c, b], "non_critical_child_ids": [a]},
    )
    assert response.status_code == 200, response.json()
    assert response.json()["plan_id"] == parent
    for plan_id, critical, index in ((c, True, 0), (b, True, 1), (a, False, 0)):
        detail = api_client.get(f"/api/plans/{plan_id}").json()
        assert (detail["goal_is_critical"], detail["goal_sort_order"]) == (critical, index)
    assert api_client.post("/api/plans/validate").status_code == 200


@pytest.mark.parametrize("bad", ["duplicate", "foreign", "missing"])
def test_bad_order_leaves_all_fields_unchanged(api_client, bad):
    master = api_client.get("/api/plans/master").json()["master_plan_id"]
    parent = create_goal(api_client, master, "Parent")
    child = create_goal(api_client, parent, "Child")
    before = api_client.get(f"/api/plans/{child}").json()
    ids = {"duplicate": [child, child], "foreign": [child, str(uuid4())], "missing": []}[bad]
    result = api_client.put(
        f"/api/plans/{parent}/children/order",
        json={"critical_child_ids": ids, "non_critical_child_ids": []},
    )
    assert result.status_code == 422
    assert api_client.get(f"/api/plans/{child}").json() == before


def test_linked_parent_noop_then_changed_order_detaches_subtree(api_client, api_db_engine):
    master = api_client.get("/api/plans/master").json()["master_plan_id"]
    parent = create_goal(api_client, master, "Parent")
    a = create_goal(api_client, parent, "A")
    b = create_goal(api_client, parent, "B")
    with Session(api_db_engine) as session, session.begin():
        for plan_id in (parent, a, b):
            row = session.get(Plan, UUID(plan_id))
            row.clone_status = CloneStatus.LINKED
            row.cloned_from_id = UUID(master)
    for ids, expected in (([a, b], "LINKED"), ([b, a], "DETACHED")):
        response = api_client.put(
            f"/api/plans/{parent}/children/order",
            json={"critical_child_ids": [], "non_critical_child_ids": ids},
        )
        assert response.status_code == 200, response.json()
        for plan_id in (parent, a, b):
            assert api_client.get(f"/api/plans/{plan_id}").json()["clone_status"] == expected
