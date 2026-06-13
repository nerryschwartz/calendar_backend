"""create remaining core orm tables

Revision ID: 7369a1e5acb0
Revises: be7d178b7c5a
Create Date: 2026-06-13 00:48:57.189828

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7369a1e5acb0"
down_revision: str | Sequence[str] | None = "be7d178b7c5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "app_settings",
        sa.Column("singleton_id", sa.Integer(), nullable=False),
        sa.Column("local_timezone", sa.String(), nullable=False),
        sa.Column("master_horizon_duration_minutes", sa.Integer(), nullable=False),
        sa.Column("scheduling_granularity_minutes", sa.Integer(), nullable=False),
        sa.Column("exact_solver_time_limit_seconds", sa.Integer(), nullable=False),
        sa.Column("exact_solver_model_size_limit", sa.Integer(), nullable=False),
        sa.Column("heuristic_enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "free_time_week_start_day",
            sa.Enum(
                "MONDAY",
                "TUESDAY",
                "WEDNESDAY",
                "THURSDAY",
                "FRIDAY",
                "SATURDAY",
                "SUNDAY",
                name="freetimeweekstartday",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "singleton_id = 1",
            name=op.f("ck_app_settings_app_settings_singleton_id_is_one"),
        ),
        sa.PrimaryKeyConstraint("singleton_id", name=op.f("pk_app_settings")),
    )
    op.create_table(
        "calendar_run",
        sa.Column("calendar_run_id", sa.Uuid(), nullable=False),
        sa.Column("run_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum("SUCCESS", "FAILED", name="calendarrunstatus", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "solver_status",
            sa.Enum("OPTIMAL", "FEASIBLE", "INFEASIBLE", name="solverstatus", native_enum=False),
            nullable=True,
        ),
        sa.Column("conflict_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("runtime_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("calendar_run_id", name=op.f("pk_calendar_run")),
    )
    op.create_table(
        "free_time_activity",
        sa.Column("free_time_activity_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("real_fraction", sa.Numeric(precision=18, scale=9), nullable=False),
        sa.Column("minimum_block_size_minutes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "minimum_block_size_minutes >= 0",
            name=op.f("ck_free_time_activity_minimum_block_size_non_negative"),
        ),
        sa.PrimaryKeyConstraint("free_time_activity_id", name=op.f("pk_free_time_activity")),
    )
    op.create_table(
        "active_calendar_state",
        sa.Column("singleton_id", sa.Integer(), nullable=False),
        sa.Column("active_calendar_run_id", sa.Uuid(), nullable=True),
        sa.Column("last_refresh_failed", sa.Boolean(), nullable=False),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_failure_reason",
            sa.Enum(
                "ASSIGNMENT_FAILED",
                "ASSIGNMENT_PRECONDITION_FAILED",
                name="lastfailurereason",
                native_enum=False,
            ),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "singleton_id = 1",
            name=op.f("ck_active_calendar_state_active_calendar_state_singleton_id_is_one"),
        ),
        sa.ForeignKeyConstraint(
            ["active_calendar_run_id"],
            ["calendar_run.calendar_run_id"],
            name=op.f("fk_active_calendar_state_active_calendar_run_id_calendar_run"),
        ),
        sa.PrimaryKeyConstraint("singleton_id", name=op.f("pk_active_calendar_state")),
    )
    op.create_table(
        "calendar_entry",
        sa.Column("calendar_entry_id", sa.Uuid(), nullable=False),
        sa.Column(
            "entry_type",
            sa.Enum("TASK", "FREE_TIME", name="calendarentrytype", native_enum=False),
            nullable=False,
        ),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_plan_id", sa.Uuid(), nullable=True),
        sa.Column("source_free_time_activity_id", sa.Uuid(), nullable=True),
        sa.Column("calendar_run_id", sa.Uuid(), nullable=True),
        sa.Column("display_label", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "start_time < end_time",
            name=op.f("ck_calendar_entry_start_before_end"),
        ),
        sa.ForeignKeyConstraint(
            ["calendar_run_id"],
            ["calendar_run.calendar_run_id"],
            name=op.f("fk_calendar_entry_calendar_run_id_calendar_run"),
        ),
        sa.ForeignKeyConstraint(
            ["source_free_time_activity_id"],
            ["free_time_activity.free_time_activity_id"],
            name=op.f("fk_calendar_entry_source_free_time_activity_id_free_time_activity"),
        ),
        sa.ForeignKeyConstraint(
            ["source_plan_id"],
            ["plan.plan_id"],
            name=op.f("fk_calendar_entry_source_plan_id_plan"),
        ),
        sa.PrimaryKeyConstraint("calendar_entry_id", name=op.f("pk_calendar_entry")),
    )
    op.create_table(
        "free_time_activity_prerequisite",
        sa.Column("prerequisite_id", sa.Uuid(), nullable=False),
        sa.Column("free_time_activity_id", sa.Uuid(), nullable=False),
        sa.Column("source_plan_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["free_time_activity_id"],
            ["free_time_activity.free_time_activity_id"],
            name=op.f(
                "fk_free_time_activity_prerequisite_free_time_activity_id_free_time_activity"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["source_plan_id"],
            ["plan.plan_id"],
            name=op.f("fk_free_time_activity_prerequisite_source_plan_id_plan"),
        ),
        sa.PrimaryKeyConstraint("prerequisite_id", name=op.f("pk_free_time_activity_prerequisite")),
    )
    op.create_table(
        "time_constraint_group",
        sa.Column("time_constraint_group_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column(
            "constraint_kind",
            sa.Enum(
                "USER",
                "SYSTEM_REPETITION_WINDOW",
                "SYSTEM_MASTER_HORIZON",
                name="constraintkind",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["plan.plan_id"],
            name=op.f("fk_time_constraint_group_plan_id_plan"),
        ),
        sa.PrimaryKeyConstraint(
            "time_constraint_group_id",
            name=op.f("pk_time_constraint_group"),
        ),
    )
    op.create_table(
        "repetition_instance",
        sa.Column("repetition_instance_id", sa.Uuid(), nullable=False),
        sa.Column("repetition_plan_id", sa.Uuid(), nullable=False),
        sa.Column("instance_index", sa.Integer(), nullable=False),
        sa.Column("root_clone_id", sa.Uuid(), nullable=False),
        sa.Column("instance_start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_critical", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "instance_index >= 0",
            name=op.f("ck_repetition_instance_instance_index_non_negative"),
        ),
        sa.CheckConstraint(
            "sort_order >= 0",
            name=op.f("ck_repetition_instance_sort_order_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["repetition_plan_id"],
            ["repetition_plan.plan_id"],
            name=op.f("fk_repetition_instance_repetition_plan_id_repetition_plan"),
        ),
        sa.ForeignKeyConstraint(
            ["root_clone_id"],
            ["plan.plan_id"],
            name=op.f("fk_repetition_instance_root_clone_id_plan"),
        ),
        sa.PrimaryKeyConstraint("repetition_instance_id", name=op.f("pk_repetition_instance")),
    )
    op.create_table(
        "time_window",
        sa.Column("time_window_id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "start_time < end_time",
            name=op.f("ck_time_window_start_before_end"),
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["time_constraint_group.time_constraint_group_id"],
            name=op.f("fk_time_window_group_id_time_constraint_group"),
        ),
        sa.PrimaryKeyConstraint("time_window_id", name=op.f("pk_time_window")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("time_window")
    op.drop_table("repetition_instance")
    op.drop_table("time_constraint_group")
    op.drop_table("free_time_activity_prerequisite")
    op.drop_table("calendar_entry")
    op.drop_table("active_calendar_state")
    op.drop_table("free_time_activity")
    op.drop_table("calendar_run")
    op.drop_table("app_settings")
