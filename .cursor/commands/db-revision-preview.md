Generate and review an Alembic migration draft. Stop before applying it.

Use after SQLAlchemy model changes and before manual migration approval.

Prerequisites:
- Model changes are already in the working tree.
- Alembic is configured under `calendar_backend/db/migrations/`.
- If the model change adds DB-level enforcement (CHECK, UNIQUE, etc.), schema tests for that enforcement should already exist and be marked `failure_expected` per [repo convention §13](../repo_conventions.md) until this revision is applied.

## 1. Wire model imports in `env.py`

Before autogenerate, ensure `calendar_backend/db/migrations/env.py` imports every ORM module that should register tables on `Base.metadata`.

1. Inspect `calendar_backend/models/` for submodules that define mapped classes (for example `plans.py`).
2. Read current `env.py` imports.
3. Add missing side-effect imports only:
   ```python
   from calendar_backend.models import plans  # noqa: F401
   ```
   - Import from owning submodules (per package re-export policy), not barrel re-exports from `models/__init__.py`.
   - Include only modules whose tables belong in this revision (typically all model modules that exist so far).
   - Do not import future or unrelated modules.
4. Keep `target_metadata = Base.metadata` unchanged unless the repo already uses a different approved pattern.

If `env.py` changed, run on the narrowest scope:
```bash
uv run ruff format calendar_backend/db/migrations/env.py
uv run ruff check calendar_backend/db/migrations/env.py
uv run pyright
```

If the migration message is unclear, ask for a short `-m` message and stop.

## 2. Pre-autogenerate checks

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

## 3. Generate the migration

```bash
uv run alembic revision --autogenerate -m "<message>"
```

Show the generated file path under `calendar_backend/db/migrations/versions/`.

## 4. Agent migration review (report only)

Read the full generated revision file and produce a structured review for the user.

**Do not edit the migration file.** Report red flags and likely needed changes; the user applies edits manually before `/db-revision-continue`.

Use the §8.6 checklist from `docs/cursor_implementation_guide.md`:

- expected tables present; unexpected tables absent
- constraints named and meaningful (compare to `NAMING_CONVENTION` in `calendar_backend/db/base.py`)
- nullable settings correct
- enum/string values as intended (`native_enum=False` → strings in SQLite)
- FK constraints correct (targets, ondelete if present)
- indexes necessary, not speculative
- partial/filtered unique indexes present and correct (especially SQLite `sqlite_where`)
- downgrade reverses the upgrade when practical

Flag §8.12 risks when relevant:

- empty or near-empty migration (often missing `env.py` imports)
- drop/add instead of rename
- app/service imports inside the migration
- SQLite batch-mode needs for ALTER operations — use `op.batch_alter_table(..., schema=None)` per [repo convention §4](../repo_conventions.md)
- autogenerate style not normalized (`typing.Union`, missing `from __future__ import annotations`)
- multiple `Base` metadata sources
- speculative indexes or constraints not reflected in models

After autogenerate, normalize the revision file to [repo convention §4](../repo_conventions.md) during manual review (before `/db-revision-continue`).

Review output format:

```markdown
## Migration preview: <revision> — <message>

**File:** `calendar_backend/db/migrations/versions/<file>.py`

### Summary
- upgrade: <brief: tables created/altered/dropped>
- downgrade: <reversible? notes>

### Checklist (§8.6)
- [ ] / [x] <item> — <one-line note>

### Red flags
- <severity> — <issue> — <why it matters>

### Likely manual edits (user applies)
1. <file or function> — <what to change and suggested direction, not a full rewrite>

### env.py
- <imports added or already sufficient>
```

If no red flags or manual edits are likely, say so explicitly.

## Do not

- run `alembic upgrade` or `downgrade`
- edit the generated migration file (user edits after review)
- run `/commit-changes`
- run `/review-abstractions`
- fix unrelated failing tests

Stop after preview output and wait for manual migration approval.

The user edits the migration script as needed, then runs `/db-revision-continue`.
