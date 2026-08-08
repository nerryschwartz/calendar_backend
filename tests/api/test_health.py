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
