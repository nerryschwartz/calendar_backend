"""Thin development CLI for local manual smoke testing.

Commands call public calendar_backend services. Do not put business logic here
that is not also available through the importable backend package.
"""

from __future__ import annotations

import argparse
import sys

from alembic.util.exc import CommandError
from calendar_backend.domain.time import SystemClock
from calendar_backend.orchestration.refresh_schedule import OrchestrationService
from calendar_backend.services.app_settings import AppSettingsService
from calendar_backend.services.master_plan import MasterPlanService

from tools import cli_support


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="calendar-backend-dev",
        description="Local development CLI for calendar_backend smoke testing.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    db_parser = subparsers.add_parser(
        "db",
        help="Database initialization and status (run from repository root)",
    )
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)
    db_subparsers.add_parser("init", help="Apply Alembic migrations to head")
    db_subparsers.add_parser("status", help="Show database path and Alembic revision")
    db_subparsers.add_parser(
        "reset",
        help="Delete the database file and re-apply migrations (empty schema)",
    )

    master_parser = subparsers.add_parser("master", help="Master plan inspection")
    master_subparsers = master_parser.add_subparsers(dest="master_command", required=True)
    master_subparsers.add_parser(
        "show",
        help="Show the master goal plan (bootstraps master row on first run)",
    )

    settings_parser = subparsers.add_parser("settings", help="App settings inspection")
    settings_subparsers = settings_parser.add_subparsers(
        dest="settings_command",
        required=True,
    )
    settings_subparsers.add_parser(
        "show",
        help="Show persisted app settings (bootstraps defaults on first run)",
    )

    refresh_parser = subparsers.add_parser("refresh", help="Schedule refresh workflows")
    refresh_subparsers = refresh_parser.add_subparsers(dest="refresh_command", required=True)
    schedule_parser = refresh_subparsers.add_parser(
        "schedule",
        help=(
            "Run OrchestrationService.refresh_schedule "
            "(bootstraps master/settings; empty tree may fail)"
        ),
    )
    schedule_parser.add_argument(
        "--run-started-at",
        dest="run_started_at",
        default=None,
        metavar="ISO",
        help="UTC minute-aligned ISO-8601 timestamp (default: current time)",
    )

    return parser


def _cmd_db_init(_args: argparse.Namespace) -> int:
    try:
        revision = cli_support.upgrade_head()
    except (CommandError, FileNotFoundError, OSError, RuntimeError) as exc:
        print(f"db init failed: {exc}", file=sys.stderr)
        return 1
    print(f"Database initialized at head revision: {revision}")
    return 0


def _cmd_db_status(_args: argparse.Namespace) -> int:
    db_path = cli_support.database_path_from_url().resolve()
    print(f"database_path: {db_path}")
    exists = db_path.is_file()
    print(f"database_exists: {exists}")
    if not exists:
        print("alembic_revision: (no database file)")
        return 0
    try:
        revision = cli_support.current_revision()
    except OSError as exc:
        print(f"db status failed: {exc}", file=sys.stderr)
        return 1
    if revision is None:
        print("alembic_revision: (none)")
    else:
        print(f"alembic_revision: {revision}")
    return 0


def _cmd_db_reset(_args: argparse.Namespace) -> int:
    try:
        cli_support.delete_database_file_if_exists()
        revision = cli_support.upgrade_head()
    except (CommandError, FileNotFoundError, OSError, RuntimeError) as exc:
        print(f"db reset failed: {exc}", file=sys.stderr)
        return 1
    print(f"Database reset; empty schema at head revision: {revision}")
    return 0


def _cmd_master_show(_args: argparse.Namespace) -> int:
    try:
        with cli_support.with_session() as session:
            result = MasterPlanService(session, SystemClock()).ensure_master_exists()
    except OSError as exc:
        print(f"master show failed: {exc}", file=sys.stderr)
        return 1
    dto = cli_support.print_service_result(result)
    if dto is None:
        return 1
    cli_support.print_goal_plan_dto(dto)
    return 0


def _cmd_settings_show(_args: argparse.Namespace) -> int:
    try:
        with cli_support.with_session() as session:
            result = AppSettingsService(session, SystemClock()).get_settings()
    except OSError as exc:
        print(f"settings show failed: {exc}", file=sys.stderr)
        return 1
    dto = cli_support.print_service_result(result)
    if dto is None:
        return 1
    cli_support.print_app_settings_dto(dto)
    return 0


def _cmd_refresh_schedule(args: argparse.Namespace) -> int:
    clock = SystemClock()
    try:
        run_started_at = cli_support.parse_run_started_at(args.run_started_at, clock)
    except cli_support.RunStartedAtError as exc:
        print(f"refresh schedule failed: {exc}", file=sys.stderr)
        return 1
    try:
        with cli_support.with_session() as session:
            result = OrchestrationService(session, clock).refresh_schedule(run_started_at)
    except OSError as exc:
        print(f"refresh schedule failed: {exc}", file=sys.stderr)
        return 1
    cli_support.print_refresh_schedule_summary(result)
    return 0 if result.success else 1


def _dispatch(args: argparse.Namespace) -> int:
    result: int | None = None
    if args.command == "db":
        if args.db_command == "init":
            result = _cmd_db_init(args)
        elif args.db_command == "status":
            result = _cmd_db_status(args)
        elif args.db_command == "reset":
            result = _cmd_db_reset(args)
    elif args.command == "master" and args.master_command == "show":
        result = _cmd_master_show(args)
    elif args.command == "settings" and args.settings_command == "show":
        result = _cmd_settings_show(args)
    elif args.command == "refresh" and args.refresh_command == "schedule":
        result = _cmd_refresh_schedule(args)

    if result is None:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 1
    return result


def main() -> None:
    """Run the development CLI."""
    raise SystemExit(_dispatch(build_parser().parse_args()))


if __name__ == "__main__":
    main()
