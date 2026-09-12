"""Pure, keyed repetition previews shared by the API and clone persistence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Self
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from calendar_backend.domain.enums import ConstraintKind, PlanKind, RepeatMode
from calendar_backend.domain.errors import ServiceMessage
from calendar_backend.domain.repetitions import (
    RepetitionSettingsState,
    compute_instance_indices,
    validate_repetition_settings_update,
)


class ProjectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectionSettings(ProjectionModel):
    repeat_mode: RepeatMode
    start_time: datetime
    repeat_interval_minutes: int = Field(gt=0)
    manual_count: int | None = Field(default=None, gt=0)
    end_time: datetime | None = None
    default_instance_critical: bool = False

    @model_validator(mode="after")
    def validate_settings(self) -> Self:
        state = RepetitionSettingsState(**self.model_dump(), generated_at=None)
        error = validate_repetition_settings_update(state, state)
        if error is not None:
            raise ValueError(error.message)
        return self


class ProjectionWindow(ProjectionModel):
    ref: str = Field(min_length=1)
    start_time: datetime
    end_time: datetime

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        for value in (self.start_time, self.end_time):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError("Window times must be timezone-aware UTC")
            if value.second or value.microsecond:
                raise ValueError("Window times must be minute aligned")
        if self.end_time <= self.start_time:
            raise ValueError("Window end must be after start")
        return self


class ProjectionGroup(ProjectionModel):
    ref: str = Field(min_length=1)
    constraint_kind: ConstraintKind = ConstraintKind.USER
    windows: list[ProjectionWindow] = Field(min_length=1)


class ProjectionNode(ProjectionModel):
    ref: str = Field(min_length=1)
    parent_ref: str | None = None
    kind: PlanKind
    name: str
    is_critical: bool | None = None
    sort_order: int | None = Field(default=None, ge=0)
    duration_minutes: int | None = Field(default=None, gt=0)
    divisible: bool = False
    minimum_chunk_size_minutes: int | None = Field(default=None, gt=0)
    allowed_block_families: list[str] = Field(default_factory=lambda: ["default"])
    block_family: str = "default"
    prerequisite_refs: list[str] = Field(default_factory=list)
    immediate_prerequisite_ref: str | None = None
    repetition: ProjectionSettings | None = None
    template_root_ref: str | None = None
    constraint_groups: list[ProjectionGroup] = Field(default_factory=list)

    @field_validator("allowed_block_families")
    @classmethod
    def normalize_families(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or value.strip() == "free-time" for value in values):
            raise ValueError("Task block families must be nonempty and cannot be free-time")
        return sorted({value.strip() for value in values or ["default"]})

    @model_validator(mode="after")
    def validate_subtype(self) -> Self:
        if self.kind in (PlanKind.TASK, PlanKind.BLOCK):
            if self.duration_minutes is None:
                raise ValueError("Task/block nodes require duration_minutes")
            if self.divisible != (self.minimum_chunk_size_minutes is not None):
                raise ValueError("Chunk size must be set exactly when divisible")
            if (self.minimum_chunk_size_minutes or 0) > self.duration_minutes:
                raise ValueError("Chunk size exceeds duration")
        if self.kind == PlanKind.BLOCK and (
            not self.block_family.strip() or self.block_family == "free-time"
        ):
            raise ValueError("Invalid block family")
        if self.kind == PlanKind.REPETITION and (
            self.repetition is None or self.template_root_ref is None
        ):
            raise ValueError("Repetition nodes require settings and template_root_ref")
        return self


class ProjectionTemplate(ProjectionModel):
    root_ref: str
    nodes: list[ProjectionNode] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        by_ref = {node.ref: node for node in self.nodes}
        if len(by_ref) != len(self.nodes) or self.root_ref not in by_ref:
            raise ValueError("Template requires unique node refs and a valid root_ref")
        refs = list(by_ref)
        for node in self.nodes:
            current, seen = node, set()
            while current.ref != self.root_ref:
                if current.ref in seen or current.parent_ref not in by_ref:
                    raise ValueError("Template nodes must form one acyclic tree")
                seen.add(current.ref)
                current = by_ref[current.parent_ref]
            if node.ref != self.root_ref:
                parent = by_ref[node.parent_ref]
                if parent.kind not in (PlanKind.GOAL, PlanKind.REPETITION):
                    raise ValueError("Task/block nodes cannot have children")
                if parent.kind == PlanKind.REPETITION and parent.template_root_ref != node.ref:
                    raise ValueError("A nested repetition may contain only its template")
            if node.kind == PlanKind.REPETITION:
                child = by_ref.get(node.template_root_ref)
                if child is None or child.parent_ref != node.ref:
                    raise ValueError("Nested template must be a child of its repetition")
            for group in node.constraint_groups:
                if group.constraint_kind != ConstraintKind.USER:
                    raise ValueError("Template inputs accept USER constraints only")
                refs.append(group.ref)
                refs.extend(window.ref for window in group.windows)
        if len(set(refs)) != len(refs):
            raise ValueError("Plan, group and window refs must be globally unique")
        return self


class PreviewInput(ProjectionModel):
    repetition_ref: str = Field(min_length=1)
    settings: ProjectionSettings
    template: ProjectionTemplate


class FrozenHorizon(ProjectionModel):
    run_started_at: datetime
    master_horizon_end: datetime | None = None


class ProjectedInstance(ProjectionModel):
    instance_index: int
    instance_start_time: datetime
    root_ref: str
    nodes: list[ProjectionNode]


class GenerationPreview(ProjectionModel):
    generation_key: str
    input_fingerprint: str
    frozen_horizon: FrozenHorizon
    input: PreviewInput
    instances: list[ProjectedInstance]


class CommitGenerationInput(ProjectionModel):
    preview: GenerationPreview
    resolved_refs: dict[str, str]
    omitted_instance_indices: list[int] = Field(default_factory=list)


def input_fingerprint(value: PreviewInput) -> str:
    return hashlib.sha256(
        json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def generated_ref(generation_key: str, instance_index: int, source_ref: str) -> str:
    return str(uuid5(NAMESPACE_URL, json.dumps([generation_key, instance_index, source_ref])))


def project_instances(
    value: PreviewInput,
    horizon: FrozenHorizon,
    generation_key: str | None = None,
    *,
    instance_indices: tuple[int, ...] | None = None,
) -> GenerationPreview:
    """Project only data: no session, clock lookup, solver, or persistence."""
    key = generation_key or str(uuid4())
    settings = value.settings
    indices = (
        instance_indices
        if instance_indices is not None
        else compute_instance_indices(
            repeat_mode=settings.repeat_mode,
            start_time=settings.start_time,
            repeat_interval_minutes=settings.repeat_interval_minutes,
            manual_count=settings.manual_count,
            end_time=settings.end_time,
            master_horizon_end=horizon.master_horizon_end,
        )
    )
    if isinstance(indices, ServiceMessage):
        raise ValueError(indices.message)
    node_refs = {node.ref for node in value.template.nodes}
    instances = []
    for index in indices:
        refs = {ref: generated_ref(key, index, ref) for ref in node_refs}
        offset = timedelta(minutes=index * settings.repeat_interval_minutes)
        start = settings.start_time + offset
        nodes = []
        for source in value.template.nodes:
            node = source.model_copy(deep=True)
            node.ref = refs[source.ref]
            node.parent_ref = refs.get(source.parent_ref, value.repetition_ref)
            node.prerequisite_refs = [refs.get(ref, ref) for ref in source.prerequisite_refs]
            node.immediate_prerequisite_ref = refs.get(
                source.immediate_prerequisite_ref, source.immediate_prerequisite_ref
            )
            node.template_root_ref = refs.get(source.template_root_ref, source.template_root_ref)
            for group in node.constraint_groups:
                group.ref = generated_ref(key, index, group.ref)
                for window in group.windows:
                    window.ref = generated_ref(key, index, window.ref)
                    window.start_time += offset
                    window.end_time += offset
            if source.ref == value.template.root_ref:
                node.is_critical = settings.default_instance_critical
                node.sort_order = index
                node.constraint_groups.append(
                    ProjectionGroup(
                        ref=generated_ref(key, index, "system:group"),
                        constraint_kind=ConstraintKind.SYSTEM_REPETITION_WINDOW,
                        windows=[
                            ProjectionWindow(
                                ref=generated_ref(key, index, "system:window"),
                                start_time=start,
                                end_time=start
                                + timedelta(minutes=settings.repeat_interval_minutes),
                            )
                        ],
                    )
                )
            nodes.append(node)
        instances.append(
            ProjectedInstance(
                instance_index=index,
                instance_start_time=start,
                root_ref=refs[value.template.root_ref],
                nodes=nodes,
            )
        )
    return GenerationPreview(
        generation_key=key,
        input_fingerprint=input_fingerprint(value),
        frozen_horizon=horizon,
        input=value,
        instances=instances,
    )
