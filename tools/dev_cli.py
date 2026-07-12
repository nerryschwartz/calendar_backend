"""Thin development CLI for local manual smoke testing.

Commands call public calendar_backend services. Do not put business logic here
that is not also available through the importable backend package.
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="calendar-backend-dev",
        description="Local development CLI for calendar_backend smoke testing.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    db_parser = subparsers.add_parser("db", help="Database initialization and status")
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)
    db_subparsers.add_parser("init", help="Apply Alembic migrations to head")
    db_subparsers.add_parser("status", help="Show database path and Alembic revision")
    db_subparsers.add_parser(
        "reset",
        help="Delete the database file and re-apply migrations (empty schema)",
    )

    master_parser = subparsers.add_parser("master", help="Master plan inspection")
    master_subparsers = master_parser.add_subparsers(dest="master_command", required=True)
    master_subparsers.add_parser("show", help="Show the master goal plan")

    settings_parser = subparsers.add_parser("settings", help="App settings inspection")
    settings_subparsers = settings_parser.add_subparsers(
        dest="settings_command",
        required=True,
    )
    settings_subparsers.add_parser("show", help="Show persisted app settings")

    refresh_parser = subparsers.add_parser("refresh", help="Schedule refresh workflows")
    refresh_subparsers = refresh_parser.add_subparsers(dest="refresh_command", required=True)
    schedule_parser = refresh_subparsers.add_parser(
        "schedule",
        help="Run OrchestrationService.refresh_schedule",
    )
    schedule_parser.add_argument(
        "--run-started-at",
        dest="run_started_at",
        default=None,
        metavar="ISO",
        help="UTC minute-aligned ISO-8601 timestamp (default: current time)",
    )

    return parser


def _stub_not_implemented(command: str) -> int:
    print(f"Command not implemented yet: {command}", file=sys.stderr)
    return 1


def _cmd_db_init(_args: argparse.Namespace) -> int:
    return _stub_not_implemented("db init")


def _cmd_db_status(_args: argparse.Namespace) -> int:
    return _stub_not_implemented("db status")


def _cmd_db_reset(_args: argparse.Namespace) -> int:
    return _stub_not_implemented("db reset")


def _cmd_master_show(_args: argparse.Namespace) -> int:
    return _stub_not_implemented("master show")


def _cmd_settings_show(_args: argparse.Namespace) -> int:
    return _stub_not_implemented("settings show")


def _cmd_refresh_schedule(_args: argparse.Namespace) -> int:
    return _stub_not_implemented("refresh schedule")


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
