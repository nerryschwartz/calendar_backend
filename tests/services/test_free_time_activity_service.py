"""Integration tests for FreeTimeActivityService."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from calendar_backend.db.session import transaction
from calendar_backend.domain.enums import CalendarEntryType
from calendar_backend.domain.errors import MessageCode
from calendar_backend.domain.free_time_draft import FreeTimeDraftBody
from calendar_backend.domain.ids import FreeTimeActivityID, FreeTimeActivityPrerequisiteID, PlanID
from calendar_backend.domain.task_families import DEFAULT_BLOCK_FAMILY, FREE_TIME_BLOCK_FAMILY
from calendar_backend.models.calendar import CalendarEntry
from calendar_backend.models.free_time import FreeTimeActivity, FreeTimeActivityPrerequisite
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.free_time_activity import (
    FreeTimeActivityService,
    cleanup_orphaned_activities_after_plan_delete,
)
from calendar_backend.services.free_time_draft import FreeTimeDraftService
from calendar_backend.services.master_horizon import MasterHorizonService
from calendar_backend.services.master_plan import MasterPlanService
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .conftest import FakeClock

RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize("mode", ["direct", "draft", "rollback"])
def test_delete_activity_cleans_future_rows_and_preserves_history(
    service_db_session: Session, mode: str
) -> None:
    master_id = _bootstrap_master(service_db_session)
    service = _service(service_db_session)
    created = service.create_activity("reading", Decimal("1"), 0)
    assert created.success and created.value is not None
    activity_id = created.value.free_time_activity_id
    assert service.add_prerequisite(activity_id, master_id).success
    entry_ids: list[uuid.UUID] = []
    with transaction(service_db_session) as txn:
        for minutes in (-60, -15, 0, 60):
            entry_id = uuid.uuid4()
            entry_ids.append(entry_id)
            txn.add(
                CalendarEntry(
                    calendar_entry_id=entry_id,
                    entry_type=CalendarEntryType.FREE_TIME,
                    start_time=RUN_AT + timedelta(minutes=minutes),
                    end_time=RUN_AT + timedelta(minutes=minutes + 30),
                    source_free_time_activity_id=activity_id,
                    display_label="reading",
                    created_at=RUN_AT,
                    updated_at=RUN_AT,
                )
            )
    if mode == "direct":
        result = service.delete_activity(activity_id)
    else:
        edits = [{"op": "delete", "activity_ref": str(activity_id)}]
        if mode == "rollback":
            edits.append({"op": "update", "activity_ref": "draft:missing", "name": "failure"})
        result = FreeTimeDraftService(service_db_session, _clock()).apply_edits(
            FreeTimeDraftBody.model_validate({"edits": edits}).edits
        )
    service_db_session.commit()
    if mode == "rollback":
        assert not result.success
        assert service_db_session.get(FreeTimeActivity, activity_id) is not None
        assert (
            service_db_session.scalar(
                select(func.count()).select_from(FreeTimeActivityPrerequisite)
            )
            == 1
        )
        for entry_id in entry_ids:
            row = service_db_session.get(CalendarEntry, entry_id)
            assert row is not None and row.source_free_time_activity_id == activity_id
    else:
        assert result.success
        assert service_db_session.get(FreeTimeActivity, activity_id) is None
        assert (
            service_db_session.scalar(
                select(func.count()).select_from(FreeTimeActivityPrerequisite)
            )
            == 0
        )
        for entry_id in entry_ids[:2]:
            row = service_db_session.get(CalendarEntry, entry_id)
            assert row is not None and row.source_free_time_activity_id is None
            assert row.display_label == "reading"
        for entry_id in entry_ids[2:]:
            assert service_db_session.get(CalendarEntry, entry_id) is None


def test_delete_activity_rejects_invalid_remaining_fractions(service_db_session: Session) -> None:
    draft = FreeTimeDraftBody.model_validate(
        {
            "edits": [
                {
                    "op": "create",
                    "draft_ref": ref,
                    "name": ref,
                    "real_fraction": "0.5",
                    "minimum_block_size_minutes": 0,
                }
                for ref in ("draft:a", "draft:b")
            ]
        }
    )
    created = FreeTimeDraftService(service_db_session, _clock()).apply_edits(draft.edits)
    assert created.success and created.value is not None
    activity_id = created.value.activities[0].free_time_activity_id
    result = _service(service_db_session).delete_activity(activity_id)
    assert not result.success
    assert result.errors[0].code == MessageCode.INVALID_FREE_TIME_FRACTIONS
    assert service_db_session.get(FreeTimeActivity, activity_id) is not None


def _clock() -> FakeClock:
    return FakeClock(RUN_AT)


def _service(session: Session) -> FreeTimeActivityService:
    return FreeTimeActivityService(session, _clock())


def _bootstrap_master(session: Session) -> PlanID:
    clock = _clock()
    master = MasterPlanService(session, clock).ensure_master_exists()
    assert master.success and master.value is not None
    AppSettingsService(session, clock).get_settings()
    MasterHorizonService(session, clock).refresh_master_horizon(RUN_AT)
    return master.value.plan_id


@pytest.mark.integration
def test_create_activity_persists_enabled_fraction_one(service_db_session: Session) -> None:
    result = _service(service_db_session).create_activity(
        "reading",
        Decimal("1"),
        minimum_block_size_minutes=15,
    )

    assert result.success and result.value is not None
    dto = result.value
    assert dto.name == "reading"
    assert dto.enabled is True
    assert dto.real_fraction == Decimal("1")
    assert dto.minimum_block_size_minutes == 15
    assert dto.prerequisite_plan_ids == ()


@pytest.mark.integration
def test_create_second_activity_rejects_fraction_sum_not_one(service_db_session: Session) -> None:
    service = _service(service_db_session)
    assert service.create_activity("reading", Decimal("1"), minimum_block_size_minutes=0).success

    result = service.create_activity("gaming", Decimal("0.5"), minimum_block_size_minutes=0)

    assert not result.success
    assert any(error.code == MessageCode.INVALID_FREE_TIME_FRACTIONS for error in result.errors)


@pytest.mark.integration
def test_update_activity_rejects_invalid_fraction_sum(service_db_session: Session) -> None:
    service = _service(service_db_session)
    created = service.create_activity("reading", Decimal("1"), minimum_block_size_minutes=0)
    assert created.success and created.value is not None

    result = service.update_activity(
        created.value.free_time_activity_id,
        real_fraction=Decimal("0.5"),
    )

    assert not result.success
    assert any(error.code == MessageCode.INVALID_FREE_TIME_FRACTIONS for error in result.errors)
    service_db_session.commit()
    reloaded = service.get_activity(created.value.free_time_activity_id)
    assert reloaded.success and reloaded.value is not None
    assert reloaded.value.real_fraction == Decimal("1")


def test_failed_enable_does_not_persist(service_db_session: Session) -> None:
    service = _service(service_db_session)
    created = service.create_activity("reading", Decimal("0.5"), 0, enabled=False)
    assert created.success and created.value is not None
    assert not service.set_enabled(created.value.free_time_activity_id, True).success
    service_db_session.commit()
    reloaded = service.get_activity(created.value.free_time_activity_id)
    assert reloaded.success and reloaded.value is not None
    assert not reloaded.value.enabled


@pytest.mark.integration
def test_set_enabled_rejects_zero_fraction_when_enabling(service_db_session: Session) -> None:
    service = _service(service_db_session)
    created = service.create_activity(
        "reading",
        Decimal("1"),
        minimum_block_size_minutes=0,
        enabled=False,
    )
    assert created.success and created.value is not None
    updated = service.update_activity(
        created.value.free_time_activity_id,
        real_fraction=Decimal("0"),
    )
    assert updated.success

    result = service.set_enabled(created.value.free_time_activity_id, True)

    assert not result.success
    assert any(error.code == MessageCode.INVALID_FREE_TIME_FRACTIONS for error in result.errors)


@pytest.mark.integration
def test_add_prerequisite_rejects_missing_plan(service_db_session: Session) -> None:
    created = _service(service_db_session).create_activity(
        "reading",
        Decimal("1"),
        minimum_block_size_minutes=0,
    )
    assert created.success and created.value is not None

    result = _service(service_db_session).add_prerequisite(
        created.value.free_time_activity_id,
        PlanID(uuid.uuid4()),
    )

    assert not result.success
    assert any(error.code == MessageCode.PLAN_NOT_FOUND for error in result.errors)


@pytest.mark.integration
def test_add_prerequisite_rejects_duplicate(service_db_session: Session) -> None:
    master_id = _bootstrap_master(service_db_session)
    created = _service(service_db_session).create_activity(
        "reading",
        Decimal("1"),
        minimum_block_size_minutes=0,
    )
    assert created.success and created.value is not None
    service = _service(service_db_session)
    first = service.add_prerequisite(created.value.free_time_activity_id, master_id)
    assert first.success

    second = service.add_prerequisite(created.value.free_time_activity_id, master_id)

    assert not second.success
    assert any(
        error.code == MessageCode.DUPLICATE_FREE_TIME_PREREQUISITE for error in second.errors
    )


@pytest.mark.integration
def test_remove_prerequisite_updates_activity(service_db_session: Session) -> None:
    master_id = _bootstrap_master(service_db_session)
    service = _service(service_db_session)
    created = service.create_activity("reading", Decimal("1"), minimum_block_size_minutes=0)
    assert created.success and created.value is not None
    added = service.add_prerequisite(created.value.free_time_activity_id, master_id)
    assert added.success and added.value is not None
    prerequisite_id = service_db_session.scalar(
        select(FreeTimeActivityPrerequisite.prerequisite_id).where(
            FreeTimeActivityPrerequisite.free_time_activity_id
            == created.value.free_time_activity_id
        )
    )
    assert prerequisite_id is not None

    removed = service.remove_prerequisite(
        created.value.free_time_activity_id,
        FreeTimeActivityPrerequisiteID(prerequisite_id),
    )

    assert removed.success and removed.value is not None
    assert removed.value.prerequisite_plan_ids == ()


@pytest.mark.integration
def test_list_activities_returns_sorted_rows(service_db_session: Session) -> None:
    service = _service(service_db_session)
    assert service.create_activity(
        "beta",
        Decimal("0.5"),
        minimum_block_size_minutes=0,
        enabled=False,
    ).success
    assert service.create_activity(
        "alpha",
        Decimal("0.5"),
        minimum_block_size_minutes=0,
        enabled=False,
    ).success

    result = service.list_activities()

    assert result.success and result.value is not None
    assert {dto.name for dto in result.value} == {"alpha", "beta"}


@pytest.mark.integration
def test_cleanup_orphaned_activities_deletes_when_no_calendar_refs(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master(service_db_session)
    service = _service(service_db_session)
    created = service.create_activity("reading", Decimal("1"), minimum_block_size_minutes=0)
    assert created.success and created.value is not None
    assert service.add_prerequisite(created.value.free_time_activity_id, master_id).success
    activity_id = created.value.free_time_activity_id

    with transaction(service_db_session) as txn:
        txn.execute(
            delete(FreeTimeActivityPrerequisite).where(
                FreeTimeActivityPrerequisite.free_time_activity_id == activity_id
            )
        )
        cleanup_orphaned_activities_after_plan_delete(
            txn,
            (activity_id,),
            updated_at=RUN_AT,
        )

    assert service_db_session.get(FreeTimeActivity, activity_id) is None


@pytest.mark.integration
def test_cleanup_orphaned_activities_disables_when_calendar_refs_exist(
    service_db_session: Session,
) -> None:
    master_id = _bootstrap_master(service_db_session)
    service = _service(service_db_session)
    created = service.create_activity("reading", Decimal("1"), minimum_block_size_minutes=0)
    assert created.success and created.value is not None
    assert service.add_prerequisite(created.value.free_time_activity_id, master_id).success
    activity_id = created.value.free_time_activity_id

    with transaction(service_db_session) as txn:
        txn.add(
            CalendarEntry(
                calendar_entry_id=uuid.uuid4(),
                entry_type=CalendarEntryType.FREE_TIME,
                start_time=RUN_AT,
                end_time=RUN_AT + timedelta(hours=1),
                source_plan_id=None,
                source_free_time_activity_id=activity_id,
                calendar_run_id=None,
                display_label="reading",
                created_at=RUN_AT,
                updated_at=RUN_AT,
            )
        )
        txn.flush()
        txn.execute(
            delete(FreeTimeActivityPrerequisite).where(
                FreeTimeActivityPrerequisite.free_time_activity_id == activity_id
            )
        )
        cleanup_orphaned_activities_after_plan_delete(
            txn,
            (activity_id,),
            updated_at=RUN_AT,
        )

    row = service_db_session.get(FreeTimeActivity, activity_id)
    assert row is not None
    assert row.enabled is False


@pytest.mark.integration
def test_get_activity_returns_not_found_for_missing_id(service_db_session: Session) -> None:
    result = _service(service_db_session).get_activity(FreeTimeActivityID(uuid.uuid4()))

    assert not result.success
    assert any(error.code == MessageCode.FREE_TIME_ACTIVITY_NOT_FOUND for error in result.errors)


@pytest.mark.integration
def test_all_disabled_fraction_configuration_is_allowed(service_db_session: Session) -> None:
    service = _service(service_db_session)
    first = service.create_activity(
        "reading",
        Decimal("1"),
        minimum_block_size_minutes=0,
        enabled=False,
    )
    second = service.create_activity(
        "gaming",
        Decimal("1"),
        minimum_block_size_minutes=0,
        enabled=False,
    )

    assert first.success
    assert second.success
    count = service_db_session.scalar(select(func.count()).select_from(FreeTimeActivity))
    assert count == 2


@pytest.mark.integration
def test_set_allowed_block_families_persists_and_returns_effective(
    service_db_session: Session,
) -> None:
    created = _service(service_db_session).create_activity(
        "reading",
        Decimal("1"),
        minimum_block_size_minutes=30,
    )
    assert created.success and created.value is not None
    activity_id = created.value.free_time_activity_id

    result = _service(service_db_session).set_allowed_block_families(activity_id, ("transit",))
    assert result.success and result.value is not None
    assert result.value.allowed_block_families == (FREE_TIME_BLOCK_FAMILY, "transit")

    row = service_db_session.get(FreeTimeActivity, activity_id)
    assert row is not None
    assert row.allowed_block_families == '["transit"]'


@pytest.mark.integration
def test_clear_allowed_block_families_stores_null(service_db_session: Session) -> None:
    created = _service(service_db_session).create_activity(
        "reading",
        Decimal("1"),
        minimum_block_size_minutes=30,
    )
    assert created.success and created.value is not None
    activity_id = created.value.free_time_activity_id
    service = _service(service_db_session)
    assert service.set_allowed_block_families(activity_id, ("transit",)).success

    result = service.clear_allowed_block_families(activity_id)
    assert result.success and result.value is not None
    assert result.value.allowed_block_families == (
        FREE_TIME_BLOCK_FAMILY,
        DEFAULT_BLOCK_FAMILY,
    )

    row = service_db_session.get(FreeTimeActivity, activity_id)
    assert row is not None
    assert row.allowed_block_families is None
