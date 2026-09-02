from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
import pytest


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


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "GOAL", "is_critical": False, "name": "goal child"},
        {
            "kind": "TASK",
            "is_critical": False,
            "name": "task child",
            "duration_minutes": 45,
            "divisible": True,
            "minimum_chunk_size_minutes": 15,
        },
        {
            "kind": "BLOCK",
            "is_critical": False,
            "name": "block child",
            "duration_minutes": 60,
            "divisible": False,
            "block_family": "deep-work",
        },
        {
            "kind": "REPETITION",
            "is_critical": False,
            "name": "repetition child",
            "repeat_mode": "MANUAL_COUNT",
            "start_time": "2026-06-08T09:00:00+00:00",
            "repeat_interval_minutes": 1440,
            "manual_count": 2,
            "default_instance_critical": False,
            "template_type": "TASK",
            "template_name": "template task",
            "template_duration_minutes": 30,
            "template_divisible": False,
        },
    ],
)
def test_create_child_response_includes_plan_id_for_supported_kinds(
    api_client: TestClient,
    payload: dict[str, object],
) -> None:
    master_response = api_client.get("/api/plans/master")
    assert master_response.status_code == 200
    master_id = master_response.json()["master_plan_id"]

    child_response = api_client.post(f"/api/plans/{master_id}/children", json=payload)

    assert child_response.status_code == 200, child_response.json()
    body = child_response.json()
    assert body["plan_id"]
    if "parent_id" in body:
        assert body["parent_id"] == master_id


def test_delete_non_master_child_then_validate_returns_structured_missing_plan(
    api_client: TestClient,
) -> None:
    master_response = api_client.get("/api/plans/master")
    assert master_response.status_code == 200
    master_id = master_response.json()["master_plan_id"]

    child_response = api_client.post(
        f"/api/plans/{master_id}/children",
        json={"kind": "GOAL", "is_critical": False, "name": "temporary goal"},
    )
    assert child_response.status_code == 200
    child_id = child_response.json()["plan_id"]

    delete_response = api_client.delete(f"/api/plans/{child_id}")
    assert delete_response.status_code == 200, delete_response.json()
    assert delete_response.json() == {"status": "ok"}

    validate_response = api_client.post("/api/plans/validate")
    assert validate_response.status_code == 200
    assert validate_response.json() == {"status": "ok"}

    for stale_response in (
        api_client.delete(f"/api/plans/{child_id}"),
        api_client.get(f"/api/plans/{child_id}"),
    ):
        assert stale_response.status_code == 422
        assert stale_response.json()["detail"]["errors"][0]["code"] == "PLAN_NOT_FOUND"


def test_missing_plan_delete_and_detail_use_service_error_envelope(
    api_client: TestClient,
) -> None:
    missing_plan_id = uuid.uuid4()

    for response in (
        api_client.delete(f"/api/plans/{missing_plan_id}"),
        api_client.get(f"/api/plans/{missing_plan_id}"),
    ):
        assert response.status_code == 422
        error = response.json()["detail"]["errors"][0]
        assert error["code"] == "PLAN_NOT_FOUND"
        assert error["details"] == {"plan_id": str(missing_plan_id)}
