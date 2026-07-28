"""Pure template-trace computation per V2 design §6.1."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from calendar_backend.domain.ids import PlanID
from calendar_backend.models.plans import Plan


@dataclass(frozen=True)
class TemplateTraceStep:
    repetition_plan_id: PlanID
    repeat_interval_minutes: int


TemplateTrace = tuple[TemplateTraceStep, ...]


def _repetition_shell_by_template_root(
    plans_by_id: dict[uuid.UUID, Plan],
) -> dict[uuid.UUID, Plan]:
    shells_by_template_root: dict[uuid.UUID, Plan] = {}
    for plan in plans_by_id.values():
        repetition_plan = plan.repetition_plan
        if repetition_plan is None:
            continue
        shells_by_template_root[repetition_plan.template_root_id] = plan
    return shells_by_template_root


def compute_template_trace(
    plan_id: PlanID | uuid.UUID,
    plans_by_id: dict[uuid.UUID, Plan],
) -> TemplateTrace:
    current_id = uuid.UUID(str(plan_id))
    shells_by_template_root = _repetition_shell_by_template_root(plans_by_id)
    steps: list[TemplateTraceStep] = []
    visited: set[uuid.UUID] = set()
    jumped_to_shell = False

    while True:
        if current_id in visited:
            raise ValueError(f"template trace cycle detected at plan {current_id}")
        visited.add(current_id)

        plan = plans_by_id.get(current_id)
        if plan is None:
            raise ValueError(f"template trace missing plan {current_id}")

        if plan.is_master:
            break

        shell = shells_by_template_root.get(current_id)
        if shell is not None:
            repetition_plan = shell.repetition_plan
            if repetition_plan is None:
                raise ValueError(
                    f"template root {current_id} maps to repetition shell {shell.plan_id} "
                    "without repetition_plan"
                )
            steps.append(
                TemplateTraceStep(
                    repetition_plan_id=PlanID(shell.plan_id),
                    repeat_interval_minutes=repetition_plan.repeat_interval_minutes,
                )
            )
            current_id = shell.plan_id
            jumped_to_shell = True
            continue

        if plan.repetition_plan is not None and not jumped_to_shell:
            steps.append(
                TemplateTraceStep(
                    repetition_plan_id=PlanID(plan.plan_id),
                    repeat_interval_minutes=plan.repetition_plan.repeat_interval_minutes,
                )
            )

        jumped_to_shell = False
        if plan.parent_id is None:
            break
        current_id = plan.parent_id

    return tuple(steps)


def traces_match(left: TemplateTrace, right: TemplateTrace) -> bool:
    return left == right
