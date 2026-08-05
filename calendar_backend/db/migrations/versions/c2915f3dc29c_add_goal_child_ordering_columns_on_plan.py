"""add goal child ordering columns on plan

Revision ID: c2915f3dc29c
Revises: 7111454550a7
Create Date: 2026-07-25 22:47:30.025996

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2915f3dc29c"
down_revision: str | Sequence[str] | None = "7111454550a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GOAL_CHILD_ORDERING_CHECK = (
    "(goal_is_critical IS NULL AND goal_sort_order IS NULL) "
    "OR (goal_is_critical IS NOT NULL AND goal_sort_order IS NOT NULL "
    "AND goal_sort_order >= 0)"
)

_COPY_GOAL_ORDERING_FROM_CHAINS_SQL = """
WITH ordered_items AS (
    SELECT
        gci.child_plan_id AS plan_id,
        gcc.is_critical AS goal_is_critical,
        CAST(
            ROW_NUMBER() OVER (
                PARTITION BY gcc.parent_goal_id, gcc.is_critical
                ORDER BY gcc.sort_order, gci.position, gci.goal_child_chain_item_id
            ) AS INTEGER
        ) - 1 AS goal_sort_order
    FROM goal_child_chain_item AS gci
    INNER JOIN goal_child_chain AS gcc
        ON gci.chain_id = gcc.goal_child_chain_id
)
UPDATE plan
SET
    goal_is_critical = (
        SELECT ordered_items.goal_is_critical
        FROM ordered_items
        WHERE ordered_items.plan_id = plan.plan_id
    ),
    goal_sort_order = (
        SELECT ordered_items.goal_sort_order
        FROM ordered_items
        WHERE ordered_items.plan_id = plan.plan_id
    )
WHERE plan.plan_id IN (SELECT ordered_items.plan_id FROM ordered_items)
"""

_UNCOPIED_CHAIN_CHILDREN_SQL = """
SELECT COUNT(*)
FROM goal_child_chain_item AS gci
INNER JOIN plan AS p ON p.plan_id = gci.child_plan_id
WHERE p.goal_is_critical IS NULL OR p.goal_sort_order IS NULL
"""


def _copy_goal_ordering_from_chains() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(_COPY_GOAL_ORDERING_FROM_CHAINS_SQL))
    uncopied = connection.execute(sa.text(_UNCOPIED_CHAIN_CHILDREN_SQL)).scalar_one()
    if uncopied:
        msg = f"goal child ordering migration left {uncopied} chain item(s) without flat fields"
        raise RuntimeError(msg)


def _set_sqlite_foreign_keys(enabled: bool) -> None:
    connection = op.get_bind()
    connection.execute(sa.text(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}"))


def _add_goal_ordering_check() -> None:
    _set_sqlite_foreign_keys(False)
    try:
        with op.batch_alter_table("plan", schema=None) as batch_op:
            batch_op.create_check_constraint(
                op.f("ck_plan_goal_child_ordering_fields_paired"),
                _GOAL_CHILD_ORDERING_CHECK,
            )
    finally:
        _set_sqlite_foreign_keys(True)


def _drop_goal_ordering_check() -> None:
    _set_sqlite_foreign_keys(False)
    try:
        with op.batch_alter_table("plan", schema=None) as batch_op:
            batch_op.drop_constraint(
                op.f("ck_plan_goal_child_ordering_fields_paired"),
                type_="check",
            )
    finally:
        _set_sqlite_foreign_keys(True)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    plan_columns = {column["name"] for column in inspector.get_columns("plan")}
    if "goal_is_critical" not in plan_columns:
        op.add_column("plan", sa.Column("goal_is_critical", sa.Boolean(), nullable=True))
    if "goal_sort_order" not in plan_columns:
        op.add_column("plan", sa.Column("goal_sort_order", sa.Integer(), nullable=True))
    _copy_goal_ordering_from_chains()
    inspector = sa.inspect(bind)
    plan_checks = {constraint["name"] for constraint in inspector.get_check_constraints("plan")}
    if op.f("ck_plan_goal_child_ordering_fields_paired") not in plan_checks:
        _add_goal_ordering_check()


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    plan_checks = {constraint["name"] for constraint in inspector.get_check_constraints("plan")}
    if op.f("ck_plan_goal_child_ordering_fields_paired") in plan_checks:
        _drop_goal_ordering_check()
    plan_columns = {column["name"] for column in inspector.get_columns("plan")}
    if "goal_sort_order" in plan_columns:
        op.drop_column("plan", "goal_sort_order")
    if "goal_is_critical" in plan_columns:
        op.drop_column("plan", "goal_is_critical")
