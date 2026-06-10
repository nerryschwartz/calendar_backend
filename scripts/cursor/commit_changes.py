"""Commit helper for Cursor-driven development.

This script intentionally keeps humans in the loop for staging and commit
boundaries. It runs checks, shows the diff, suggests a strict commit rubric,
and then opens interactive staging.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence


def run(cmd: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print(f"\n$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, text=True)


def capture(cmd: Sequence[str]) -> str:
    return subprocess.check_output(cmd, text=True).strip()


def has_changes() -> bool:
    result = subprocess.run(["git", "diff", "--quiet"], check=False)
    unstaged = result.returncode != 0
    result_cached = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
    staged = result_cached.returncode != 0
    return unstaged or staged


def run_checks(*, skip_tests: bool) -> None:
    run(["uv", "run", "ruff", "format", "."])
    run(["uv", "run", "ruff", "check", "."])
    run(["uv", "run", "pyright"])

    if skip_tests:
        print("\nTests skipped for this invocation only.")
    else:
        run(["uv", "run", "pytest", "-m", "not slow and not failure_expected"])


def run_staging_loop() -> int:
    while True:
        answer = input("Open interactive staging now? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Stopping before staging.")
            return 0

        run(["git", "add", "-p"], check=False)
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False).returncode != 0

        if not staged:
            print("No staged changes.")
        else:
            print("\n## Staged diff")
            run(["git", "diff", "--cached", "--stat"], check=False)
            run(["git", "diff", "--cached", "--name-status"], check=False)

            message = input("Commit message, or blank to skip commit: ").strip()
            if message:
                run(["git", "commit", "-m", message])

        if not has_changes():
            print("All changes committed.")
            return 0

        again = input("Continue staging another commit? [y/N] ").strip().lower()
        if again not in {"y", "yes"}:
            print("Remaining changes left uncommitted.")
            return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip pytest for this invocation only.",
    )
    parser.add_argument(
        "--skip-checks",
        action="store_true",
        help="Skip ruff, pyright, and pytest for this invocation only.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show status and suggested process without staging or committing.",
    )
    args = parser.parse_args()

    if not has_changes():
        print("No changes to commit.")
        return 0

    if args.skip_checks:
        print("\nChecks skipped for this invocation only.")
    else:
        run_checks(skip_tests=args.skip_tests)

    print("\n## Current diff summary")
    run(["git", "status", "--short"], check=False)
    run(["git", "diff", "--stat"], check=False)
    run(["git", "diff", "--name-status"], check=False)

    print(
        """
## Commit splitting rubric

Prefer separate commits when changes differ by:
- behavior vs tests
- production code vs refactor
- config/dependency changes vs app code
- generated files vs source files
- docs vs implementation
- schema migration vs consumers
- mechanical rename vs semantic behavior change

Use `git add -p` for patch-level staging.
"""
    )

    if args.dry_run:
        return 0

    return run_staging_loop()


if __name__ == "__main__":
    sys.exit(main())
