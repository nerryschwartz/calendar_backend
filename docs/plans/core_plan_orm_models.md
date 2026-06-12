# Plan: Core plan ORM models

**Finalized plan location:** `docs/plans/core_plan_orm_models.md`

## Context

Implement Prompt 4 from [docs/cursor_implementation_guide.md](../cursor_implementation_guide.md): core plan-tree persistence for `calendar_backend` per [docs/calendar_backend_v1_engineering_design_updated.pdf](../calendar_backend_v1_engineering_design_updated.pdf) §6 and Appendix §1–§3.

Design constraints:
- [`calendar_backend/models/`](../../calendar_backend/models/) owns SQLAlchemy table mappings only — **no public mutation behavior** on models (design §4, §11).
- **Subtype pattern:** base `plan` table + one-to-one detail tables (`goal_plan`, `task_plan`, `repetition_plan`) sharing `plan_id` as PK/FK (design §6).
- **Only `GoalPlan` may be master** (`is_master` on `plan`); exactly one master when present (design §6, Appendix §6).
- **Goal child chains** model precedence sequences under goals (`goal_child_chain`, `goal_child_chain_item`).
- Use existing [`calendar_backend/db/base.py`](../../calendar_backend/db/base.py) `Base` + [`NAMING_CONVENTION`](../../calendar_backend/db/base.py); Alembic already wired in [`calendar_backend/db/migrations/env.py`](../../calendar_backend/db/migrations/env.py) without model imports.
- Reuse domain enums from [`calendar_backend/domain/enums.py`](../../calendar_backend/domain/enums.py) (`PlanKind`, `CloneStatus`, `RepeatMode`) for column values.
- [`calendar_backend/domain/ids.py`](../../calendar_backend/domain/ids.py) NewTypes are used at **service boundaries**, not inside ORM column types (store `Uuid(as_uuid=True)` / `UUID`).

**Locked decision (clarification):** Slice 3 DB enforcement covers **practical structural rules** (FKs, partial UNIQUE for master, CHECKs, chain uniqueness). **plan_kind ↔ detail-row pairing** and tree reachability are **deferred** to services + future `PlanTreeInvariantService` (design: “Services validate subtype compatibility”).

Current repo state:
- Database infrastructure plan is complete ([`docs/plans/database_infrastructure.md`](database_infrastructure.md)).
- Domain primitives plan is complete ([`docs/plans/domain_primitives.md`](domain_primitives.md)).
- No `calendar_backend/models/` package yet.
- No Alembic table revisions yet.

Build workflow: use `/build-plan-slice` per slice against this file; stop after each slice for approval.

## Non-goals

- Remaining ORM tables (Prompt 5): constraints, repetition instances, calendar, free time, settings, runs.
- Service-layer plan creation, master bootstrap, tree mutation, or invariant validation logic.
- SQLAlchemy polymorphic joined-table inheritance (use explicit tables + relationships).
- SQLite triggers for cross-table subtype alignment.
- Soft delete, audit/history, plan-type conversion.
- Production HTTP/API or CLI commands beyond schema tests.
- OR-Tools / scheduling code.

## Locked assumptions

- **Module layout:** single [`calendar_backend/models/plans.py`](../../calendar_backend/models/plans.py) for all six tables; empty [`calendar_backend/models/__init__.py`](../../calendar_backend/models/__init__.py) (per [`.cursor/rules/25-package-re-exports.mdc`](../../.cursor/rules/25-package-re-exports.mdc) — no model barrel exports).
- **Table names:** snake_case matching design (`plan`, `goal_plan`, `task_plan`, `repetition_plan`, `goal_child_chain`, `goal_child_chain_item`).
- **UUID columns:** SQLAlchemy 2 `Uuid(as_uuid=True)` mapped to Python `uuid.UUID`.
- **Enums:** SQLAlchemy `Enum(..., native_enum=False, values_callable=...)` backed by domain `StrEnum` values (SQLite/Postgres portable).
- **Timestamps:** `DateTime(timezone=True)`; **no DB defaults** — services set `created_at`/`updated_at` via `Clock` (design §10).
- **Chain membership:** `UNIQUE(child_plan_id)` on `goal_child_chain_item` prevents duplicate chain placement; **not** every tree child must have a chain row (repetition template subtrees use `parent_id` only).
- **Master DB rules:** partial unique index on `plan.is_master` WHERE `is_master = 1`; CHECK `(NOT is_master OR plan_kind = 'GOAL')`.
- **FK delete behavior:** conservative (`RESTRICT` / no cascades that could delete master); deletion semantics are service-owned.
- **Migration:** first revision via `alembic revision --autogenerate`, then hand-review; slice 4 wires model imports into `env.py`.
- **Checks:** slices 1–4 run ruff + pyright; slice 5 adds pytest.

## Slices

### Slice 1: Plan base and subtype detail tables

**Objective:** Add ORM mapped classes for `plan` and the three one-to-one subtype detail tables with columns from design §6.

**Files expected to change:**
- [`calendar_backend/models/__init__.py`](../../calendar_backend/models/__init__.py) (new, empty or docstring only)
- [`calendar_backend/models/plans.py`](../../calendar_backend/models/plans.py) (new — partial: Plan, GoalPlan, TaskPlan, RepetitionPlan)

**Implementation steps:**
1. Create `models/` package with empty `__init__.py`.
2. In `plans.py`, define mapped classes inheriting from `Base`:
   - **`Plan`:** `plan_id` (UUID PK), `plan_kind`, `name`, `parent_id` (nullable FK → `plan.plan_id`), `is_master`, `cloned_from_id` (nullable FK → `plan.plan_id`), `clone_status`, `created_at`, `updated_at`.
   - **`GoalPlan`:** `plan_id` PK/FK → `plan.plan_id` (only column besides relationship placeholders).
   - **`TaskPlan`:** `plan_id` PK/FK; `duration_minutes`, `divisible`, `minimum_chunk_size_minutes`, `user_completed`, `completed_at` (nullable).
   - **`RepetitionPlan`:** `plan_id` PK/FK; `repeat_mode`, `start_time`, `repeat_interval_minutes`, `manual_count` (nullable), `end_time` (nullable), `template_root_id` (FK → `plan.plan_id`), `default_instance_critical`, `generated_at` (nullable).
3. Use domain enums for `plan_kind`, `clone_status`, `repeat_mode`.
4. **No relationships yet** (slice 3); minimal `Mapped`/`mapped_column` only.
5. Models are data containers only — no methods like `mark_complete()`.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- Four mapped classes register four tables on `Base.metadata` (`plan`, `goal_plan`, `task_plan`, `repetition_plan`).
- Column names and nullability match design §6.
- Strict pyright passes; no service/domain imports beyond enums.

**Risks/edge cases:**
- Do not use SQLAlchemy inheritance mappers; detail tables are standalone with shared `plan_id`.
- `template_root_id` and self-FKs on `plan` must use explicit FK targets to avoid mapper ordering issues later.

---

### Slice 2: Goal child chain tables

**Objective:** Add ORM mapped classes for goal child chain headers and items.

**Files expected to change:**
- [`calendar_backend/models/plans.py`](../../calendar_backend/models/plans.py) (extend)

**Implementation steps:**
1. Add **`GoalChildChain`:** `goal_child_chain_id` (UUID PK), `parent_goal_id` (FK → `goal_plan.plan_id`), `is_critical`, `sort_order`, `created_at`, `updated_at`.
2. Add **`GoalChildChainItem`:** `goal_child_chain_item_id` (UUID PK), `chain_id` (FK → `goal_child_chain.goal_child_chain_id`), `child_plan_id` (FK → `plan.plan_id`), `position`.
3. Keep persistence-only — no ordering or chain mutation helpers.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- Both tables appear on `Base.metadata` (six plan/chain tables total).
- FK targets reference correct tables (`goal_plan`, not generic `plan` for parent goal).
- pyright strict passes.

**Risks/edge cases:**
- `parent_goal_id` must FK to `goal_plan.plan_id` to enforce goal-parent typing at schema level.

---

### Slice 3: Relationships and practical DB constraints

**Objective:** Wire SQLAlchemy relationships and add SQLite-friendly constraints enforceable without triggers.

**Files expected to change:**
- [`calendar_backend/models/plans.py`](../../calendar_backend/models/plans.py)

**Implementation steps:**
1. Add **`relationship()`** definitions (read-oriented, no cascade delete from master):
   - `Plan` ↔ subtype (`uselist=False`, `back_populates`).
   - `Plan` self-referential `parent` / `children` via `parent_id`.
   - `GoalPlan` ↔ `GoalChildChain` / chains list.
   - `GoalChildChain` ↔ `GoalChildChainItem`.
   - `GoalChildChainItem` → `Plan` (child).
2. Add table constraints via `__table_args__`:
   - **`CheckConstraint`:** `NOT is_master OR plan_kind = 'GOAL'`.
   - **Partial unique index** on `plan.is_master` WHERE `is_master IS TRUE` (at most one master).
   - **`UniqueConstraint`** on `goal_child_chain_item.child_plan_id`.
   - Optional single-table checks if straightforward: e.g. `position >= 0`, `sort_order >= 0`; `duration_minutes` not checked here (scheduling validation is service-layer).
3. **Do not** add triggers or CHECKs requiring existence of matching detail rows per `plan_kind`.
4. Document in module docstring (one short paragraph) that subtype pairing and tree invariants are service/invariant responsibilities.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- Relationships allow navigating plan → subtype and goal → chains → items in tests (slice 5).
- Constraint names follow `NAMING_CONVENTION` via SQLAlchemy metadata.
- No polymorphic inheritance mappers introduced.

**Risks/edge cases:**
- Partial unique index syntax must be valid for SQLite (SQLAlchemy `Index(..., sqlite_where=...)`).
- Avoid `cascade="all, delete-orphan"` on plan tree relationships — deletion is service-orchestrated.

---

### Slice 4: Initial Alembic migration

**Objective:** Register models with Alembic and create the first table migration for plan/chain schema.

**Files expected to change:**
- [`calendar_backend/db/migrations/env.py`](../../calendar_backend/db/migrations/env.py)
- [`calendar_backend/db/migrations/versions/<revision>_create_plan_tables.py`](../../calendar_backend/db/migrations/versions/) (new)

**Implementation steps:**
1. Import plan models in `env.py` so tables register on `Base.metadata`:
   ```python
   from calendar_backend.models import plans  # noqa: F401
   ```
   (Only `plans` for this plan — not future Prompt 5 modules.)
2. Run autogenerate against temp/dev DB:
   ```bash
   uv run alembic revision --autogenerate -m "create plan and child chain tables"
   ```
3. **Hand-review** revision: confirm all six tables, FKs, partial unique index, CHECK, and `UNIQUE(child_plan_id)`; edit autogenerate output if needed.
4. Verify upgrade/downgrade on empty SQLite DB:
   ```bash
   uv run alembic upgrade head
   uv run alembic downgrade base   # if downgrade is practical; otherwise document one-way
   ```

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- `alembic upgrade head` creates all six tables on a fresh SQLite database.
- `env.py` import ensures non-empty metadata for future autogenerate.
- Migration file is committed and reviewable (no silent autogenerate blind trust).

**Risks/edge cases:**
- Autogenerate may miss or mis-name partial indexes — verify manually.
- Ensure migration runs with FK pragma path used in app (`create_engine_for_url` event) when testing upgrades.

---

### Slice 5: Model and schema tests

**Objective:** Pytest coverage proving schema shape, key constraints, and migration applicability.

**Files expected to change:**
- [`tests/models/__init__.py`](../../tests/models/__init__.py) (new, empty)
- [`tests/models/test_plans_schema.py`](../../tests/models/test_plans_schema.py) (new)

**Implementation steps:**
1. Create `tests/models/` package.
2. **Metadata tests** (temp SQLite file + `create_engine_for_url`):
   - All six table names exist on `Base.metadata`.
   - Key columns present (sample: `plan.is_master`, `task_plan.duration_minutes`, `goal_child_chain_item.child_plan_id`).
   - FK targets include self-FK on `plan.parent_id`, detail PK/FK, chain FKs.
3. **Constraint tests** (inline inserts via SQLAlchemy Core or session, `@pytest.mark.integration`):
   - Partial unique: two rows with `is_master=True` fails.
   - CHECK: `is_master=True` with `plan_kind != GOAL` fails.
   - UNIQUE: same `child_plan_id` in two chain items fails.
   - FK: invalid `parent_id` / `plan_id` fails when FKs enabled.
4. **Migration smoke test:** `alembic upgrade head` on temp DB URL; assert tables exist via `inspect(engine).get_table_names()`.
5. Use minimal fixture data factories **inside test file** (inline UUIDs, required fields only) — no shared test factory module unless duplication is painful.
6. Do **not** test service tree rules, master bootstrap, or subtype pairing completeness.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -m "not slow and not failure_expected"
```

**Acceptance criteria:**
- All new model/schema tests pass.
- Existing [`tests/db/test_session.py`](../../tests/db/test_session.py) and [`tests/domain/`](../../tests/domain/) continue to pass.
- Tests demonstrate Appendix §12-relevant column types (timezone-aware datetime columns exist) at schema level only.

**Risks/edge cases:**
- Use temp-file SQLite (not `:memory:`) if Alembic and session tests need persistent connections across calls.
- Insert tests need valid rows: create master goal plan + detail rows with consistent IDs before chain item tests.

## Abstraction check

| Introduced item | Needed now? | Justification |
|-----------------|-------------|---------------|
| Mapped classes (`Plan`, `GoalPlan`, etc.) | Yes | Design §6 ORM tables |
| SQLAlchemy `relationship()` | Yes | Slice 3 objective; enables navigation in tests and later services |
| Test inline fixtures | Maybe | Allowed in slice 5 test file only; no shared `ModelFactory` registry |
| Repository / DAO layer | No | Services talk to Session directly per design |
| Polymorphic inheritance mapper | No | Conflicts with explicit subtype table pattern |
| Base model mixin / TimestampMixin | No | Only two tables have created/updated; YAGNI |
| Separate `models/chains.py` module | No | Prompt 4 scope fits one `plans.py`; split only if file grows unwieldy in implementation |

No new domain types — reuse existing enums; map ORM rows to domain at service layer later.

## Dependency changes

None expected — `sqlalchemy` and `alembic` already in [`pyproject.toml`](../../pyproject.toml).

```bash
uv sync   # if fresh clone only
```

## Open questions

None blocking implementation.

## Changed in this revision

- Finalized plan to [`docs/plans/core_plan_orm_models.md`](core_plan_orm_models.md) (was draft in `~/.cursor/plans/`).
- Added **Finalized plan location** header and pointed build workflow at this file.
- Updated **Current repo state** to reflect completed database and domain primitive plans.
- Fixed slice 1 acceptance criteria: four tables for four mapped classes (not “six logical tables”).
- Clarified slice 2 acceptance: six plan/chain tables total after chain tables are added.
