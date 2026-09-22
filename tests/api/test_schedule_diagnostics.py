"""Scheduling failures explain evidence and preserve the last usable calendar."""

from datetime import UTC, datetime

from calendar_backend.api.deps import get_clock
from calendar_backend.domain.enums import SolverStatus
from calendar_backend.models.runs import ActiveCalendarState, CalendarRun
from sqlalchemy import select
from sqlalchemy.orm import Session



class DiagnosticTestClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now_utc(self) -> datetime:
        return self._now


def setup_two_things(client):
    client.app.dependency_overrides[get_clock] = lambda: DiagnosticTestClock(
        datetime(2026, 9, 16, 0, 33, tzinfo=UTC)
    )
    assert (
        client.patch(
            "/api/settings",
            json={"master_horizon_duration": {"years": 2}, "local_timezone": "America/Chicago"},
        ).status_code
        == 200
    )
    master = client.get("/api/plans/master").json()["master_plan_id"]

    def create(parent, **body):
        response = client.post(f"/api/plans/{parent}/children", json=body)
        assert response.status_code == 200, response.json()
        return response.json()["plan_id"]

    def constrain(plan, start, end):
        response = client.post(
            f"/api/plans/{plan}/constraints/groups",
            json={"windows": [{"start_time": start, "end_time": end}]},
        )
        assert response.status_code == 200, response.json()

    parent = create(master, kind="GOAL", name="Two Things", is_critical=False)
    constrain(parent, "2026-09-16T05:00:00Z", "2026-09-17T04:59:00Z")
    first = create(
        parent,
        kind="TASK",
        name="Thing 1",
        is_critical=True,
        duration_minutes=120,
        divisible=True,
        minimum_chunk_size_minutes=60,
    )
    constrain(first, "2026-09-17T00:00:00Z", "2026-09-17T03:00:00Z")
    second = create(parent, kind="TASK", name="Thing 2", is_critical=True, duration_minutes=60)
    constrain(second, "2026-09-17T00:30:00Z", "2026-09-17T02:30:00Z")
    return first, second


def test_confirmed_schedule_then_impossible_edit_preserves_calendar(api_client, api_db_engine):
    first, second = setup_two_things(api_client)
    success = api_client.post("/api/schedule/refresh")
    assert success.status_code == 200, success.json()
    entries = api_client.get("/api/calendar/tasks").json()["entries"]
    assert len(entries) == 3
    assert [(entry["display_label"], entry["start_time"][11:16]) for entry in entries] == [
        ("Thing 1", "00:00"),
        ("Thing 2", "01:00"),
        ("Thing 1", "02:00"),
    ]
    with Session(api_db_engine) as session:
        prior_run = session.get(ActiveCalendarState, 1).active_calendar_run_id
    assert (
        api_client.patch(
            f"/api/plans/{second}/task/scheduling",
            json={"duration_minutes": 181, "divisible": False},
        ).status_code
        == 200
    )
    failure = api_client.post("/api/schedule/refresh")
    assert failure.status_code == 422, failure.json()
    assignment = failure.json()["detail"]["value"]["assignment"]
    assert assignment["optimization_status"] == "INFEASIBLE"
    diagnostics = assignment["conflicts"][0]["diagnostics"]
    assert diagnostics["solver"]["proof_status"] == "proven_infeasible"
    assert {item["name"] for item in diagnostics["tasks"]} == {"Thing 1", "Thing 2"}
    assert {item["name"] for item in diagnostics["constraint_sources"]} >= {
        "Master",
        "Two Things",
        "Thing 1",
        "Thing 2",
    }
    assert api_client.get("/api/calendar/tasks").json()["entries"] == entries
    with Session(api_db_engine) as session:
        assert session.get(ActiveCalendarState, 1).active_calendar_run_id == prior_run
        assert (
            session.scalar(
                select(CalendarRun).where(CalendarRun.calendar_run_id != prior_run)
            ).status
            == "FAILED"
        )


def test_guard_and_greedy_exhaustion_is_unknown_not_infeasible(api_client, api_db_engine):
    setup_two_things(api_client)
    assert (
        api_client.patch("/api/settings", json={"exact_solver_model_size_limit": 1}).status_code
        == 200
    )
    response = api_client.post("/api/schedule/refresh")
    assert response.status_code == 422, response.json()
    result = response.json()["detail"]["value"]["assignment"]
    assert result["optimization_status"] == "UNKNOWN"
    assert any(item["code"] == "SOLVER_LIMIT_REACHED" for item in result["warnings"])
    diagnostic = result["conflicts"][0]["diagnostics"]["solver"]
    assert diagnostic["stage"] == "model_size_guard"
    assert diagnostic["estimate"] > diagnostic["limit"] == 1
    assert diagnostic["proof_status"] == "not_proven"
    with Session(api_db_engine) as session:
        assert session.get(ActiveCalendarState, 1).active_calendar_run_id is None
        assert session.scalar(select(CalendarRun)).solver_status == SolverStatus.UNKNOWN


def test_missing_named_family_has_named_resolution_diagnostic(api_client):
    _, second = setup_two_things(api_client)
    assert (
        api_client.put(
            f"/api/plans/{second}/task/block-families", json={"families": ["work"]}
        ).status_code
        == 200
    )
    response = api_client.post("/api/schedule/refresh")
    assert response.status_code == 422, response.json()
    conflicts = response.json()["detail"]["value"]["assignment"]["conflicts"]
    assert conflicts[0]["diagnostics"]["tasks"][0]["name"] == "Thing 2"
    assert conflicts[0]["diagnostics"]["tasks"][0]["allowed_block_families"] == ["work"]
    assert conflicts[0]["diagnostics"]["effective_windows"][0]["windows"] == []
