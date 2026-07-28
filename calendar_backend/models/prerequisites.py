"""ORM mappings for plan-level prerequisite edges."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from calendar_backend.db.base import Base

if TYPE_CHECKING:
    from calendar_backend.models.plans import Plan


class PlanPrerequisite(Base):
    __tablename__ = "plan_prerequisite"
    __table_args__ = (
        CheckConstraint(
            "plan_id != prerequisite_plan_id",
            name="no_self_prerequisite",
        ),
    )

    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plan.plan_id"),
        primary_key=True,
    )
    prerequisite_plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("plan.plan_id"),
        primary_key=True,
    )

    dependent_plan: Mapped[Plan] = relationship(
        foreign_keys=[plan_id],
    )
    prerequisite_plan: Mapped[Plan] = relationship(
        foreign_keys=[prerequisite_plan_id],
    )
