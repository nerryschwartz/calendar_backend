"""ORM mappings for active calendar entries."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from calendar_backend.db.base import Base
from calendar_backend.domain.enums import CalendarEntryType
from calendar_backend.models.free_time import FreeTimeActivity
from calendar_backend.models.plans import Plan


class CalendarEntry(Base):
    __tablename__ = "calendar_entry"
    __table_args__ = (
        CheckConstraint(
            "start_time < end_time",
            name="start_before_end",
        ),
    )

    calendar_entry_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    entry_type: Mapped[CalendarEntryType] = mapped_column(
        Enum(CalendarEntryType, native_enum=False),
        nullable=False,
    )
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plan.plan_id"),
        nullable=True,
    )
    source_free_time_activity_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("free_time_activity.free_time_activity_id"),
        nullable=True,
    )
    calendar_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("calendar_run.calendar_run_id"),
        nullable=True,
    )
    display_label: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    source_plan: Mapped[Plan | None] = relationship(foreign_keys=[source_plan_id])
    source_free_time_activity: Mapped[FreeTimeActivity | None] = relationship(
        foreign_keys=[source_free_time_activity_id]
    )
