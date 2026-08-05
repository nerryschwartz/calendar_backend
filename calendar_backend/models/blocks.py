"""ORM mappings for block plan subtype and block calendar entries."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from calendar_backend.db.base import Base
from calendar_backend.models.plans import Plan
from calendar_backend.models.runs import CalendarRun


class BlockPlan(Base):
    __tablename__ = "block_plan"
    __table_args__ = (
        CheckConstraint(
            "duration_minutes > 0",
            name="block_duration_positive",
        ),
        CheckConstraint(
            "(divisible = 1 AND minimum_chunk_size_minutes IS NOT NULL) "
            "OR (divisible = 0 AND minimum_chunk_size_minutes IS NULL)",
            name="block_chunk_matches_divisibility",
        ),
        CheckConstraint(
            "minimum_chunk_size_minutes IS NULL OR minimum_chunk_size_minutes > 0",
            name="block_minimum_chunk_positive_when_set",
        ),
        CheckConstraint(
            "minimum_chunk_size_minutes IS NULL OR minimum_chunk_size_minutes <= duration_minutes",
            name="block_minimum_chunk_lte_duration",
        ),
        CheckConstraint(
            "length(trim(block_family)) > 0",
            name="block_family_non_empty",
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
    block_family: Mapped[str] = mapped_column(String, nullable=False)
    immediate_prerequisite_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plan.plan_id"),
        nullable=True,
    )

    plan: Mapped[Plan] = relationship(
        back_populates="block_plan",
        foreign_keys=[plan_id],
    )
    immediate_prerequisite_plan: Mapped[Plan | None] = relationship(
        foreign_keys=[immediate_prerequisite_plan_id],
    )


class BlockCalendarEntry(Base):
    __tablename__ = "block_calendar_entry"
    __table_args__ = (
        CheckConstraint(
            "start_time < end_time",
            name="block_calendar_start_before_end",
        ),
    )

    block_calendar_entry_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plan.plan_id"),
        nullable=False,
    )
    calendar_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("calendar_run.calendar_run_id"),
        nullable=True,
    )
    display_label: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    source_plan: Mapped[Plan] = relationship(foreign_keys=[source_plan_id])
    calendar_run: Mapped[CalendarRun | None] = relationship(
        foreign_keys=[calendar_run_id],
    )
