from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

DRAFT_URL = "/api/free-time/activities/draft-edits"


def _create(draft_ref: str = "draft:reading", fraction: str = "1") -> dict[str, Any]:
    return {
        "op": "create",
        "draft_ref": draft_ref,
        "name": "Reading",
        "real_fraction": fraction,
        "minimum_block_size_minutes": 15,
        "enabled": True,
    }


def test_draft_create_then_edit_families_and_prerequisites(api_client: TestClient) -> None:
    master_id = api_client.get("/api/plans/master").json()["master_plan_id"]
    response = api_client.post(
        DRAFT_URL,
        json={
            "edits": [
                _create(),
                {
                    "op": "update",
                    "activity_ref": "draft:reading",
                    "name": "Books",
                    "minimum_block_size_minutes": 20,
                },
                {
                    "op": "set_block_families",
                    "activity_ref": "draft:reading",
                    "families": ["focus"],
                },
                {
                    "op": "add_prerequisite",
                    "activity_ref": "draft:reading",
                    "prerequisite_plan_id": master_id,
                },
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["applied_count"] == 4
    activity = response.json()["activities"][0]
    assert activity["name"] == "Books"
    assert activity["minimum_block_size_minutes"] == 20
    assert activity["allowed_block_families"] == ["free-time", "focus"]
    assert activity["prerequisite_plan_ids"] == [master_id]
    activity_id = activity["free_time_activity_id"]
    response = api_client.post(
        DRAFT_URL,
        json={
            "edits": [
                {"op": "clear_block_families", "activity_ref": activity_id},
                {
                    "op": "remove_prerequisite",
                    "activity_ref": activity_id,
                    "prerequisite_plan_id": master_id,
                },
                {"op": "set_enabled", "activity_ref": activity_id, "enabled": False},
            ]
        },
    )
    assert response.status_code == 200, response.text
    activity = response.json()["activities"][0]
    assert not activity["enabled"]
    assert activity["prerequisite_plan_ids"] == []
    assert activity["allowed_block_families"] == ["free-time", "default"]


def test_draft_validates_final_fractions_and_updates_existing(api_client: TestClient) -> None:
    first = api_client.post(DRAFT_URL, json={"edits": [_create()]}).json()["activities"][0]
    activity_id = first["free_time_activity_id"]
    response = api_client.post(
        DRAFT_URL,
        json={
            "edits": [
                _create("draft:second", "0.4"),
                {"op": "update", "activity_ref": activity_id, "real_fraction": "0.6"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert sorted(float(row["real_fraction"]) for row in response.json()["activities"]) == [
        0.4,
        0.6,
    ]
    second_id = next(
        row["free_time_activity_id"]
        for row in response.json()["activities"]
        if row["free_time_activity_id"] != activity_id
    )
    response = api_client.post(
        DRAFT_URL,
        json={
            "edits": [
                {"op": "delete", "activity_ref": second_id},
                {"op": "update", "activity_ref": activity_id, "real_fraction": "1"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["activities"]) == 1


@pytest.mark.parametrize(
    "edits",
    [
        [],
        [_create(), {"op": "delete", "activity_ref": "draft:reading"}],
        [{"op": "delete", "activity_ref": "draft:unsaved"}],
    ],
)
def test_draft_drop_unsaved_and_empty_batch(
    api_client: TestClient, edits: list[dict[str, Any]]
) -> None:
    response = api_client.post(DRAFT_URL, json={"edits": edits})
    assert response.status_code == 200, response.text
    assert response.json() == {"applied_count": len(edits), "activities": []}
    assert api_client.get("/api/free-time/activities").json()["activities"] == []


@pytest.mark.parametrize(
    "bad_edit",
    [
        {"op": "update", "activity_ref": "draft:missing", "name": "missing"},
        {"op": "delete", "activity_ref": str(uuid4())},
        {"op": "set_block_families", "activity_ref": "draft:reading", "families": [""]},
        {
            "op": "add_prerequisite",
            "activity_ref": "draft:reading",
            "prerequisite_plan_id": str(uuid4()),
        },
        {
            "op": "remove_prerequisite",
            "activity_ref": "draft:reading",
            "prerequisite_plan_id": str(uuid4()),
        },
        {"op": "update", "activity_ref": "draft:reading", "real_fraction": "0.5"},
        _create(),
    ],
)
def test_draft_error_rolls_back_earlier_edits(
    api_client: TestClient, bad_edit: dict[str, Any]
) -> None:
    response = api_client.post(DRAFT_URL, json={"edits": [_create(), bad_edit]})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["applied_count"] == 0
    assert response.json()["detail"]["errors"]
    assert api_client.get("/api/free-time/activities").json()["activities"] == []


def test_draft_duplicate_prerequisite_rolls_back(api_client: TestClient) -> None:
    master_id = api_client.get("/api/plans/master").json()["master_plan_id"]
    prerequisite = {
        "op": "add_prerequisite",
        "activity_ref": "draft:reading",
        "prerequisite_plan_id": master_id,
    }
    response = api_client.post(DRAFT_URL, json={"edits": [_create(), prerequisite, prerequisite]})
    assert response.status_code == 422
    assert response.json()["detail"]["errors"][0]["code"] == "DUPLICATE_FREE_TIME_PREREQUISITE"
    assert api_client.get("/api/free-time/activities").json()["activities"] == []


@pytest.mark.parametrize(
    "edits",
    [
        [{"op": "unsupported"}],
        [{"op": "set_enabled", "activity_ref": "draft:x"}],
        [{**_create(), "minimum_block_size_minutes": -1}],
        [{**_create(), "real_fraction": "NaN"}],
    ],
)
def test_invalid_draft_schema_has_zero_applied_count(
    api_client: TestClient, edits: list[dict[str, Any]]
) -> None:
    response = api_client.post(DRAFT_URL, json={"edits": edits})
    assert response.status_code == 422
    assert response.json()["detail"]["applied_count"] == 0
    assert response.json()["detail"]["errors"][0]["code"] == "INVALID_FREE_TIME_DRAFT"


def test_failed_draft_preserves_existing_activity(api_client: TestClient) -> None:
    activity = api_client.post(DRAFT_URL, json={"edits": [_create()]}).json()["activities"][0]
    activity_id = activity["free_time_activity_id"]
    before = api_client.get(f"/api/free-time/activities/{activity_id}").json()
    response = api_client.post(
        DRAFT_URL,
        json={
            "edits": [
                {
                    "op": "update",
                    "activity_ref": activity_id,
                    "name": "Changed",
                    "real_fraction": "0.5",
                },
            ]
        },
    )
    assert response.status_code == 422
    assert api_client.get(f"/api/free-time/activities/{activity_id}").json() == before


def test_delete_activity_route_and_not_found(api_client: TestClient) -> None:
    activity = api_client.post(DRAFT_URL, json={"edits": [_create()]}).json()["activities"][0]
    activity_id = activity["free_time_activity_id"]
    assert api_client.delete(f"/api/free-time/activities/{activity_id}").json() == {"status": "ok"}
    assert api_client.get("/api/free-time/activities").json()["activities"] == []
    response = api_client.delete(f"/api/free-time/activities/{activity_id}")
    assert response.status_code == 422
    assert response.json()["detail"]["errors"][0]["code"] == "FREE_TIME_ACTIVITY_NOT_FOUND"
