"""ORM mappings for free-time activities and prerequisites."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from calendar_backend.db.base import Base
from calendar_backend.models.plans import Plan


class FreeTimeActivity(Base):
    __tablename__ = "free_time_activity"
    __table_args__ = (
        CheckConstraint(
            "minimum_block_size_minutes >= 0",
            name="minimum_block_size_non_negative",
        ),
    )

    free_time_activity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    real_fraction: Mapped[Decimal] = mapped_column(Numeric(18, 9), nullable=False)
    minimum_block_size_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    prerequisites: Mapped[list[FreeTimeActivityPrerequisite]] = relationship(
        back_populates="activity",
    )


class FreeTimeActivityPrerequisite(Base):
    __tablename__ = "free_time_activity_prerequisite"

    prerequisite_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    free_time_activity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("free_time_activity.free_time_activity_id"),
        nullable=False,
    )
    source_plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plan.plan_id"),
        nullable=False,
    )

    activity: Mapped[FreeTimeActivity] = relationship(back_populates="prerequisites")
    source_plan: Mapped[Plan] = relationship(foreign_keys=[source_plan_id])
