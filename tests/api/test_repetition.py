"""Repetition routes exercise real persistence with an unaligned server clock."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from ortools.sat.python import cp_model


def create_repetition(client: TestClient, **overrides: object) -> str:
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


def test_generation_status_is_read_only_and_matches_contract(api_client: TestClient):
    assert api_client.get("/api/repetitions/generation-status").json() == {"repetitions": []}
    first = create_repetition(api_client)
    second = create_repetition(api_client, name="Dinner")
    rows = api_client.get("/api/repetitions/generation-status").json()["repetitions"]
    assert len(rows) == 2
    for row in rows:
        assert set(row) == {
            "plan_id",
            "name",
            "parent_id",
            "template_root_id",
            "generated_at",
            "instance_count",
        }
        assert row["generated_at"] is None and row["instance_count"] == 0
    response = api_client.post("/api/schedule/refresh")
    assert response.status_code == 422, response.json()
    errors = response.json()["detail"]["errors"]
    assert {error["details"]["repetition_plan_id"] for error in errors} == {first, second}
    assert {error["code"] for error in errors} == {"REPETITION_NOT_GENERATED"}
    assert api_client.post(f"/api/repetitions/{first}/generate-instances").status_code == 200
    rows = api_client.get("/api/repetitions/generation-status").json()["repetitions"]
    generated = next(row for row in rows if row["plan_id"] == first)
    assert generated["instance_count"] == 2 and generated["generated_at"] is not None
    assert next(row for row in rows if row["plan_id"] == second)["generated_at"] is None


@pytest.mark.slow
def test_lunch_generates_fourteen_daily_tasks_in_shifted_windows(
    lunch_api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_solver = cp_model.CpSolver

    def single_worker_solver():
        solver = original_solver()
        solver.parameters.num_search_workers = 1
        return solver

    monkeypatch.setattr(cp_model, "CpSolver", single_worker_solver)
    client = lunch_api_client
    assert (
        client.patch(
            "/api/settings",
            json={
                "master_horizon_duration": {"days": 14},
                "local_timezone": "America/Chicago",
                "exact_solver_time_limit_seconds": 2,
            },
        ).status_code
        == 200
    )
    rep_id = create_repetition(
        client,
        repeat_mode="DATE_RANGE",
        manual_count=None,
        start_time="2026-09-12T05:00:00Z",
        end_time="2026-09-26T05:00:00Z",
    )
    status = client.get("/api/repetitions/generation-status").json()["repetitions"][0]
    template_id = status["template_root_id"]
    window = {"start_time": "2026-09-12T16:00:00Z", "end_time": "2026-09-12T20:00:00Z"}
    group = client.post(f"/api/plans/{template_id}/constraints/groups", json={"windows": [window]})
    assert group.status_code == 200, group.json()
    assert client.post(f"/api/repetitions/{rep_id}/generate-instances").status_code == 200
    response = client.post("/api/schedule/refresh")
    assert response.status_code == 200, response.json()
    entries = client.get("/api/calendar/tasks").json()["entries"]
    assert len(entries) == 14
    local_dates = set()
    for entry in entries:
        start = datetime.fromisoformat(entry["start_time"]).astimezone(ZoneInfo("America/Chicago"))
        end = datetime.fromisoformat(entry["end_time"]).astimezone(ZoneInfo("America/Chicago"))
        assert start.hour >= 11 and (end.hour < 15 or (end.hour == 15 and end.minute == 0))
        assert end - start == timedelta(minutes=30)
        assert entry["source_plan_id"] != template_id
        local_dates.add(start.date())
    assert local_dates == {
        datetime(2026, 9, 12).date() + timedelta(days=index) for index in range(14)
    }
    status = client.get("/api/repetitions/generation-status").json()["repetitions"][0]
    assert status["instance_count"] == 14
    # A shell window is absolute and must not be shifted/reclassified as a template window.
    assert (
        client.post(
            f"/api/plans/{rep_id}/constraints/groups", json={"windows": [window]}
        ).status_code
        == 200
    )
    assert client.post("/api/schedule/refresh").status_code == 422
