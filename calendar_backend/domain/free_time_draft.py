"""Validated commands and result for an atomic free-time draft."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from calendar_backend.domain.free_time import FreeTimeActivityDTO


class CreateActivityEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["create"]
    draft_ref: str = Field(min_length=1)
    name: str
    real_fraction: Decimal = Field(allow_inf_nan=False)
    minimum_block_size_minutes: int = Field(ge=0)
    enabled: bool = True


class ActivityRefEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    activity_ref: str = Field(min_length=1)


class UpdateActivityEdit(ActivityRefEdit):
    op: Literal["update"]
    name: str | None = None
    real_fraction: Decimal | None = Field(default=None, allow_inf_nan=False)
    minimum_block_size_minutes: int | None = Field(default=None, ge=0)


class SetEnabledEdit(ActivityRefEdit):
    op: Literal["set_enabled"]
    enabled: bool


class SetBlockFamiliesEdit(ActivityRefEdit):
    op: Literal["set_block_families"]
    families: tuple[str, ...]


class ClearBlockFamiliesEdit(ActivityRefEdit):
    op: Literal["clear_block_families"]


class PrerequisiteEdit(ActivityRefEdit):
    op: Literal["add_prerequisite", "remove_prerequisite"]
    prerequisite_plan_id: UUID


class DeleteActivityEdit(ActivityRefEdit):
    op: Literal["delete"]


FreeTimeDraftEdit = Annotated[
    CreateActivityEdit
    | UpdateActivityEdit
    | SetEnabledEdit
    | SetBlockFamiliesEdit
    | ClearBlockFamiliesEdit
    | PrerequisiteEdit
    | DeleteActivityEdit,
    Field(discriminator="op"),
]


class FreeTimeDraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edits: tuple[FreeTimeDraftEdit, ...]


@dataclass(frozen=True)
class FreeTimeDraftResult:
    applied_count: int
    activities: tuple[FreeTimeActivityDTO, ...]
