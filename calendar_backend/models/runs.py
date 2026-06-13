"""ORM mappings for calendar run metadata and active calendar state."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Integer, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from calendar_backend.db.base import Base
from calendar_backend.domain.enums import CalendarRunStatus, LastFailureReason, SolverStatus

if TYPE_CHECKING:
    from calendar_backend.models.calendar import CalendarEntry


class CalendarRun(Base):
    __tablename__ = "calendar_run"

    calendar_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    run_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    run_finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status: Mapped[CalendarRunStatus] = mapped_column(
        Enum(CalendarRunStatus, native_enum=False),
        nullable=False,
    )
    solver_status: Mapped[SolverStatus | None] = mapped_column(
        Enum(SolverStatus, native_enum=False),
        nullable=True,
    )
    conflict_count: Mapped[int] = mapped_column(Integer, nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False)
    runtime_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    calendar_entries: Mapped[list[CalendarEntry]] = relationship(back_populates="calendar_run")


class ActiveCalendarState(Base):
    __tablename__ = "active_calendar_state"
    __table_args__ = (
        CheckConstraint(
            "singleton_id = 1",
            name="active_calendar_state_singleton_id_is_one",
        ),
    )

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    active_calendar_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("calendar_run.calendar_run_id"),
        nullable=True,
    )
    last_refresh_failed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_failure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_failure_reason: Mapped[LastFailureReason | None] = mapped_column(
        Enum(LastFailureReason, native_enum=False),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    active_calendar_run: Mapped[CalendarRun | None] = relationship(
        foreign_keys=[active_calendar_run_id],
    )
