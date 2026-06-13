"""ORM mappings for application settings singleton."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from calendar_backend.db.base import Base
from calendar_backend.domain.enums import FreeTimeWeekStartDay


class AppSettings(Base):
    __tablename__ = "app_settings"
    __table_args__ = (
        CheckConstraint(
            "singleton_id = 1",
            name="app_settings_singleton_id_is_one",
        ),
    )

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    local_timezone: Mapped[str] = mapped_column(String, nullable=False)
    master_horizon_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduling_granularity_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    exact_solver_time_limit_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    exact_solver_model_size_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    heuristic_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    free_time_week_start_day: Mapped[FreeTimeWeekStartDay] = mapped_column(
        Enum(FreeTimeWeekStartDay, native_enum=False),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
