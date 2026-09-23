"""Recursive creation, projection, and refresh share nested template semantics."""

from datetime import timedelta
from uuid import UUID

import pytest
from calendar_backend.domain.time import sqlite_utc
from calendar_backend.models.plans import Plan, RepetitionPlan
from calendar_backend.services.repetition_projection import snapshot_repetition
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def repetition(template, **overrides):
    return {
        "kind": "REPETITION",
        "name": "Repeat",
        "repeat_mode": "DATE_RANGE",
        "start_time": "2026-09-12T05:00:00Z",
        "end_time": "2026-09-14T05:00:00Z",
        "repeat_interval_minutes": 1440,
        "template": template,
        **overrides,
    }


@pytest.mark.parametrize("kind", ["GOAL", "TASK", "BLOCK", "REPETITION"])
def test_recursive_template_kinds(api_client, kind):
    master = api_client.get("/api/plans/master").json()["master_plan_id"]
    template = {"kind": kind, "name": "First instance"}
    if kind in ("TASK", "BLOCK"):
        template["duration_minutes"] = 30
    if kind == "BLOCK":
        template["block_family"] = "work"
    if kind == "REPETITION":
        template = repetition({"kind": "GOAL", "name": "Inner goal"})
    response = api_client.post(
        f"/api/plans/{master}/children", json=repetition(template, is_critical=False)
    )
    assert response.status_code == 200, response.json()
    template_id = response.json()["template_root_id"]
    assert api_client.get(f"/api/plans/{template_id}").json()["plan_kind"] == kind
    if kind == "GOAL":
        child = api_client.post(
            f"/api/plans/{template_id}/children",
            json={
                "kind": "TASK",
                "name": "Goal child",
                "is_critical": True,
                "duration_minutes": 30,
            },
        )
        assert child.status_code == 200, child.json()
    assert api_client.post("/api/plans/validate").status_code == 200


@pytest.mark.parametrize(
    "extra",
    [
        {"template_type": "TASK"},
        {"template_name": None},
        {"template": {"kind": "TASK", "name": "Bad", "duration_minutes": 0}},
        {"template": {"kind": "GOAL", "name": "Bad", "is_critical": True}},
    ],
)
def test_recursive_invalid_input_writes_nothing(api_client, api_db_engine, extra):
    master = api_client.get("/api/plans/master").json()["master_plan_id"]
    body = repetition({"kind": "GOAL", "name": "Template"}, is_critical=False)
    body.update(extra)
    response = api_client.post(f"/api/plans/{master}/children", json=body)
    assert response.status_code == 422, response.json()
    with Session(api_db_engine) as session:
        assert session.scalar(select(func.count()).select_from(Plan)) == 1


def test_nested_dates_survive_projection_commit_and_refresh(api_client, api_db_engine):
    master = api_client.get("/api/plans/master").json()["master_plan_id"]
    body = repetition(
        repetition(repetition({"kind": "TASK", "name": "Leaf", "duration_minutes": 30})),
        is_critical=False,
    )
    response = api_client.post(f"/api/plans/{master}/children", json=body)
    assert response.status_code == 200, response.json()
    rep_id = response.json()["plan_id"]
    with Session(api_db_engine) as session:
        value = snapshot_repetition(session, session.get(RepetitionPlan, UUID(rep_id)))
    preview = api_client.post(
        "/api/repetitions/preview-instances", json=value.model_dump(mode="json")
    ).json()
    committed = api_client.post(
        f"/api/repetitions/{rep_id}/commit-generation",
        json={"preview": preview, "resolved_refs": {}},
    )
    assert committed.status_code == 200, committed.json()
    for _ in range(2):
        assert api_client.post(f"/api/repetitions/{rep_id}/refresh").status_code == 200
        with Session(api_db_engine) as session:
            for index, instance in enumerate(preview["instances"]):
                for source in value.template.nodes:
                    if source.repetition is None:
                        continue
                    # Match persisted lineage rather than names shared by nested repetitions.
                    row = session.scalar(
                        select(RepetitionPlan)
                        .join(Plan, Plan.plan_id == RepetitionPlan.plan_id)
                        .where(
                            Plan.cloned_from_id == UUID(source.ref),
                            Plan.plan_id.in_([UUID(node["ref"]) for node in instance["nodes"]]),
                        )
                    )
                    assert row is not None
                    assert sqlite_utc(row.start_time) == source.repetition.start_time + timedelta(
                        days=index
                    )
                    assert sqlite_utc(row.end_time) == source.repetition.end_time + timedelta(
                        days=index
                    )
    assert api_client.post("/api/plans/validate").status_code == 200
