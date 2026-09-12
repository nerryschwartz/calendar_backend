"""Repetition metadata migrations preserve data and match ORM constraints."""

from datetime import UTC, datetime
from uuid import uuid4

import calendar_backend.db.session as db_session
from alembic import command
from alembic.config import Config
from calendar_backend.domain.enums import CloneStatus, PlanKind
from calendar_backend.models.plans import GoalPlan, Plan
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session


def test_generation_metadata_upgrade_downgrade_preserves_plans(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'generation-migration.sqlite3'}"
    monkeypatch.setattr(db_session, "DEFAULT_DATABASE_URL", url)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "b91f6d82a304")
    engine = create_engine(url)
    plan_id = uuid4()
    try:
        with Session(engine) as session, session.begin():
            session.add(
                Plan(
                    plan_id=plan_id,
                    name="Keep this plan",
                    plan_kind=PlanKind.GOAL,
                    parent_id=None,
                    is_master=True,
                    clone_status=CloneStatus.NOT_CLONED,
                    cloned_from_id=None,
                    goal_is_critical=None,
                    goal_sort_order=None,
                    created_at=datetime(2026, 9, 12, tzinfo=UTC),
                    updated_at=datetime(2026, 9, 12, tzinfo=UTC),
                )
            )
            session.add(GoalPlan(plan_id=plan_id))
        command.upgrade(config, "head")
        schema = inspect(engine)
        assert {"repetition_generation_receipt", "repetition_skipped_occurrence"}.issubset(
            schema.get_table_names()
        )
        for table in ("repetition_generation_receipt", "repetition_skipped_occurrence"):
            assert schema.get_foreign_keys(table)[0]["options"]["ondelete"] == "CASCADE"
        assert schema.get_check_constraints("repetition_skipped_occurrence")
        assert schema.get_unique_constraints("repetition_generation_receipt")
        command.downgrade(config, "b91f6d82a304")
        assert "repetition_generation_receipt" not in inspect(engine).get_table_names()
        with Session(engine) as session:
            assert (
                session.scalar(select(Plan.name).where(Plan.plan_id == plan_id)) == "Keep this plan"
            )
        command.upgrade(config, "head")
    finally:
        engine.dispose()
