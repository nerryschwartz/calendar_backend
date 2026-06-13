"""ORM mappings for the plan tree and goal child chains.

Subtype pairing (plan_kind vs detail rows) and tree reachability are enforced
by services and PlanTreeInvariantService, not by database triggers or ORM.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from calendar_backend.db.base import Base
from calendar_backend.domain.enums import CloneStatus, PlanKind, RepeatMode


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

    plan: Mapped[Plan] = relationship(back_populates="task_plan")


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

    plan: Mapped[Plan] = relationship(
        back_populates="repetition_plan",
        foreign_keys=[plan_id],
    )


class GoalChildChain(Base):
    __tablename__ = "goal_child_chain"
    __table_args__ = (
        CheckConstraint(
            "sort_order >= 0",
            name="sort_order_non_negative",
        ),
    )

    goal_child_chain_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    parent_goal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("goal_plan.plan_id"),
        nullable=False,
    )
    is_critical: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    parent_goal: Mapped[GoalPlan] = relationship(back_populates="chains")
    items: Mapped[list[GoalChildChainItem]] = relationship(back_populates="chain")


class GoalChildChainItem(Base):
    __tablename__ = "goal_child_chain_item"
    __table_args__ = (
        UniqueConstraint("child_plan_id"),
        CheckConstraint(
            "position >= 0",
            name="position_non_negative",
        ),
    )

    goal_child_chain_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    chain_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("goal_child_chain.goal_child_chain_id"),
        nullable=False,
    )
    child_plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plan.plan_id"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    chain: Mapped[GoalChildChain] = relationship(back_populates="items")
    child_plan: Mapped[Plan] = relationship(foreign_keys=[child_plan_id])
