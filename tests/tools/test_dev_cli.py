from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.orchestration import RefreshScheduleResult
from calendar_backend.domain.resolution import ResolveTasksResult
from calendar_backend.domain.results import ServiceResult, fail, ok
from sqlalchemy import create_engine, text
from tools.dev_cli import _dispatch, build_parser  # pyright: ignore[reportPrivateUsage]

from tools import cli_support

APPLICATION_TABLES = (
    "plan",
    "goal_plan",
    "task_plan",
    "repetition_plan",
    "goal_child_chain",
    "goal_child_chain_item",
    "time_constraint_group",
    "time_window",
    "repetition_instance",
    "calendar_entry",
    "free_time_activity",
    "free_time_activity_prerequisite",
    "calendar_run",
    "active_calendar_state",
    "app_settings",
)

RUN_AT = datetime(2026, 6, 7, 10, 0, tzinfo=UTC)


def dispatch(argv: list[str]) -> int:
    return _dispatch(build_parser().parse_args(argv))


def assert_empty_application_schema(url: str) -> None:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            for table in APPLICATION_TABLES:
                count = connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                assert count == 0
    finally:
        engine.dispose()


def test_build_parser_root_help_lists_subcommands() -> None:
    help_text = build_parser().format_help()
    assert "{db,master,settings,refresh}" in help_text


def test_build_parser_db_subcommands() -> None:
    args = build_parser().parse_args(["db", "init"])
    assert args.command == "db"
    assert args.db_command == "init"


def test_build_parser_refresh_schedule_accepts_run_started_at() -> None:
    args = build_parser().parse_args(
        ["refresh", "schedule", "--run-started-at", "2026-06-07T10:00:00+00:00"]
    )
    assert args.command == "refresh"
    assert args.refresh_command == "schedule"
    assert args.run_started_at == "2026-06-07T10:00:00+00:00"


def test_dispatch_db_init_empty_schema(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = dispatch(["db", "init"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Database initialized at head revision:" in captured.out
    assert_empty_application_schema(cli_support.DATABASE_URL)


def test_dispatch_db_reset_empty_schema(capsys: pytest.CaptureFixture[str]) -> None:
    assert dispatch(["db", "init"]) == 0
    exit_code = dispatch(["db", "reset"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Database reset; empty schema at head revision:" in captured.out
    assert_empty_application_schema(cli_support.DATABASE_URL)


def test_dispatch_db_status_after_init(capsys: pytest.CaptureFixture[str]) -> None:
    assert dispatch(["db", "init"]) == 0
    exit_code = dispatch(["db", "status"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "database_exists: True" in captured.out
    assert "alembic_revision:" in captured.out
    assert "(no database file)" not in captured.out


def test_dispatch_db_status_missing_file(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = dispatch(["db", "status"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "database_exists: False" in captured.out
    assert "alembic_revision: (no database file)" in captured.out


def test_dispatch_master_show_bootstraps(capsys: pytest.CaptureFixture[str]) -> None:
    assert dispatch(["db", "init"]) == 0
    exit_code = dispatch(["master", "show"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "name: master" in captured.out
    assert "is_master: True" in captured.out


def test_dispatch_settings_show_bootstraps(capsys: pytest.CaptureFixture[str]) -> None:
    assert dispatch(["db", "init"]) == 0
    exit_code = dispatch(["settings", "show"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "local_timezone: UTC" in captured.out
    assert "heuristic_enabled: True" in captured.out


def test_dispatch_master_show_service_failure_prints_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert dispatch(["db", "init"]) == 0

    def fake_ensure_master_exists(self: object) -> ServiceResult[object]:
        del self
        return fail(
            ServiceMessage(
                code=MessageCode.PLAN_NOT_FOUND,
                message="master missing",
            )
        )

    monkeypatch.setattr(
        "tools.dev_cli.MasterPlanService.ensure_master_exists",
        fake_ensure_master_exists,
    )
    exit_code = dispatch(["master", "show"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "PLAN_NOT_FOUND" in captured.err


def test_dispatch_settings_show_service_failure_prints_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert dispatch(["db", "init"]) == 0

    def fake_get_settings(self: object) -> ServiceResult[object]:
        del self
        return fail(
            ServiceMessage(
                code=MessageCode.INVALID_MASTER_PLAN,
                message="settings unavailable",
            )
        )

    monkeypatch.setattr(
        "tools.dev_cli.AppSettingsService.get_settings",
        fake_get_settings,
    )
    exit_code = dispatch(["settings", "show"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "INVALID_MASTER_PLAN" in captured.err


def test_dispatch_refresh_schedule_rejects_sub_minute(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = dispatch(["refresh", "schedule", "--run-started-at", "2026-06-07T10:00:01+00:00"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "minute-aligned" in captured.err


def test_dispatch_refresh_schedule_rejects_non_utc(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = dispatch(["refresh", "schedule", "--run-started-at", "2026-06-07T10:00:00+05:00"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "UTC" in captured.err


def test_dispatch_refresh_schedule_stubbed_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert dispatch(["db", "init"]) == 0

    def fake_refresh_schedule(
        self: object,
        run_started_at: datetime,
    ) -> ServiceResult[RefreshScheduleResult]:
        del self
        return ok(
            RefreshScheduleResult(
                run_started_at=run_started_at,
                resolved=ResolveTasksResult(
                    run_started_at=run_started_at,
                    valid_incomplete=(),
                    valid_completed=(),
                    invalid_incomplete=(),
                    invalid_completed=(),
                    precedence_constraints=(),
                    warnings=(),
                ),
                assignment=None,
                free_time=None,
            )
        )

    monkeypatch.setattr(
        "tools.dev_cli.OrchestrationService.refresh_schedule",
        fake_refresh_schedule,
    )
    exit_code = dispatch(["refresh", "schedule", "--run-started-at", "2026-06-07T10:00:00+00:00"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "success: True" in captured.out
    assert "valid_incomplete_count: 0" in captured.out


def test_dispatch_refresh_schedule_stubbed_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert dispatch(["db", "init"]) == 0
    resolved = ResolveTasksResult(
        run_started_at=RUN_AT,
        valid_incomplete=(),
        valid_completed=(),
        invalid_incomplete=(),
        invalid_completed=(),
        precedence_constraints=(),
        warnings=(),
    )

    def fake_refresh_schedule(
        self: object,
        run_started_at: datetime,
    ) -> ServiceResult[RefreshScheduleResult]:
        del self
        return fail(
            ServiceMessage(
                code=MessageCode.INVALID_INCOMPLETE_TASKS_BLOCK_ASSIGNMENT,
                message="blocked",
            ),
            _value=RefreshScheduleResult(
                run_started_at=run_started_at,
                resolved=resolved,
                assignment=None,
                free_time=None,
            ),
        )

    monkeypatch.setattr(
        "tools.dev_cli.OrchestrationService.refresh_schedule",
        fake_refresh_schedule,
    )
    exit_code = dispatch(["refresh", "schedule", "--run-started-at", "2026-06-07T10:00:00+00:00"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "success: False" in captured.out
    assert "valid_incomplete_count: 0" in captured.out
    assert "INVALID_INCOMPLETE_TASKS_BLOCK_ASSIGNMENT" in captured.err


def test_subprocess_root_help_exits_zero(project_root: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tools.dev_cli", "--help"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "calendar-backend-dev" in result.stdout


def test_subprocess_refresh_schedule_help_exits_zero(project_root: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tools.dev_cli", "refresh", "schedule", "--help"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--run-started-at" in result.stdout
