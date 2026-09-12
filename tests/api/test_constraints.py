from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_master_mutation_guards_through_api(api_client: TestClient) -> None:
    master_id = api_client.get("/api/plans/master").json()["master_plan_id"]
    child = api_client.post(
        f"/api/plans/{master_id}/children",
        json={"kind": "TASK", "is_critical": False, "name": "prerequisite"},
    )
    assert child.status_code == 200
    prerequisite = api_client.post(
        f"/api/plans/{master_id}/prerequisites",
        json={"prerequisite_plan_id": child.json()["plan_id"]},
    )
    constraint = api_client.post(
        f"/api/plans/{master_id}/constraints/groups",
        json={
            "windows": [{"start_time": "2026-06-07T11:00:00Z", "end_time": "2026-06-07T12:00:00Z"}]
        },
    )
    for response in (prerequisite, constraint):
        assert response.status_code == 422
        assert response.json()["detail"]["errors"][0]["code"] == "MASTER_MUTATION_FORBIDDEN"


@pytest.mark.slow
@pytest.mark.parametrize("kind,calendar", [("TASK", "tasks"), ("BLOCK", "blocks")])
def test_constraint_changes_affect_schedule_refresh(
    api_client: TestClient, kind: str, calendar: str
) -> None:
    assert (
        api_client.patch(
            "/api/settings", json={"master_horizon_duration": {"minutes": 180}}
        ).status_code
        == 200
    )
    master_id = api_client.get("/api/plans/master").json()["master_plan_id"]
    child = api_client.post(
        f"/api/plans/{master_id}/children",
        json={"kind": kind, "is_critical": False, "name": "constrained", "duration_minutes": 30},
    )
    assert child.status_code == 200
    plan_id = child.json()["plan_id"]
    group = api_client.post(
        f"/api/plans/{plan_id}/constraints/groups",
        json={
            "windows": [{"start_time": "2026-06-07T11:00:00Z", "end_time": "2026-06-07T12:00:00Z"}]
        },
    )
    assert group.status_code == 200
    group_id = group.json()["constraint_group_id"]
    assert api_client.post("/api/schedule/refresh").status_code == 200
    entries = api_client.get(f"/api/calendar/{calendar}").json()["entries"]
    assert len(entries) == 1
    assert entries[0]["start_time"].startswith("2026-06-07T11:")
    assert (
        api_client.put(
            f"/api/constraints/groups/{group_id}/windows",
            json={
                "windows": [
                    {"start_time": "2026-06-07T11:00:00Z", "end_time": "2026-06-07T11:05:00Z"}
                ]
            },
        ).status_code
        == 200
    )
    assert api_client.post("/api/schedule/refresh").status_code == 422
    assert api_client.delete(f"/api/constraints/groups/{group_id}").status_code == 200
    assert api_client.post("/api/schedule/refresh").status_code == 200
    entries = api_client.get(f"/api/calendar/{calendar}").json()["entries"]
    assert len(entries) == 1
    assert entries[0]["start_time"].startswith("2026-06-07T10:")
