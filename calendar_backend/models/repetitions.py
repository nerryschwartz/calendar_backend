"""ORM mappings for repetition plan instances."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from calendar_backend.db.base import Base
from calendar_backend.models.plans import Plan, RepetitionPlan


class RepetitionInstance(Base):
    __tablename__ = "repetition_instance"
    __table_args__ = (
        CheckConstraint(
            "instance_index >= 0",
            name="instance_index_non_negative",
        ),
        CheckConstraint(
            "sort_order >= 0",
            name="sort_order_non_negative",
        ),
    )

    repetition_instance_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
    )
    repetition_plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("repetition_plan.plan_id"),
        nullable=False,
    )
    instance_index: Mapped[int] = mapped_column(Integer, nullable=False)
    root_clone_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plan.plan_id"),
        nullable=False,
    )
    instance_start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    is_critical: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)

    repetition_plan: Mapped[RepetitionPlan] = relationship(back_populates="instances")
    root_clone: Mapped[Plan] = relationship(foreign_keys=[root_clone_id])
