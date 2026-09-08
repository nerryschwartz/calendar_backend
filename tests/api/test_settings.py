from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_settings_preserve_compound_duration_total_minutes(
    api_client: TestClient,
) -> None:
    total_minutes = (1 * 365 * 24 * 60) + (2 * 30 * 24 * 60) + (3 * 24 * 60) + (4 * 60) + 5

    update_response = api_client.patch(
        "/api/settings",
        json={"master_horizon_duration_minutes": total_minutes},
    )
    assert update_response.status_code == 200, update_response.json()
    assert update_response.json()["master_horizon_duration_minutes"] == total_minutes

    get_response = api_client.get("/api/settings")
    assert get_response.status_code == 200
    assert get_response.json()["master_horizon_duration_minutes"] == total_minutes


@pytest.mark.parametrize("minutes", [0, -1])
def test_settings_reject_non_positive_master_horizon_duration(
    api_client: TestClient,
    minutes: int,
) -> None:
    response = api_client.patch(
        "/api/settings",
        json={"master_horizon_duration_minutes": minutes},
    )

    assert response.status_code == 422
    error = response.json()["detail"]["errors"][0]
    assert error["code"] == "INVALID_DURATION"
    assert error["details"] == {"master_horizon_duration_minutes": str(minutes)}


def test_settings_accepts_iana_timezone(api_client: TestClient) -> None:
    response = api_client.patch(
        "/api/settings",
        json={"local_timezone": "America/New_York"},
    )

    assert response.status_code == 200, response.json()
    assert response.json()["local_timezone"] == "America/New_York"


def test_settings_rejects_fixed_timezone_abbreviation(api_client: TestClient) -> None:
    response = api_client.patch(
        "/api/settings",
        json={"local_timezone": "EST"},
    )

    assert response.status_code == 422
    error = response.json()["detail"]["errors"][0]
    assert error["code"] == "INVALID_TIME_WINDOW"
    assert error["details"] == {"local_timezone": "EST"}
