from __future__ import annotations

from fastapi.testclient import TestClient


def test_saved_non_critical_master_goal_sequence_refreshes_with_non_minute_clock(
    non_minute_api_client: TestClient,
) -> None:
    master_response = non_minute_api_client.get("/api/plans/master")
    assert master_response.status_code == 200
    master_id = master_response.json()["master_plan_id"]

    child_response = non_minute_api_client.post(
        f"/api/plans/{master_id}/children",
        json={"kind": "GOAL", "is_critical": False, "name": "queued goal"},
    )
    assert child_response.status_code == 200

    validate_response = non_minute_api_client.post("/api/plans/validate")
    assert validate_response.status_code == 200
    assert validate_response.json() == {"status": "ok"}

    refresh_response = non_minute_api_client.post("/api/schedule/refresh")
    assert refresh_response.status_code == 200
