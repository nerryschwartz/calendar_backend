"""Read-only snapshot adapter for pure repetition generation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from calendar_backend.db.session import transaction
from calendar_backend.domain.calendar_duration import CalendarDuration
from calendar_backend.domain.dtos import repetition_plan_dto_from_rows
from calendar_backend.domain.enums import CloneStatus, ConstraintKind, PlanKind, RepeatMode
from calendar_backend.domain.errors import MessageCode, ServiceMessage, ServiceTransactionAborted
from calendar_backend.domain.ids import PlanID
from calendar_backend.domain.repetition_projection import (
    CommitGenerationInput,
    FrozenHorizon,
    GenerationPreview,
    PreviewInput,
    ProjectedInstance,
    ProjectionGroup,
    ProjectionNode,
    ProjectionSettings,
    ProjectionTemplate,
    ProjectionWindow,
    generated_ref,
    project_instances,
)
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.task_families import (
    effective_allowed_block_families,
    serialize_allowed_block_families,
)
from calendar_backend.domain.time import sqlite_utc, truncate_to_minute
from calendar_backend.models.blocks import BlockPlan
from calendar_backend.models.constraints import TimeConstraintGroup, TimeWindow
from calendar_backend.models.plans import GoalPlan, Plan, RepetitionPlan, TaskPlan
from calendar_backend.models.prerequisites import PlanPrerequisite
from calendar_backend.models.repetitions import RepetitionGenerationReceipt, RepetitionInstance
from calendar_backend.models.settings import AppSettings
from calendar_backend.services.app_settings import (
    DEFAULT_LOCAL_TIMEZONE,
    DEFAULT_MASTER_HORIZON_DURATION,
)


def snapshot_settings(row: RepetitionPlan) -> ProjectionSettings:
    return ProjectionSettings(
        repeat_mode=row.repeat_mode,
        start_time=sqlite_utc(row.start_time),
        repeat_interval_minutes=row.repeat_interval_minutes,
        manual_count=row.manual_count,
        end_time=sqlite_utc(row.end_time) if row.end_time is not None else None,
        default_instance_critical=row.default_instance_critical,
    )


def read_frozen_horizon(session: Session, now: datetime) -> FrozenHorizon:
    # Read the singleton directly: get_settings() would create it on an empty DB.
    with session.no_autoflush:
        settings = session.get(AppSettings, 1)
        duration = (
            CalendarDuration(**settings.master_horizon_duration)
            if settings
            else DEFAULT_MASTER_HORIZON_DURATION
        )
        timezone = settings.local_timezone if settings else DEFAULT_LOCAL_TIMEZONE
        start = truncate_to_minute(now)
        return FrozenHorizon(
            run_started_at=start, master_horizon_end=duration.end_at(start, timezone)
        )


def snapshot_repetition(session: Session, repetition: RepetitionPlan) -> PreviewInput:
    nodes = []
    remaining = [repetition.template_root_id]
    seen = set()
    while remaining:
        plan_id = remaining.pop(0)
        if plan_id in seen:
            continue
        seen.add(plan_id)
        plan = session.get(Plan, plan_id)
        if plan is None:
            raise ValueError("Repetition template node no longer exists")
        remaining.extend(
            session.scalars(
                select(Plan.plan_id)
                .where(Plan.parent_id == plan_id)
                .order_by(Plan.goal_sort_order, Plan.plan_id)
            )
        )
        groups = []
        for group in session.scalars(
            select(TimeConstraintGroup)
            .where(
                TimeConstraintGroup.plan_id == plan_id,
                TimeConstraintGroup.constraint_kind == ConstraintKind.USER,
            )
            .order_by(TimeConstraintGroup.time_constraint_group_id)
        ):
            groups.append(
                ProjectionGroup(
                    ref=str(group.time_constraint_group_id),
                    windows=[
                        ProjectionWindow(
                            ref=str(window.time_window_id),
                            start_time=sqlite_utc(window.start_time),
                            end_time=sqlite_utc(window.end_time),
                        )
                        for window in session.scalars(
                            select(TimeWindow)
                            .where(TimeWindow.group_id == group.time_constraint_group_id)
                            .order_by(TimeWindow.time_window_id)
                        )
                    ],
                )
            )
        node = ProjectionNode.model_construct(
            ref=str(plan_id),
            parent_ref=str(plan.parent_id) if plan_id != repetition.template_root_id else None,
            kind=plan.plan_kind,
            name=plan.name,
            is_critical=plan.goal_is_critical,
            sort_order=plan.goal_sort_order,
            constraint_groups=groups,
            prerequisite_refs=[
                str(ref)
                for ref in session.scalars(
                    select(PlanPrerequisite.prerequisite_plan_id)
                    .where(PlanPrerequisite.plan_id == plan_id)
                    .order_by(PlanPrerequisite.prerequisite_plan_id)
                )
            ],
        )
        subtype = (
            session.get(TaskPlan if plan.plan_kind == PlanKind.TASK else BlockPlan, plan_id)
            if plan.plan_kind in (PlanKind.TASK, PlanKind.BLOCK)
            else None
        )
        if subtype is not None:
            node.duration_minutes = subtype.duration_minutes
            node.divisible = subtype.divisible
            node.minimum_chunk_size_minutes = subtype.minimum_chunk_size_minutes
            node.immediate_prerequisite_ref = (
                str(subtype.immediate_prerequisite_plan_id)
                if subtype.immediate_prerequisite_plan_id
                else None
            )
            if isinstance(subtype, TaskPlan):
                node.allowed_block_families = list(
                    effective_allowed_block_families(subtype.allowed_block_families)
                )
            else:
                node.block_family = subtype.block_family
        nested = (
            session.get(RepetitionPlan, plan_id) if plan.plan_kind == PlanKind.REPETITION else None
        )
        if nested is not None:
            node.repetition = snapshot_settings(nested)
            node.template_root_ref = str(nested.template_root_id)
        nodes.append(node)
    return PreviewInput(
        repetition_ref=str(repetition.plan_id),
        settings=snapshot_settings(repetition),
        template=ProjectionTemplate(root_ref=str(repetition.template_root_id), nodes=nodes),
    )


def semantic_input(value: PreviewInput, resolved_refs: dict[str, str]) -> dict:
    """Constraint identities/order are not semantics, but duplicate groups are retained."""
    result = value.model_dump(mode="json")

    def resolve(ref):
        return resolved_refs.get(ref, ref)

    result["repetition_ref"] = resolve(value.repetition_ref)
    result["template"]["root_ref"] = resolve(value.template.root_ref)
    for node in result["template"]["nodes"]:
        is_root = node["ref"] == value.template.root_ref
        for field in ("ref", "parent_ref", "immediate_prerequisite_ref", "template_root_ref"):
            node[field] = resolve(node[field])
        if is_root:
            node["parent_ref"] = None
            node["is_critical"] = node["sort_order"] = None
        node["prerequisite_refs"] = sorted(resolve(ref) for ref in node["prerequisite_refs"])
        groups = []
        for group in node["constraint_groups"]:
            groups.append(sorted([(w["start_time"], w["end_time"]) for w in group["windows"]]))
        node["constraint_groups"] = sorted(groups)
        node["allowed_block_families"] = sorted(node["allowed_block_families"])
    result["template"]["nodes"].sort(key=lambda node: node["ref"])
    return result


def preview_generation(session: Session, value: PreviewInput, now: datetime) -> GenerationPreview:
    horizon = read_frozen_horizon(session, now)
    if value.settings.repeat_mode != RepeatMode.DATE_RANGE or value.settings.end_time is not None:
        horizon.master_horizon_end = None
    return project_instances(value, horizon)


def materialize_instance(
    session: Session,
    value: PreviewInput,
    instance: ProjectedInstance,
    generation_key: str,
    resolved_refs: dict[str, str],
    now: datetime,
) -> dict[str, dict[str, str]]:
    """Persist one authoritative projection; generated keys are also its stable UUIDs."""
    refs = {"plans": {}, "groups": {}, "windows": {}}
    source_by_clone = {
        generated_ref(generation_key, instance.instance_index, node.ref): resolved_refs.get(
            node.ref, node.ref
        )
        for node in value.template.nodes
    }
    repetition_id = UUID(resolved_refs.get(value.repetition_ref, value.repetition_ref))
    by_ref = {node.ref: node for node in instance.nodes}
    pending = list(instance.nodes)
    while pending:
        ready = [
            node
            for node in pending
            if node.parent_ref not in by_ref or node.parent_ref in refs["plans"]
        ]
        if not ready:
            raise ValueError("Projected plan tree contains a cycle")
        for node in ready:
            parent_id = UUID(node.parent_ref) if node.parent_ref in by_ref else repetition_id
            parent_is_goal = (
                node.parent_ref in by_ref and by_ref[node.parent_ref].kind == PlanKind.GOAL
            )
            session.add(
                Plan(
                    plan_id=UUID(node.ref),
                    plan_kind=node.kind,
                    name=node.name,
                    parent_id=parent_id,
                    is_master=False,
                    cloned_from_id=UUID(source_by_clone[node.ref]),
                    clone_status=CloneStatus.LINKED,
                    goal_is_critical=node.is_critical if parent_is_goal else None,
                    goal_sort_order=node.sort_order if parent_is_goal else None,
                    created_at=now,
                    updated_at=now,
                )
            )
            refs["plans"][node.ref] = node.ref
            pending.remove(node)
        session.flush()

    for node in instance.nodes:
        plan_id = UUID(node.ref)
        immediate = node.immediate_prerequisite_ref
        immediate_id = UUID(resolved_refs.get(immediate, immediate)) if immediate else None
        if node.kind == PlanKind.GOAL:
            session.add(GoalPlan(plan_id=plan_id))
        elif node.kind in (PlanKind.TASK, PlanKind.BLOCK):
            fields = dict(
                plan_id=plan_id,
                duration_minutes=node.duration_minutes,
                divisible=node.divisible,
                minimum_chunk_size_minutes=node.minimum_chunk_size_minutes,
                immediate_prerequisite_plan_id=immediate_id,
                user_completed=False,
                completed_at=None,
            )
            if node.kind == PlanKind.TASK:
                session.add(
                    TaskPlan(
                        **fields,
                        allowed_block_families=serialize_allowed_block_families(
                            tuple(node.allowed_block_families)
                        ),
                    )
                )
            else:
                session.add(BlockPlan(**fields, block_family=node.block_family))
        else:
            assert node.repetition is not None and node.template_root_ref is not None
            session.add(
                RepetitionPlan(
                    plan_id=plan_id,
                    template_root_id=UUID(node.template_root_ref),
                    generated_at=None,
                    **node.repetition.model_dump(),
                )
            )
        for ref in node.prerequisite_refs:
            session.add(
                PlanPrerequisite(
                    plan_id=plan_id, prerequisite_plan_id=UUID(resolved_refs.get(ref, ref))
                )
            )
        for group in node.constraint_groups:
            session.add(
                TimeConstraintGroup(
                    time_constraint_group_id=UUID(group.ref),
                    plan_id=plan_id,
                    constraint_kind=group.constraint_kind,
                )
            )
            refs["groups"][group.ref] = group.ref
            for window in group.windows:
                session.add(
                    TimeWindow(
                        time_window_id=UUID(window.ref),
                        group_id=UUID(group.ref),
                        start_time=window.start_time,
                        end_time=window.end_time,
                    )
                )
                refs["windows"][window.ref] = window.ref
    session.add(
        RepetitionInstance(
            repetition_instance_id=uuid4(),
            repetition_plan_id=repetition_id,
            instance_index=instance.instance_index,
            root_clone_id=UUID(instance.root_ref),
            instance_start_time=instance.instance_start_time,
            is_critical=value.settings.default_instance_critical,
            sort_order=instance.instance_index,
        )
    )
    session.flush()
    return refs


def commit_generation(  # noqa: PLR0911, PLR0912
    session: Session, repetition_id: PlanID, body: CommitGenerationInput, now: datetime
) -> ServiceResult[dict]:
    preview = body.preview
    request_hash = hashlib.sha256(
        json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()
    try:
        with transaction(session) as txn:
            # Take the DB writer/row lock before reading receipts, including on SQLite.
            txn.execute(
                update(RepetitionPlan)
                .where(RepetitionPlan.plan_id == repetition_id)
                .values(generated_at=RepetitionPlan.generated_at)
            )
            repetition = txn.get(RepetitionPlan, repetition_id)
            plan = txn.get(Plan, repetition_id)
            if repetition is None or plan is None:
                return fail(
                    ServiceMessage(
                        code=MessageCode.PLAN_NOT_FOUND, message="Repetition not found", details={}
                    )
                )
            receipt = txn.get(RepetitionGenerationReceipt, preview.generation_key)
            if receipt is not None:
                if (
                    receipt.repetition_plan_id != repetition_id
                    or receipt.request_fingerprint != request_hash
                ):
                    return fail(
                        ServiceMessage(
                            code=MessageCode.REPETITION_GENERATION_CONFLICT,
                            message="Generation key was already used for a different request",
                            details={},
                        )
                    )
                return ok(
                    {
                        "repetition": repetition_plan_dto_from_rows(plan, repetition),
                        "reference_map": receipt.reference_map,
                    }
                )
            if repetition.generated_at is not None:
                return fail(
                    ServiceMessage(
                        code=MessageCode.REPETITION_ALREADY_GENERATED,
                        message="Repetition instances were already generated",
                        details={},
                    )
                )
            expected = project_instances(
                preview.input, preview.frozen_horizon, preview.generation_key
            )
            stored = snapshot_repetition(txn, repetition)
            if expected != preview or semantic_input(
                preview.input, body.resolved_refs
            ) != semantic_input(stored, {}):
                raise ValueError(
                    "Repetition settings or template changed; regenerate the queued instances"
                )
            if preview.frozen_horizon.master_horizon_end is not None:
                current = read_frozen_horizon(txn, preview.frozen_horizon.run_started_at)
                if current != preview.frozen_horizon:
                    raise ValueError(
                        "Master horizon settings changed; regenerate the queued instances"
                    )
            valid_indices = {instance.instance_index for instance in preview.instances}
            if not set(body.omitted_instance_indices).issubset(valid_indices):
                raise ValueError("Omitted occurrence index is not in this preview")
            if body.omitted_instance_indices:
                raise ValueError(
                    "Omitting occurrences is not supported until omission storage is available"
                )
            refs = {"plans": {}, "groups": {}, "windows": {}}
            for instance in preview.instances:
                created = materialize_instance(
                    txn,
                    preview.input,
                    instance,
                    preview.generation_key,
                    body.resolved_refs,
                    truncate_to_minute(now),
                )
                for kind, mapping in created.items():
                    refs[kind].update(mapping)
            repetition.generated_at = truncate_to_minute(now)
            plan.updated_at = now
            txn.add(
                RepetitionGenerationReceipt(
                    generation_key=preview.generation_key,
                    repetition_plan_id=repetition_id,
                    request_fingerprint=request_hash,
                    reference_map=refs,
                )
            )
            txn.flush()
            return ok(
                {
                    "repetition": repetition_plan_dto_from_rows(plan, repetition),
                    "reference_map": refs,
                }
            )
    except (ValueError, OverflowError) as exc:
        return fail(
            ServiceMessage(code=MessageCode.REPETITION_PREVIEW_STALE, message=str(exc), details={})
        )
    except ServiceTransactionAborted as exc:
        return fail(*exc.errors)
