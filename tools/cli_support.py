"""Shared helpers for the development CLI."""

from __future__ import annotations

import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from calendar_backend.db.session import (
    DEFAULT_DATABASE_URL,
    create_engine_for_url,
    create_session_factory,
)
from calendar_backend.domain.dtos import AppSettingsDTO, GoalPlanDTO
from calendar_backend.domain.results import ServiceResult
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

DATABASE_URL = DEFAULT_DATABASE_URL


def database_path_from_url(url: str = DATABASE_URL) -> Path:
    return Path(unquote(url.removeprefix("sqlite:///")))


def alembic_config(ini_path: str = "alembic.ini") -> Config:
    path = Path(ini_path)
    if not path.is_file():
        msg = f"{path} not found; run calendar-backend-dev from the repository root"
        raise FileNotFoundError(msg)
    return Config(str(path))


def read_current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        return context.get_current_revision()


def upgrade_head(url: str = DATABASE_URL) -> str:
    create_engine_for_url(url)
    config = alembic_config()
    command.upgrade(config, "head")
    engine = create_engine_for_url(url)
    try:
        revision = read_current_revision(engine)
    finally:
        engine.dispose()
    if revision is None:
        msg = "Alembic upgrade completed but no revision recorded"
        raise RuntimeError(msg)
    return revision


def current_revision(url: str = DATABASE_URL) -> str | None:
    engine = create_engine_for_url(url)
    try:
        return read_current_revision(engine)
    finally:
        engine.dispose()


def delete_database_file_if_exists(url: str = DATABASE_URL) -> bool:
    db_path = database_path_from_url(url)
    if db_path.exists():
        db_path.unlink()
        return True
    return False


@contextmanager
def with_session(url: str = DATABASE_URL) -> Generator[Session]:
    engine = create_engine_for_url(url)
    session = create_session_factory(engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def print_service_result[T](result: ServiceResult[T]) -> T | None:
    if result.success:
        return result.value
    for error in result.errors:
        line = f"{error.code}: {error.message}"
        if error.details:
            details = ", ".join(f"{key}={value}" for key, value in error.details.items())
            line = f"{line} ({details})"
        print(line, file=sys.stderr)
    return None


def print_goal_plan_dto(dto: GoalPlanDTO) -> None:
    print(f"plan_id: {dto.plan_id}")
    print(f"name: {dto.name}")
    print(f"is_master: {dto.is_master}")
    parent_id = dto.parent_id if dto.parent_id is not None else "(none)"
    print(f"parent_id: {parent_id}")
    print(f"created_at: {dto.created_at.isoformat()}")
    print(f"updated_at: {dto.updated_at.isoformat()}")


def print_app_settings_dto(dto: AppSettingsDTO) -> None:
    print(f"local_timezone: {dto.local_timezone}")
    print(f"master_horizon_duration_minutes: {dto.master_horizon_duration_minutes}")
    print(f"exact_solver_time_limit_seconds: {dto.exact_solver_time_limit_seconds}")
    print(f"exact_solver_model_size_limit: {dto.exact_solver_model_size_limit}")
    print(f"heuristic_enabled: {dto.heuristic_enabled}")
    print(f"free_time_week_start_day: {dto.free_time_week_start_day.value}")
    print(f"updated_at: {dto.updated_at.isoformat()}")
