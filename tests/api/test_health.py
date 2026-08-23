from __future__ import annotations

from fastapi.testclient import TestClient


def test_health(api_client: TestClient) -> None:
    response = api_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_get_master(api_client: TestClient) -> None:
    response = api_client.get("/api/plans/master")
    assert response.status_code == 200
    body = response.json()
    assert "master_plan_id" in body
    assert body["plan"]["is_master"] is True


def test_get_master_create_non_critical_goal_and_validate(api_client: TestClient) -> None:
    master_response = api_client.get("/api/plans/master")
    assert master_response.status_code == 200
    master_id = master_response.json()["master_plan_id"]

    child_response = api_client.post(
        f"/api/plans/{master_id}/children",
        json={"kind": "GOAL", "is_critical": False, "name": "generic goal"},
    )
    assert child_response.status_code == 200

    validate_response = api_client.post("/api/plans/validate")
    assert validate_response.status_code == 200
    assert validate_response.json() == {"status": "ok"}
