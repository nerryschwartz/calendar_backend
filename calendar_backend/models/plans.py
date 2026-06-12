from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from calendar_backend.db.base import Base
from calendar_backend.domain.enums import CloneStatus, PlanKind, RepeatMode


class Plan(Base):
    __tablename__ = "plan"

    plan_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    plan_kind: Mapped[PlanKind] = mapped_column(
        Enum(PlanKind, native_enum=False),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plan.plan_id"),
        nullable=True,
    )
    is_master: Mapped[bool] = mapped_column(Boolean, nullable=False)
    cloned_from_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plan.plan_id"),
        nullable=True,
    )
    clone_status: Mapped[CloneStatus] = mapped_column(
        Enum(CloneStatus, native_enum=False),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GoalPlan(Base):
    __tablename__ = "goal_plan"

    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plan.plan_id"),
        primary_key=True,
    )


class TaskPlan(Base):
    __tablename__ = "task_plan"

    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plan.plan_id"),
        primary_key=True,
    )
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    divisible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    minimum_chunk_size_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    user_completed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class RepetitionPlan(Base):
    __tablename__ = "repetition_plan"

    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plan.plan_id"),
        primary_key=True,
    )
    repeat_mode: Mapped[RepeatMode] = mapped_column(
        Enum(RepeatMode, native_enum=False),
        nullable=False,
    )
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    repeat_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    manual_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    template_root_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plan.plan_id"),
        nullable=False,
    )
    default_instance_critical: Mapped[bool] = mapped_column(Boolean, nullable=False)
    generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
