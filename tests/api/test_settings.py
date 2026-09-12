from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_settings_preserve_compound_calendar_duration(
    api_client: TestClient,
) -> None:
    duration = dict(years=1, months=2, days=3, hours=4, minutes=5)

    update_response = api_client.patch(
        "/api/settings",
        json={"master_horizon_duration": duration},
    )
    assert update_response.status_code == 200, update_response.json()
    assert update_response.json()["master_horizon_duration"] == duration

    get_response = api_client.get("/api/settings")
    assert get_response.status_code == 200
    assert get_response.json()["master_horizon_duration"] == duration


@pytest.mark.parametrize("minutes", [0, -1, 1.5, True, "4"])
def test_settings_reject_non_positive_master_horizon_duration(
    api_client: TestClient,
    minutes: int,
) -> None:
    response = api_client.patch(
        "/api/settings",
        json={"master_horizon_duration": {"minutes": minutes}},
    )

    assert response.status_code == 422


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
