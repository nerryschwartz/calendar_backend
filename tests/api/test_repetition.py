"""Repetition routes exercise real persistence with an unaligned server clock."""

from fastapi.testclient import TestClient


def create_repetition(client: TestClient, **overrides) -> str:
    master_id = client.get("/api/plans/master").json()["master_plan_id"]
    response = client.post(
        f"/api/plans/{master_id}/children",
        json={
            "kind": "REPETITION",
            "name": "Lunch",
            "is_critical": False,
            "repeat_mode": "MANUAL_COUNT",
            "start_time": "2026-06-07T10:00:00Z",
            "repeat_interval_minutes": 1440,
            "manual_count": 2,
            "template_type": "TASK",
            "template_name": "Lunch template",
            "template_duration_minutes": 30,
            **overrides,
        },
    )
    assert response.status_code == 200, response.json()
    return response.json()["plan_id"]


def test_generation_and_refresh_normalize_only_internal_clock(non_minute_api_client: TestClient):
    client = non_minute_api_client
    plan_id = create_repetition(client)
    response = client.post(f"/api/repetitions/{plan_id}/generate-instances")
    assert response.status_code == 200, response.json()
    assert response.json()["generated_at"] == "2026-06-07T10:00:00+00:00"
    assert client.post(f"/api/repetitions/{plan_id}/refresh").status_code == 200
    assert client.post("/api/repetitions/refresh-all").status_code == 200
    duplicate = client.post(f"/api/repetitions/{plan_id}/generate-instances")
    assert duplicate.status_code == 422
    assert duplicate.json()["detail"]["errors"][0]["code"] == "REPETITION_ALREADY_GENERATED"


def test_partial_repetition_patch_preserves_mode_fields(non_minute_api_client: TestClient):
    client = non_minute_api_client
    plan_id = create_repetition(client)
    response = client.patch(
        f"/api/repetitions/{plan_id}/settings", json={"default_instance_critical": True}
    )
    assert response.status_code == 200, response.json()
    assert response.json()["manual_count"] == 2
    bad = client.patch(
        f"/api/repetitions/{plan_id}/settings", json={"start_time": "2026-06-07T10:00:37Z"}
    )
    assert bad.status_code == 422


def test_open_ended_generation_refreshes_calendar_horizon(non_minute_api_client: TestClient):
    client = non_minute_api_client
    assert (
        client.patch("/api/settings", json={"master_horizon_duration": {"days": 3}}).status_code
        == 200
    )
    plan_id = create_repetition(client, repeat_mode="DATE_RANGE", manual_count=None)
    response = client.post(f"/api/repetitions/{plan_id}/generate-instances")
    assert response.status_code == 200, response.json()
