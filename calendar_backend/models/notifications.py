"""ORM mappings for the notification queue."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from calendar_backend.db.base import Base
from calendar_backend.domain.enums import NotificationSourceKind
from calendar_backend.models.plans import Plan


class NotificationQueueItem(Base):
    __tablename__ = "notification_queue_item"
    __table_args__ = (
        CheckConstraint(
            "dismissed_at IS NULL OR dismissed_at >= created_at",
            name="notification_dismissed_after_created",
        ),
    )

    notification_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    source_kind: Mapped[NotificationSourceKind] = mapped_column(
        Enum(NotificationSourceKind, native_enum=False),
        nullable=False,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plan.plan_id"),
        nullable=False,
    )
    timer_key: Mapped[str] = mapped_column(String, nullable=False)
    window_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    calendar_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("calendar_entry.calendar_entry_id"),
        nullable=True,
    )
    block_calendar_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("block_calendar_entry.block_calendar_entry_id"),
        nullable=True,
    )
    display_label: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dismissed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    plan: Mapped[Plan] = relationship(foreign_keys=[plan_id])
