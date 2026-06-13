"""ORM mappings for goal child chains and chain items."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from calendar_backend.db.base import Base
from calendar_backend.models.plans import GoalPlan, Plan


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
