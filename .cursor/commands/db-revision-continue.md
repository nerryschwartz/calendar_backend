Apply an approved Alembic migration, verify, then commit.

Use only after `/db-revision-preview` and manual approval of the migration file.

Before applying:
- Confirm the migration file in `calendar_backend/db/migrations/versions/` has been reviewed and edited as needed.
- If approval is unclear, ask one focused question and stop.

Apply and verify:
1. uv run alembic upgrade head
2. uv run pytest -m "not slow and not failure_expected"

If checks fail, report failures and stop. Do not fix failing tests during this command.

Then run the `/commit-changes` workflow with these invocation-local overrides:
- First run `/review-abstractions` on the current diff (required; not part of the db-revision preview/continue checks above).
- Run the commit script with checks skipped because preview/continue already ran them:
  python scripts/cursor/commit_changes.py --skip-checks
- Follow all other `/commit-changes` rules: review the full diff, use patch-level staging where practical, show included/excluded hunks and proposed commit message before each commit, prefer atomic commits.

Typical schema commit scope:
- ORM model changes
- reviewed Alembic revision file
- related schema/migration tests

Do not re-run before commit:
- ruff format/check
- pyright
- pytest

Do not run autogenerate again in this command.
