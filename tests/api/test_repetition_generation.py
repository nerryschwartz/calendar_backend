"""Preview is read-only; commit is keyed, atomic, and safe to retry."""

from uuid import UUID

from calendar_backend.models.plans import Plan, RepetitionPlan
from calendar_backend.models.repetitions import RepetitionGenerationReceipt, RepetitionInstance
from calendar_backend.models.settings import AppSettings
from calendar_backend.services.repetition_projection import snapshot_repetition
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def create_input(client, engine):
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
