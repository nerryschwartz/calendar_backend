"""Read-only snapshot adapter for pure repetition generation."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from calendar_backend.domain.calendar_duration import CalendarDuration
from calendar_backend.domain.enums import ConstraintKind, PlanKind
from calendar_backend.domain.repetition_projection import (
    FrozenHorizon,
    PreviewInput,
    ProjectionGroup,
    ProjectionNode,
    ProjectionSettings,
    ProjectionTemplate,
    ProjectionWindow,
)
from calendar_backend.domain.task_families import effective_allowed_block_families
from calendar_backend.domain.time import sqlite_utc, truncate_to_minute
from calendar_backend.models.blocks import BlockPlan
from calendar_backend.models.constraints import TimeConstraintGroup, TimeWindow
from calendar_backend.models.plans import Plan, RepetitionPlan, TaskPlan
from calendar_backend.models.prerequisites import PlanPrerequisite
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
