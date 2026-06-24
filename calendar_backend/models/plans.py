"""ORM mappings for the plan tree and plan subtype detail tables.

Subtype pairing (plan_kind vs detail rows) and tree reachability are enforced
by services and PlanTreeInvariantService, not by database triggers or ORM.
Goal child chains live in ``calendar_backend.models.chains``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from calendar_backend.db.base import Base
from calendar_backend.domain.enums import CloneStatus, PlanKind, RepeatMode

if TYPE_CHECKING:
    from calendar_backend.models.chains import GoalChildChain
    from calendar_backend.models.constraints import TimeConstraintGroup
    from calendar_backend.models.repetitions import RepetitionInstance


class Plan(Base):
    __tablename__ = "plan"
    __table_args__ = (
        CheckConstraint(
            "NOT is_master OR plan_kind = 'GOAL'",
            name="master_is_goal",
        ),
        Index(
            "uq_plan_is_master",
            "is_master",
            unique=True,
            sqlite_where=text("is_master = 1"),
        ),
    )

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

    parent: Mapped[Plan | None] = relationship(
        remote_side=[plan_id],
        back_populates="children",
        foreign_keys=[parent_id],
    )
    children: Mapped[list[Plan]] = relationship(
        back_populates="parent",
        foreign_keys=[parent_id],
    )
    goal_plan: Mapped[GoalPlan | None] = relationship(
        back_populates="plan",
        uselist=False,
    )
    task_plan: Mapped[TaskPlan | None] = relationship(
        back_populates="plan",
        uselist=False,
    )
    repetition_plan: Mapped[RepetitionPlan | None] = relationship(
        back_populates="plan",
        uselist=False,
        foreign_keys="RepetitionPlan.plan_id",
    )
    constraint_groups: Mapped[list[TimeConstraintGroup]] = relationship(
        back_populates="plan",
    )


class GoalPlan(Base):
    __tablename__ = "goal_plan"

    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plan.plan_id"),
        primary_key=True,
    )

    plan: Mapped[Plan] = relationship(back_populates="goal_plan")
    chains: Mapped[list[GoalChildChain]] = relationship(back_populates="parent_goal")


class TaskPlan(Base):
    __tablename__ = "task_plan"
    __table_args__ = (
        CheckConstraint(
            "duration_minutes > 0",
            name="duration_positive",
        ),
        CheckConstraint(
            "(divisible = 1 AND minimum_chunk_size_minutes IS NOT NULL) "
            "OR (divisible = 0 AND minimum_chunk_size_minutes IS NULL)",
            name="task_chunk_matches_divisibility",
        ),
        CheckConstraint(
            "minimum_chunk_size_minutes IS NULL OR minimum_chunk_size_minutes > 0",
            name="minimum_chunk_positive_when_set",
        ),
        CheckConstraint(
            "minimum_chunk_size_minutes IS NULL OR minimum_chunk_size_minutes <= duration_minutes",
            name="minimum_chunk_lte_duration",
        ),
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plan.plan_id"),
        primary_key=True,
    )
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    divisible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    minimum_chunk_size_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_completed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    plan: Mapped[Plan] = relationship(back_populates="task_plan")


class RepetitionPlan(Base):
    __tablename__ = "repetition_plan"
    __table_args__ = (
        CheckConstraint(
            "repeat_interval_minutes > 0",
            name="repeat_interval_positive",
        ),
        CheckConstraint(
            "end_time IS NULL OR end_time > start_time",
            name="end_after_start",
        ),
        CheckConstraint(
            "manual_count IS NULL OR manual_count > 0",
            name="manual_count_positive_when_set",
        ),
        CheckConstraint(
            "repeat_mode != 'MANUAL_COUNT' OR (manual_count IS NOT NULL AND end_time IS NULL)",
            name="manual_count_mode_fields",
        ),
        CheckConstraint(
            "repeat_mode != 'DATE_RANGE' OR manual_count IS NULL",
            name="date_range_mode_fields",
        ),
    )

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

    plan: Mapped[Plan] = relationship(
        back_populates="repetition_plan",
        foreign_keys=[plan_id],
    )
    instances: Mapped[list[RepetitionInstance]] = relationship(back_populates="repetition_plan")
