# Repo-Local Codex Instructions

These instructions apply when Codex works in this repository.

## Pre-Commit Workflow

Before committing repository changes, use this workflow:

1. Run a commit-readiness audit of the current working diff, following the intent of `.cursor/commands/audit-commit-readiness.md`.
2. Run an abstraction review, following the intent of `.cursor/commands/review-abstractions.md`. This is part of this repo's pre-commit checks.
3. Run the local validation commands from `.cursor/rules/20-testing-and-checks.mdc`. This is also part of this repo's pre-commit checks:
   - `uv run ruff format .`
   - `uv run pytest -m "not slow and not failure_expected"`

For this repository, the pre-commit checks are steps 2 and 3: the abstraction review and the local validation command sequence.
