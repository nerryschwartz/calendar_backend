# Repo-Local Claude Instructions

These instructions apply when Claude works in this repository.

## Pre-Commit Workflow

Before committing repository changes, use this workflow:

1. Run a commit-readiness audit of the current working diff, following the intent of `.cursor/commands/audit-commit-readiness.md`.
2. Run an abstraction review, following the intent of `.cursor/commands/review-abstractions.md`. This is part of this repo's pre-commit checks.
3. Run the local validation commands from `.cursor/rules/20-testing-and-checks.mdc`. This is also part of this repo's pre-commit checks:
   - `uv run ruff format .`
   - `uv run pytest -m "not slow and not failure_expected"`

For this repository, the pre-commit checks are steps 2 and 3: the abstraction review and the local validation command sequence.

## Test Markers

- Mark tests with `@pytest.mark.slow` when they take more than 1 second on the local suite.
- Mark tests with `@pytest.mark.failure_expected` only when a change makes the test temporarily fail by design and the test is expected to pass again before the pull request is complete.
- Remove `failure_expected` before completing the pull request.
- During branch-loop work, non-final passes should run only tests/checks relevant to the current change, including slow tests when they are relevant.
- The final branch-loop pass must run the full default validation suite: `uv run ruff format .` and `uv run pytest -m "not slow and not failure_expected"`.
