"""ORM mappings for plan time constraint groups and windows."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from calendar_backend.db.base import Base
from calendar_backend.domain.enums import ConstraintKind
from calendar_backend.models.plans import Plan


class TimeConstraintGroup(Base):
    __tablename__ = "time_constraint_group"
    __table_args__ = (
        Index(
            "uq_time_constraint_group_plan_system_master_horizon",
            "plan_id",
            unique=True,
            sqlite_where=text("constraint_kind = 'SYSTEM_MASTER_HORIZON'"),
        ),
    )

    time_constraint_group_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plan.plan_id"),
        nullable=False,
    )
    constraint_kind: Mapped[ConstraintKind] = mapped_column(
        Enum(ConstraintKind, native_enum=False),
        nullable=False,
    )

    plan: Mapped[Plan] = relationship(back_populates="constraint_groups")
    windows: Mapped[list[TimeWindow]] = relationship(back_populates="group")


class TimeWindow(Base):
    __tablename__ = "time_window"
    __table_args__ = (
        CheckConstraint(
            "start_time < end_time",
            name="start_before_end",
        ),
    )

    time_window_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("time_constraint_group.time_constraint_group_id"),
        nullable=False,
    )
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    group: Mapped[TimeConstraintGroup] = relationship(back_populates="windows")
