# Repository code conventions

These conventions take precedence over `docs/calendar_backend_v1_engineering_design_updated.pdf`, `docs/cursor_implementation_guide.md`, finalized plans in `docs/plans/`, and existing code.

Add or change conventions only via [`/add-repo-convention`](commands/add-repo-convention.md).

---

## 1. Colocate service bootstrap defaults with the mutating service

**Scope:** Service-layer bootstrap and persisted-setting defaults only (not `db/`, domain, or test fixtures).

**Rule:** Define `DEFAULT_*` and similar bootstrap constants in the same module as the service that inserts or updates those persisted values. Do not create separate defaults/config packages for service bootstrap values.

**Examples:**
- `MASTER_PLAN_NAME` in `calendar_backend/services/master_plan.py`
- `DEFAULT_LOCAL_TIMEZONE`, `DEFAULT_MASTER_HORIZON_DURATION_MINUTES`, etc. in `calendar_backend/services/app_settings.py`

**Supersedes:** Engineering design PDF §4 “static defaults package” for service bootstrap defaults; finalized plans that reference `calendar_backend/settings/defaults.py`.

---

## 2. No pre-transaction persistence reads in mutating service methods

**Scope:** Service methods that mutate persistence (insert, update, delete) inside `transaction(session)`.

**Rule:** Do not read from `self._session` (or the injected session) before entering `with transaction(self._session)`. Perform reads inside the transaction block, including idempotency checks.

**Rationale:** Avoids split-brain between a pre-transaction read and transactional writes; keeps bootstrap/mutate paths consistent.

**Example:** `MasterPlanService.ensure_master_exists()` selects the master plan inside `transaction(...)`, not on `self._session` first.

**Does not apply to:** Read-only service methods that never mutate persistence in that call.

---

## 3. ORM relationships for graph reads; explicit SQL for filtered writes

**Scope:** Service-layer persistence access (`calendar_backend/services/`) and similar orchestration that uses a SQLAlchemy `Session`. ORM models still define `relationship()` for navigation; scheduling and domain layers stay session-free.

**Rule:**

- **Prefer ORM relationship navigation** when the task is **read-oriented**: traversing or validating a linked graph (invariants, deletion previews, tree walks, loading a coherent object graph before mapping to DTOs). Use explicit eager loading (`selectinload` / `joinedload`) when loading a graph in one service call — do not rely on unbounded lazy loading in loops.
- **Prefer explicit `select` / `delete` / `session.get`** when the task is **write-oriented or narrowly filtered**: bootstrap/idempotency by key, upsert/replace of a specific row kind, heavy `WHERE` filters (e.g. one `constraint_kind`), or bulk delete/replace where models have **no** cascade delete configured.

**Examples:**

- **Relationships:** Prompt 7 plan-tree invariant checks via `Plan.children`, `GoalPlan.chains` → `items` → `child_plan`; slice 5 asserting master horizon via `Plan.constraint_groups` → `windows`.
- **Explicit SQL:** `MasterHorizonService` selects `TimeConstraintGroup` by `(plan_id, SYSTEM_MASTER_HORIZON)` and `delete(TimeWindow)`; `MasterPlanService` selects master by `is_master`; `AppSettingsService` uses `session.get(AppSettings, 1)`.

**Rationale:** Relationships match how we explore structure; explicit SQL matches surgical mutations and avoids loading unrelated collections. Models are persistence records without cascade delete on plan/calendar trees — relationship collections do not replace explicit deletes on mutate paths.

**Aligns with:** Guide §0.2 (wire `relationship()` on ORM); layer boundaries (models own mappings, services own persistence-changing behavior).

---

## 4. Alembic revision file style (SQLite)

**Scope:** Hand-written and reviewed files under `calendar_backend/db/migrations/versions/`. Autogenerate output must be normalized to this style before `/db-revision-continue`.

**Rule:**

- Start with `from __future__ import annotations`.
- Import `Sequence` from `collections.abc` (not `typing.Sequence` / `typing.Union`). Type revision identifiers as `str | Sequence[str] | None`.
- Import `sqlalchemy as sa` when using SQLAlchemy types or `sa.text(...)`; import `op` from `alembic`.
- Do **not** import application packages (`calendar_backend.services`, `domain`, ORM models for runtime use). Migrations are schema/data SQL only.
- **Alter existing SQLite tables** (add/drop column, add/drop CHECK on existing table): use `with op.batch_alter_table("<table>", schema=None) as batch_op:` and call methods on `batch_op`.
- **Create/drop tables or indexes** on new or unchanged table definitions: use top-level `op.create_table`, `op.create_index`, `op.drop_index`, etc. (batch mode not required).
- Match sibling revisions: multi-line argument lists, `op.f(...)` for generated constraint names where used elsewhere, partial indexes with `sqlite_where=sa.text(...)`.
- Data backfills or deduplication before constraints: use `connection = op.get_bind()` and `connection.execute(sa.text(...))` in helper functions; keep upgrade/downgrade reversible when practical.

**Examples:**

- [`7e137c1ddfb0`](../../calendar_backend/db/migrations/versions/7e137c1ddfb0_remove_granularity_from_app_settings.py) — `batch_alter_table` for column drop/add.
- [`e6e01e97df46`](../../calendar_backend/db/migrations/versions/e6e01e97df46_add_repetition_plan_check_constraints.py) — `batch_alter_table` for CHECK constraints.
- [`522f4501f06a`](../../calendar_backend/db/migrations/versions/522f4501f06a_add_partial_unique_index_for_system_.py) — data cleanup via `op.get_bind()`, then `op.create_index` with `sqlite_where`.

**Aligns with:** Guide §8.10 (SQLite batch mode); `/db-revision-preview` manual edit step.
