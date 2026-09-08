from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from calendar_backend.db.session import create_session_factory
from calendar_backend.domain.enums import CalendarEntryType, CalendarRunStatus, LastFailureReason
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.results import fail
from calendar_backend.models.blocks import BlockCalendarEntry
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.runs import ActiveCalendarState, CalendarRun
from calendar_backend.services.calendar_read import CalendarReadService
from fastapi.testclient import TestClient
from sqlalchemy import delete, event
from sqlalchemy.engine import Engine

NOW = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)


@pytest.fixture
def timer_rows(api_client: TestClient, api_db_engine: Engine) -> dict[str, str]:
    master_id = api_client.get("/api/plans/master").json()["master_plan_id"]
    plan_ids = {}
    for kind in ("TASK", "BLOCK"):
        response = api_client.post(
            f"/api/plans/{master_id}/children",
            json={
                "kind": kind,
                "name": kind,
                "duration_minutes": 15,
                "is_critical": False,
            },
        )
        assert response.status_code == 200
        plan_ids[kind] = UUID(response.json()["plan_id"])
    activity = api_client.post(
        "/api/free-time/activities",
        json={
            "name": "Reading",
            "real_fraction": "1",
            "minimum_block_size_minutes": 0,
        },
    )
    activity_id = UUID(activity.json()["free_time_activity_id"])
    refs: dict[str, str] = {}
    with create_session_factory(api_db_engine)() as session, session.begin():
        run_id = uuid4()
        stale_run_id = uuid4()
        for run in (run_id, stale_run_id):
            session.add(
                CalendarRun(
                    calendar_run_id=run,
                    run_started_at=NOW,
                    run_finished_at=NOW,
                    status=CalendarRunStatus.SUCCESS,
                    conflict_count=0,
                    warning_count=0,
                    runtime_ms=0,
                    created_at=NOW,
                )
            )
        session.flush()
        session.add(
            ActiveCalendarState(
                singleton_id=1,
                active_calendar_run_id=run_id,
                last_refresh_failed=False,
                updated_at=NOW,
            )
        )
        for kind in ("TASK", "FREE_TIME", "BLOCK"):
            for minutes in (-15, 0, *range(15, 400, 15)):
                entry_id = uuid4()
                refs[f"{kind}:{minutes}"] = str(entry_id)
                values: dict[str, Any] = {
                    "start_time": NOW + timedelta(minutes=minutes),
                    "end_time": NOW + timedelta(minutes=minutes + 15),
                    "calendar_run_id": run_id,
                    "display_label": f"{kind}:{minutes}",
                    "created_at": NOW,
                    "updated_at": NOW,
                }
                if kind == "BLOCK":
                    session.add(
                        BlockCalendarEntry(
                            block_calendar_entry_id=entry_id,
                            source_plan_id=plan_ids[kind],
                            **values,
                        )
                    )
                else:
                    session.add(
                        CalendarEntry(
                            calendar_entry_id=entry_id,
                            entry_type=CalendarEntryType(kind),
                            source_plan_id=plan_ids.get(kind),
                            source_free_time_activity_id=activity_id
                            if kind == "FREE_TIME"
                            else None,
                            **values,
                        )
                    )
        session.add(
            CalendarEntry(
                calendar_entry_id=uuid4(),
                entry_type=CalendarEntryType.TASK,
                start_time=NOW,
                end_time=NOW + timedelta(minutes=15),
                calendar_run_id=stale_run_id,
                display_label="inactive run",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        refs["run_id"] = str(run_id)
    return refs


def test_no_active_run_has_diagnostics(api_client: TestClient) -> None:
    response = api_client.get("/api/timers/active")
    assert response.status_code == 200
    assert response.json() == {
        "timers": [],
        "diagnostics": {
            "backend_now": NOW.isoformat(),
            "active_calendar_run_id": None,
            "last_refresh_failed": False,
            "last_failure_at": None,
            "last_failure_reason": None,
            "nearby_entries": [],
        },
    }


def test_sqlite_active_timer_windows_and_bounded_queries(
    api_client: TestClient, api_db_engine: Engine, timer_rows: dict[str, str]
) -> None:
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "FROM calendar_entry" in statement or "FROM block_calendar_entry" in statement:
            statements.append(statement)

    event.listen(api_db_engine, "before_cursor_execute", capture)
    try:
        response = api_client.get("/api/timers/active")
    finally:
        event.remove(api_db_engine, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    body = response.json()
    assert {row["display_label"] for row in body["timers"]} == {"TASK:0", "BLOCK:0", "FREE_TIME:0"}
    assert {row["source_kind"] for row in body["timers"]} == {"TASK", "BLOCK", "FREE_TIME"}
    assert body["diagnostics"]["active_calendar_run_id"] == timer_rows["run_id"]
    assert body["diagnostics"]["backend_now"] == NOW.isoformat()
    assert len(body["diagnostics"]["nearby_entries"]) == 10
    for row in body["timers"] + body["diagnostics"]["nearby_entries"]:
        assert row["window_start_at"].endswith("+00:00")
        assert row["window_end_at"].endswith("+00:00")
    assert len(statements) == 4
    assert sum("LIMIT" in statement for statement in statements) == 2
    for statement in statements:
        assert "end_time >" in statement
        assert "calendar_run_id =" in statement
        if "LIMIT" not in statement:
            assert "start_time <=" in statement
    for kind in ("tasks", "blocks"):
        rows = api_client.get(f"/api/calendar/{kind}").json()["entries"]
        assert rows
        for row in rows:
            assert row["start_time"].endswith("+00:00")
            assert row["end_time"].endswith("+00:00")
    assert api_client.get("/api/schedule/state").json()["updated_at"].endswith("+00:00")


def test_future_entries_explain_empty_timers_and_last_failure(
    api_client: TestClient, api_db_engine: Engine, timer_rows: dict[str, str]
) -> None:
    with create_session_factory(api_db_engine)() as session, session.begin():
        for model in (CalendarEntry, BlockCalendarEntry):
            session.execute(delete(model).where(model.start_time <= NOW))
        state = session.get(ActiveCalendarState, 1)
        assert state is not None
        state.last_refresh_failed = True
        state.last_failure_at = NOW
        state.last_failure_reason = LastFailureReason.ASSIGNMENT_FAILED
    body = api_client.get("/api/timers/active").json()
    assert body["timers"] == []
    diagnostics = body["diagnostics"]
    assert diagnostics["active_calendar_run_id"] == timer_rows["run_id"]
    assert diagnostics["last_refresh_failed"]
    assert diagnostics["last_failure_at"] == NOW.isoformat()
    assert diagnostics["last_failure_reason"] == "ASSIGNMENT_FAILED"
    assert len(diagnostics["nearby_entries"]) == 10
    assert all(
        datetime.fromisoformat(row["window_start_at"]) > NOW
        for row in diagnostics["nearby_entries"]
    )


@pytest.mark.parametrize(
    "kind,prefix", [("TASK", "task"), ("BLOCK", "block"), ("FREE_TIME", "free_time")]
)
def test_complete_ended_sqlite_timer(
    api_client: TestClient, timer_rows: dict[str, str], kind: str, prefix: str
) -> None:
    timer_key = f"{prefix}:{timer_rows[f'{kind}:-15']}"
    response = api_client.post(f"/api/timers/{timer_key}/complete")
    assert response.status_code == 200, response.text
    if kind == "FREE_TIME":
        assert response.json()["notification"] is None
    else:
        assert response.json()["notification"]["window_end_at"] == NOW.isoformat()


@pytest.mark.parametrize("method", ["get_task_calendar", "get_block_calendar"])
def test_timer_calendar_read_failures_are_not_hidden(api_client: TestClient, method: str) -> None:
    error = ServiceMessage(MessageCode.ACTIVE_CALENDAR_RUN_NOT_SET, "calendar read failed", {})
    with patch.object(CalendarReadService, method, return_value=fail(error)):
        response = api_client.get("/api/timers/active")
    assert response.status_code == 422
    assert response.json()["detail"]["errors"][0]["message"] == "calendar read failed"


@pytest.mark.slow
def test_refresh_then_poll_sqlite_timers_regression(api_client: TestClient) -> None:
    master_id = api_client.get("/api/plans/master").json()["master_plan_id"]
    assert (
        api_client.patch(
            "/api/settings",
            json={"master_horizon_duration_minutes": 180, "exact_solver_time_limit_seconds": 2},
        ).status_code
        == 200
    )
    assert (
        api_client.post(
            f"/api/plans/{master_id}/children",
            json={
                "kind": "TASK",
                "is_critical": False,
                "name": "Timer integration task",
                "duration_minutes": 15,
            },
        ).status_code
        == 200
    )
    assert api_client.post("/api/schedule/refresh").status_code == 200
    calendar = api_client.get("/api/calendar/tasks").json()
    assert calendar["entries"][0]["start_time"] == NOW.isoformat()
    response = api_client.get("/api/timers/active")
    assert response.status_code == 200, response.text
    assert response.json()["timers"][0]["display_label"] == "Timer integration task"
