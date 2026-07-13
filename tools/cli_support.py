"""Shared helpers for the development CLI."""

from __future__ import annotations

import sys
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
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
from calendar_backend.domain.errors import ServiceMessage
from calendar_backend.domain.orchestration import RefreshScheduleResult
from calendar_backend.domain.results import ServiceResult
from calendar_backend.domain.time import (
    Clock,
    is_minute_aligned,
    require_utc,
    truncate_to_minute,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

DATABASE_URL = DEFAULT_DATABASE_URL


class RunStartedAtError(ValueError):
    """Invalid --run-started-at value for the development CLI."""


def _service_message_line(message: ServiceMessage) -> str:
    line = f"{message.code}: {message.message}"
    if message.details:
        details = ", ".join(f"{key}={value}" for key, value in message.details.items())
        line = f"{line} ({details})"
    return line


def database_path_from_url(url: str | None = None) -> Path:
    if url is None:
        url = DATABASE_URL
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


def upgrade_head(url: str | None = None) -> str:
    if url is None:
        url = DATABASE_URL
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


def current_revision(url: str | None = None) -> str | None:
    if url is None:
        url = DATABASE_URL
    engine = create_engine_for_url(url)
    try:
        return read_current_revision(engine)
    finally:
        engine.dispose()


def delete_database_file_if_exists(url: str | None = None) -> bool:
    if url is None:
        url = DATABASE_URL
    db_path = database_path_from_url(url)
    if db_path.exists():
        db_path.unlink()
        return True
    return False


@contextmanager
def with_session(url: str | None = None) -> Generator[Session]:
    if url is None:
        url = DATABASE_URL
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
    _print_service_errors(result.errors)
    return None


def parse_run_started_at(raw: str | None, clock: Clock) -> datetime:
    if raw is None:
        return truncate_to_minute(clock.now_utc())

    iso_value = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(iso_value)
    except ValueError as exc:
        msg = f"invalid ISO-8601 timestamp: {raw}"
        raise RunStartedAtError(msg) from exc

    try:
        require_utc(parsed)
    except ValueError as exc:
        msg = "run_started_at must be timezone-aware UTC"
        raise RunStartedAtError(msg) from exc

    if not is_minute_aligned(parsed):
        msg = "run_started_at must be minute-aligned"
        raise RunStartedAtError(msg)

    return parsed


def _print_service_errors(errors: tuple[ServiceMessage, ...]) -> None:
    for error in errors:
        print(_service_message_line(error), file=sys.stderr)


def _print_warnings(label: str, warnings: tuple[ServiceMessage, ...]) -> None:
    for warning in warnings:
        print(f"warning[{label}]: {_service_message_line(warning)}")


def print_refresh_schedule_summary(result: ServiceResult[RefreshScheduleResult]) -> None:
    print(f"success: {result.success}")
    if result.value is None:
        _print_warnings("refresh", result.warnings)
        if not result.success:
            _print_service_errors(result.errors)
        return

    payload = result.value
    print(f"run_started_at: {payload.run_started_at.isoformat()}")
    if payload.resolved is not None:
        print(f"valid_incomplete_count: {len(payload.resolved.valid_incomplete)}")
        print(f"invalid_incomplete_count: {len(payload.resolved.invalid_incomplete)}")
        _print_warnings("resolved", payload.resolved.warnings)
    if payload.assignment is not None:
        print(f"optimization_status: {payload.assignment.optimization_status.value}")
        print(f"task_entry_count: {len(payload.assignment.calendar_entries)}")
        _print_warnings("assignment", payload.assignment.warnings)
    if payload.free_time is not None:
        print(f"free_time_entry_count: {len(payload.free_time.calendar_entries)}")
        _print_warnings("free_time", payload.free_time.warnings)
    _print_warnings("refresh", result.warnings)
    if not result.success:
        _print_service_errors(result.errors)


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
