Generate and review an Alembic migration draft. Stop before applying it.

Use after SQLAlchemy model changes and before manual migration approval.

Prerequisites:
- Model changes are already in the working tree.
- Alembic is configured under `calendar_backend/db/migrations/`.
- `env.py` imports all model modules so autogenerate sees full metadata.

Before running autogenerate:
1. If the migration message is unclear, ask for a short `-m` message and stop.
2. Run pre-autogenerate checks:
   uv run ruff format .
   uv run ruff check .
   uv run pyright
3. Generate the migration:
   uv run alembic revision --autogenerate -m "<message>"

After autogenerate:
- Show the generated file path under `calendar_backend/db/migrations/versions/`.
- Review the migration as a draft using the §8.6 checklist from `docs/cursor_implementation_guide.md`:
  - expected tables present; unexpected tables absent
  - constraints named and meaningful
  - nullable settings correct
  - enum/string values as intended
  - FK constraints correct
  - indexes necessary, not speculative
  - downgrade reverses the migration
- Flag §8.12 risks when relevant (drop/add instead of rename, empty migration, app imports in migration, etc.).

Do not:
- run `alembic upgrade` or `downgrade`
- run `/commit-changes`
- run `/review-abstractions`
- fix unrelated failing tests

Stop after preview output and wait for manual migration approval.
The next step is `/db-revision-continue` after the migration script is edited and approved.
