"""Pure tests for plan prerequisite validation helpers."""

from __future__ import annotations

import uuid

from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.prerequisites import would_create_prerequisite_cycle


def _plan_id(label: str) -> PlanID:
    return PlanID(uuid.uuid5(uuid.NAMESPACE_DNS, label))


def test_would_create_prerequisite_cycle_detects_self_edge() -> None:
    plan_id = _plan_id("self")
    assert (
        would_create_prerequisite_cycle(
            (),
            dependent_id=plan_id,
            prerequisite_id=plan_id,
        )
        is True
    )


def test_would_create_prerequisite_cycle_detects_three_cycle() -> None:
    plan_a = _plan_id("a")
    plan_b = _plan_id("b")
    plan_c = _plan_id("c")
    existing = (
        (plan_a, plan_b),
        (plan_b, plan_c),
    )
    assert (
        would_create_prerequisite_cycle(
            existing,
            dependent_id=plan_c,
            prerequisite_id=plan_a,
        )
        is True
    )


def test_would_create_prerequisite_cycle_allows_dag_extension() -> None:
    plan_a = _plan_id("a")
    plan_b = _plan_id("b")
    plan_c = _plan_id("c")
    existing = ((plan_a, plan_b),)
    assert (
        would_create_prerequisite_cycle(
            existing,
            dependent_id=plan_c,
            prerequisite_id=plan_a,
        )
        is False
    )
