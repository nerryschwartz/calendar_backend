"""Preview is read-only; commit is keyed, atomic, and safe to retry."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID

import pytest
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.repetition_projection import CommitGenerationInput
from calendar_backend.models.constraints import TimeWindow
from calendar_backend.models.plans import Plan, RepetitionPlan
from calendar_backend.models.repetitions import (
    RepetitionGenerationReceipt,
    RepetitionInstance,
    RepetitionSkippedOccurrence,
)
from calendar_backend.models.settings import AppSettings
from calendar_backend.services import repetition_projection as projection_service
from calendar_backend.services.repetition_projection import snapshot_repetition
from ortools.sat.python import cp_model
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def create_input(client, engine, **overrides):
    master = client.get("/api/plans/master").json()["master_plan_id"]
    response = client.post(
        f"/api/plans/{master}/children",
        json={
            "kind": "REPETITION",
            "name": "Lunch",
            "is_critical": False,
            "repeat_mode": "MANUAL_COUNT",
            "start_time": "2026-09-12T05:00:00Z",
            "repeat_interval_minutes": 1440,
            "manual_count": 2,
            "template_type": "TASK",
            "template_name": "Lunch template",
            "template_duration_minutes": 30,
            **overrides,
        },
    )
    assert response.status_code == 200, response.json()
    rep_id = response.json()["plan_id"]
    with Session(engine) as session:
        value = snapshot_repetition(session, session.get(RepetitionPlan, UUID(rep_id)))
    return rep_id, value.model_dump(mode="json")


def test_preview_on_empty_database_does_not_bootstrap(api_client, api_db_engine):
    body = {
        "repetition_ref": "draft:rep",
        "settings": {
            "repeat_mode": "DATE_RANGE",
            "start_time": "2026-06-07T10:00:00Z",
            "repeat_interval_minutes": 525600,
        },
        "template": {
            "root_ref": "draft:template",
            "nodes": [
                {
                    "ref": "draft:template",
                    "kind": "TASK",
                    "name": "Task",
                    "duration_minutes": 30,
                }
            ],
        },
    }
    response = api_client.post("/api/repetitions/preview-instances", json=body)
    assert response.status_code == 200, response.json()
    with Session(api_db_engine) as session:
        assert session.scalar(select(func.count()).select_from(Plan)) == 0
        assert session.scalar(select(func.count()).select_from(AppSettings)) == 0
        assert session.scalar(select(func.count()).select_from(RepetitionGenerationReceipt)) == 0


def test_preview_commit_and_identical_retry(api_client, api_db_engine):
    rep_id, value = create_input(api_client, api_db_engine)
    preview = api_client.post("/api/repetitions/preview-instances", json=value)
    assert preview.status_code == 200, preview.json()
    with Session(api_db_engine) as session:
        assert session.scalar(select(func.count()).select_from(RepetitionInstance)) == 0
    body = {"preview": preview.json(), "resolved_refs": {}}
    first = api_client.post(f"/api/repetitions/{rep_id}/commit-generation", json=body)
    assert first.status_code == 200, first.json()
    second = api_client.post(f"/api/repetitions/{rep_id}/commit-generation", json=body)
    assert second.status_code == 200, second.json()
    assert first.json() == second.json()
    assert len(first.json()["reference_map"]["plans"]) == 2
    with Session(api_db_engine) as session:
        assert session.scalar(select(func.count()).select_from(RepetitionInstance)) == 2
        assert session.scalar(select(func.count()).select_from(RepetitionGenerationReceipt)) == 1


def test_stale_preview_rejected_before_clones(api_client, api_db_engine):
    rep_id, value = create_input(api_client, api_db_engine)
    preview = api_client.post("/api/repetitions/preview-instances", json=value).json()
    assert (
        api_client.patch(
            f"/api/repetitions/{rep_id}/settings", json={"manual_count": 3}
        ).status_code
        == 200
    )
    response = api_client.post(
        f"/api/repetitions/{rep_id}/commit-generation",
        json={"preview": preview, "resolved_refs": {}},
    )
    assert response.status_code == 422, response.json()
    assert response.json()["detail"]["errors"][0]["code"] == "REPETITION_PREVIEW_STALE"
    with Session(api_db_engine) as session:
        assert session.scalar(select(func.count()).select_from(RepetitionInstance)) == 0


def test_draft_reference_resolution_ignores_saved_uuids(api_client, api_db_engine):
    rep_id, value = create_input(api_client, api_db_engine)
    root_id = value["template"]["root_ref"]
    value["repetition_ref"] = "pending:rep"
    value["template"]["root_ref"] = "pending:template"
    value["template"]["nodes"][0]["ref"] = "pending:template"
    preview = api_client.post("/api/repetitions/preview-instances", json=value)
    response = api_client.post(
        f"/api/repetitions/{rep_id}/commit-generation",
        json={
            "preview": preview.json(),
            "resolved_refs": {"pending:rep": rep_id, "pending:template": root_id},
        },
    )
    assert response.status_code == 200, response.json()


def test_omissions_survive_refresh_and_cleanup(api_client, api_db_engine):
    rep_id, value = create_input(api_client, api_db_engine)
    preview = api_client.post("/api/repetitions/preview-instances", json=value).json()
    body = {"preview": preview, "resolved_refs": {}, "omitted_instance_indices": [1]}
    response = api_client.post(f"/api/repetitions/{rep_id}/commit-generation", json=body)
    assert response.status_code == 200, response.json()
    assert len(response.json()["reference_map"]["plans"]) == 1
    assert api_client.post("/api/plans/validate").status_code == 200
    for _ in range(2):
        assert api_client.post(f"/api/repetitions/{rep_id}/refresh").status_code == 200
    with Session(api_db_engine) as session:
        assert session.scalar(select(func.count()).select_from(RepetitionInstance)) == 1
        assert session.scalar(select(func.count()).select_from(RepetitionSkippedOccurrence)) == 1
    assert api_client.delete(f"/api/plans/{rep_id}").status_code == 200
    with Session(api_db_engine) as session:
        assert session.scalar(select(func.count()).select_from(RepetitionGenerationReceipt)) == 0
        assert session.scalar(select(func.count()).select_from(RepetitionSkippedOccurrence)) == 0


def test_deleting_saved_occurrence_does_not_recreate_it(api_client, api_db_engine):
    rep_id, value = create_input(api_client, api_db_engine)
    preview = api_client.post("/api/repetitions/preview-instances", json=value).json()
    body = {"preview": preview, "resolved_refs": {}}
    assert (
        api_client.post(f"/api/repetitions/{rep_id}/commit-generation", json=body).status_code
        == 200
    )
    root = preview["instances"][0]["root_ref"]
    assert api_client.delete(f"/api/plans/{root}").status_code == 200
    assert api_client.post(f"/api/repetitions/{rep_id}/refresh").status_code == 200
    with Session(api_db_engine) as session:
        assert session.get(Plan, UUID(root)) is None
        assert session.scalar(select(func.count()).select_from(RepetitionInstance)) == 1
    assert api_client.post("/api/plans/validate").status_code == 200


def test_deleting_linked_child_detaches_parent_subtree(api_client, api_db_engine):
    rep_id, value = create_input(
        api_client, api_db_engine, template_type="GOAL", template_duration_minutes=None
    )
    template_id = value["template"]["root_ref"]
    for name in ("First", "Second"):
        assert (
            api_client.post(
                f"/api/plans/{template_id}/children",
                json={"kind": "TASK", "name": name, "is_critical": False, "duration_minutes": 30},
            ).status_code
            == 200
        )
    with Session(api_db_engine) as session:
        value = snapshot_repetition(session, session.get(RepetitionPlan, UUID(rep_id))).model_dump(
            mode="json"
        )
    preview = api_client.post("/api/repetitions/preview-instances", json=value).json()
    assert (
        api_client.post(
            f"/api/repetitions/{rep_id}/commit-generation",
            json={"preview": preview, "resolved_refs": {}},
        ).status_code
        == 200
    )
    nodes = preview["instances"][0]["nodes"]
    root, child = nodes[0]["ref"], nodes[1]["ref"]
    assert api_client.delete(f"/api/plans/{child}").status_code == 200
    assert api_client.post(f"/api/repetitions/{rep_id}/refresh").status_code == 200
    assert api_client.get(f"/api/plans/{root}").json()["clone_status"] == "DETACHED"
    with Session(api_db_engine) as session:
        assert session.get(Plan, UUID(child)) is None
        assert len(session.scalars(select(Plan).where(Plan.parent_id == UUID(root))).all()) == 1
    sibling = preview["instances"][1]["root_ref"]
    assert api_client.get(f"/api/plans/{sibling}").json()["clone_status"] == "LINKED"
    assert api_client.post("/api/plans/validate").status_code == 200


def test_commit_rollback_includes_receipt_and_omissions(api_client, api_db_engine, monkeypatch):
    rep_id, value = create_input(api_client, api_db_engine, manual_count=3)
    preview = api_client.post("/api/repetitions/preview-instances", json=value).json()
    original = projection_service.materialize_instance
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("Injected persistence failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(projection_service, "materialize_instance", fail_second)
    response = api_client.post(
        f"/api/repetitions/{rep_id}/commit-generation",
        json={"preview": preview, "resolved_refs": {}, "omitted_instance_indices": [2]},
    )
    assert response.status_code == 422
    with Session(api_db_engine) as session:
        assert session.scalar(select(func.count()).select_from(RepetitionInstance)) == 0
        assert session.scalar(select(func.count()).select_from(RepetitionGenerationReceipt)) == 0
        assert session.scalar(select(func.count()).select_from(RepetitionSkippedOccurrence)) == 0
        assert session.scalar(select(func.count()).select_from(Plan)) == 3


@pytest.mark.parametrize("different_keys", [False, True])
def test_concurrent_generation_has_one_batch(api_client, api_db_engine, different_keys):
    rep_id, value = create_input(api_client, api_db_engine)
    preview = api_client.post("/api/repetitions/preview-instances", json=value).json()
    other = (
        api_client.post("/api/repetitions/preview-instances", json=value).json()
        if different_keys
        else preview
    )
    gate = Barrier(2)

    def commit(p):
        body = CommitGenerationInput.model_validate({"preview": p, "resolved_refs": {}})
        with Session(api_db_engine) as session:
            gate.wait(timeout=5)
            return projection_service.commit_generation(
                session, PlanID(UUID(rep_id)), body, datetime(2026, 9, 12, tzinfo=UTC)
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(commit, [preview, other]))
    assert sum(result.success for result in results) == (1 if different_keys else 2)
    with Session(api_db_engine) as session:
        assert session.scalar(select(func.count()).select_from(RepetitionInstance)) == 2
        assert session.scalar(select(func.count()).select_from(RepetitionGenerationReceipt)) == 1


def test_receipt_key_conflict_does_not_rewrite_existing_batch(api_client, api_db_engine):
    rep_id, value = create_input(api_client, api_db_engine)
    body = {
        "preview": api_client.post("/api/repetitions/preview-instances", json=value).json(),
        "resolved_refs": {},
    }
    assert (
        api_client.post(f"/api/repetitions/{rep_id}/commit-generation", json=body).status_code
        == 200
    )
    body["omitted_instance_indices"] = [1]
    response = api_client.post(f"/api/repetitions/{rep_id}/commit-generation", json=body)
    assert response.status_code == 422
    assert response.json()["detail"]["errors"][0]["code"] == "REPETITION_GENERATION_CONFLICT"


def test_duplicate_windows_retain_individual_mapped_ids(api_client, api_db_engine):
    rep_id, value = create_input(api_client, api_db_engine)
    template_id = value["template"]["root_ref"]
    window = {"start_time": "2026-09-12T16:00:00Z", "end_time": "2026-09-12T17:00:00Z"}
    later = {"start_time": "2026-09-12T18:00:00Z", "end_time": "2026-09-12T20:00:00Z"}
    for _ in range(2):
        response = api_client.post(
            f"/api/plans/{template_id}/constraints/groups", json={"windows": [window, later]}
        )
        assert response.status_code == 200, response.json()
    with Session(api_db_engine) as session:
        value = snapshot_repetition(session, session.get(RepetitionPlan, UUID(rep_id))).model_dump(
            mode="json"
        )
    value["template"]["nodes"][0]["constraint_groups"].reverse()
    preview = api_client.post("/api/repetitions/preview-instances", json=value).json()
    response = api_client.post(
        f"/api/repetitions/{rep_id}/commit-generation",
        json={"preview": preview, "resolved_refs": {}},
    )
    assert response.status_code == 200, response.json()
    mapping = response.json()["reference_map"]
    node = preview["instances"][0]["nodes"][0]
    user_groups = node["constraint_groups"][:2]
    with Session(api_db_engine) as session:
        ids = []
        for group in user_groups:
            for window in group["windows"]:
                window_id = UUID(mapping["windows"][window["ref"]])
                ids.append(window_id)
                assert (
                    str(session.get(TimeWindow, window_id).group_id)
                    == mapping["groups"][group["ref"]]
                )
        assert len(set(ids)) == 4
    assert api_client.post("/api/plans/validate").status_code == 200


def test_frozen_horizon_allows_clock_advance_but_not_settings_change(api_client, api_db_engine):
    assert (
        api_client.patch("/api/settings", json={"master_horizon_duration": {"days": 2}}).status_code
        == 200
    )
    rep_id, value = create_input(
        api_client,
        api_db_engine,
        repeat_mode="DATE_RANGE",
        manual_count=None,
        start_time="2026-06-07T10:00:00Z",
    )
    preview = api_client.post("/api/repetitions/preview-instances", json=value).json()
    assert len(preview["instances"]) == 2
    body = CommitGenerationInput.model_validate({"preview": preview, "resolved_refs": {}})
    with Session(api_db_engine) as session:
        result = projection_service.commit_generation(
            session, PlanID(UUID(rep_id)), body, datetime(2026, 6, 8, 10, tzinfo=UTC)
        )
        assert result.success, result.errors
    rep_id2, value2 = create_input(
        api_client,
        api_db_engine,
        repeat_mode="DATE_RANGE",
        manual_count=None,
        start_time="2026-06-07T10:00:00Z",
    )
    preview2 = api_client.post("/api/repetitions/preview-instances", json=value2).json()
    assert (
        api_client.patch("/api/settings", json={"master_horizon_duration": {"days": 3}}).status_code
        == 200
    )
    response = api_client.post(
        f"/api/repetitions/{rep_id2}/commit-generation",
        json={"preview": preview2, "resolved_refs": {}},
    )
    assert response.status_code == 422


def test_rename_detaches_only_edited_instance_and_survives_refresh(api_client, api_db_engine):
    rep_id, value = create_input(api_client, api_db_engine)
    preview = api_client.post("/api/repetitions/preview-instances", json=value).json()
    assert (
        api_client.post(
            f"/api/repetitions/{rep_id}/commit-generation",
            json={"preview": preview, "resolved_refs": {}},
        ).status_code
        == 200
    )
    edited = preview["instances"][0]["root_ref"]
    sibling = preview["instances"][1]["root_ref"]
    assert (
        api_client.patch(f"/api/plans/{edited}/rename", json={"name": "Custom lunch"}).status_code
        == 200
    )
    assert (
        api_client.patch(
            f"/api/plans/{value['template']['root_ref']}/rename", json={"name": "New template"}
        ).status_code
        == 200
    )
    assert api_client.post(f"/api/repetitions/{rep_id}/refresh").status_code == 200
    detail = api_client.get(f"/api/plans/{edited}").json()
    assert detail["name"] == "Custom lunch" and detail["clone_status"] == "DETACHED"
    detail = api_client.get(f"/api/plans/{sibling}").json()
    assert detail["name"] == "New template" and detail["clone_status"] == "LINKED"


def test_instance_move_preserves_occurrence_index_and_dense_buckets(api_client, api_db_engine):
    rep_id, value = create_input(api_client, api_db_engine, manual_count=3)
    preview = api_client.post("/api/repetitions/preview-instances", json=value).json()
    assert (
        api_client.post(
            f"/api/repetitions/{rep_id}/commit-generation",
            json={"preview": preview, "resolved_refs": {}},
        ).status_code
        == 200
    )
    root = preview["instances"][2]["root_ref"]
    assert api_client.post(f"/api/plans/{root}/move", json={"position": 0}).status_code == 200
    detail = api_client.get(f"/api/plans/{root}").json()
    assert detail["repetition_instance"] == {
        "repetition_plan_id": rep_id,
        "instance_index": 2,
        "is_critical": False,
        "sort_order": 0,
    }
    assert (
        api_client.post(
            f"/api/plans/{root}/move", json={"position": -1, "is_critical": True}
        ).status_code
        == 200
    )
    assert api_client.post(f"/api/repetitions/{rep_id}/refresh").status_code == 200
    detail = api_client.get(f"/api/plans/{root}").json()
    assert detail["repetition_instance"]["is_critical"] is True
    assert detail["repetition_instance"]["instance_index"] == 2
    assert api_client.post("/api/plans/validate").status_code == 200


def test_nested_repetition_and_template_refs_are_mapped(api_client, api_db_engine):
    rep_id, value = create_input(
        api_client, api_db_engine, template_type="GOAL", template_duration_minutes=None
    )
    template_id = value["template"]["root_ref"]
    response = api_client.post(
        f"/api/plans/{template_id}/children",
        json={
            "kind": "REPETITION",
            "name": "Nested",
            "is_critical": False,
            "repeat_mode": "MANUAL_COUNT",
            "start_time": "2026-09-12T05:00:00Z",
            "repeat_interval_minutes": 60,
            "manual_count": 2,
            "template_type": "TASK",
            "template_name": "Nested task",
            "template_duration_minutes": 15,
        },
    )
    assert response.status_code == 200, response.json()
    with Session(api_db_engine) as session:
        value = snapshot_repetition(session, session.get(RepetitionPlan, UUID(rep_id))).model_dump(
            mode="json"
        )
    preview = api_client.post("/api/repetitions/preview-instances", json=value).json()
    response = api_client.post(
        f"/api/repetitions/{rep_id}/commit-generation",
        json={"preview": preview, "resolved_refs": {}},
    )
    assert response.status_code == 200, response.json()
    for instance in preview["instances"]:
        nested = next(node for node in instance["nodes"] if node["kind"] == "REPETITION")
        detail = api_client.get(f"/api/plans/{nested['ref']}").json()
        assert detail["repetition_detail"]["template_root_id"] == nested["template_root_ref"]
    assert api_client.post("/api/plans/validate").status_code == 200
    blockers = api_client.post("/api/schedule/refresh")
    assert blockers.status_code == 422
    assert len(blockers.json()["detail"]["errors"]) == 2


@pytest.mark.slow
def test_fourteen_day_schedule_with_omitted_and_deleted_instances(
    lunch_api_client, api_db_engine, monkeypatch
):
    original_solver = cp_model.CpSolver

    def single_worker():
        solver = original_solver()
        solver.parameters.num_search_workers = 1
        return solver

    monkeypatch.setattr(cp_model, "CpSolver", single_worker)
    client = lunch_api_client
    assert (
        client.patch(
            "/api/settings",
            json={
                "master_horizon_duration": {"days": 14},
                "exact_solver_time_limit_seconds": 2,
                "local_timezone": "America/Chicago",
            },
        ).status_code
        == 200
    )
    rep_id, value = create_input(client, api_db_engine, manual_count=14)
    template_id = value["template"]["root_ref"]
    window = {"start_time": "2026-09-12T16:00:00Z", "end_time": "2026-09-12T20:00:00Z"}
    assert (
        client.post(
            f"/api/plans/{template_id}/constraints/groups", json={"windows": [window]}
        ).status_code
        == 200
    )
    with Session(api_db_engine) as session:
        value = snapshot_repetition(session, session.get(RepetitionPlan, UUID(rep_id))).model_dump(
            mode="json"
        )
    preview = client.post("/api/repetitions/preview-instances", json=value).json()
    response = client.post(
        f"/api/repetitions/{rep_id}/commit-generation",
        json={"preview": preview, "resolved_refs": {}, "omitted_instance_indices": [5]},
    )
    assert response.status_code == 200, response.json()
    assert client.post("/api/plans/validate").status_code == 200
    deleted = preview["instances"][2]["root_ref"]
    assert client.delete(f"/api/plans/{deleted}").status_code == 200
    for _ in range(2):
        assert client.post(f"/api/repetitions/{rep_id}/refresh").status_code == 200
    validation = client.post("/api/plans/validate")
    assert validation.status_code == 200, validation.json()
    response = client.post("/api/schedule/refresh")
    assert response.status_code == 200, response.json()
    entries = client.get("/api/calendar/tasks").json()["entries"]
    assert len(entries) == 12
    assert {datetime.fromisoformat(entry["start_time"]).date() for entry in entries} == {
        datetime(2026, 9, 12).date() + timedelta(days=index)
        for index in range(14)
        if index not in (2, 5)
    }
