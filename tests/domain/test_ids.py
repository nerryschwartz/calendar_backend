from __future__ import annotations

from uuid import UUID, uuid4

from calendar_backend.domain.ids import PlanID, RepetitionInstanceID, new_id


def test_new_id_generates_unique_plan_ids() -> None:
    ids = {new_id(PlanID) for _ in range(20)}
    assert len(ids) == 20


def test_new_id_works_for_other_id_types() -> None:
    instance_id = new_id(RepetitionInstanceID)
    assert isinstance(instance_id, UUID)


def test_plan_id_cast_from_uuid() -> None:
    raw = uuid4()
    plan_id = PlanID(raw)
    assert plan_id == raw
    assert isinstance(plan_id, UUID)


def test_distinct_newtypes_are_not_interchangeable_at_type_level() -> None:
    plan_id = new_id(PlanID)
    instance_id = RepetitionInstanceID(plan_id)
    assert instance_id == plan_id
