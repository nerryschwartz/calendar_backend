# Plan: Core ORM models part 2

**Finalized plan location:** `docs/plans/core_orm_models_part2.md`

## Context

Implement Prompt 5 from [docs/cursor_implementation_guide.md](../cursor_implementation_guide.md): remaining core ORM persistence for `calendar_backend` per [docs/calendar_backend_v1_engineering_design_updated.pdf](../calendar_backend_v1_engineering_design_updated.pdf) §6 (constraints, repetition instances, calendar, free time, settings, runs).

Design constraints:
- [`calendar_backend/models/`](../../calendar_backend/models/) owns SQLAlchemy table mappings only — **no public mutation behavior** (design §4, §11).
- **Module layout:** separate model modules matching guide §8.5 (`chains`, `constraints`, `repetitions`, `calendar`, `free_time`, `runs`, `settings`, plus `plans` for the plan tree and subtype detail tables).
- [`calendar_backend/models/plans.py`](../../calendar_backend/models/plans.py) holds `Plan` and subtype detail tables; [`calendar_backend/models/chains.py`](../../calendar_backend/models/chains.py) holds `GoalChildChain` and `GoalChildChainItem` (same pattern as `repetitions.py` for child collections).
- Reuse domain enums from [`calendar_backend/domain/enums.py`](../../calendar_backend/domain/enums.py) and UUID storage conventions from Prompt 4.
- [`calendar_backend/domain/ids.py`](../../calendar_backend/domain/ids.py) NewTypes are for **service boundaries** only; ORM columns use `Uuid(as_uuid=True)` / `uuid.UUID`.
- **Domain `TimeWindow` dataclass** ([`calendar_backend/domain/time.py`](../../calendar_backend/domain/time.py)) is separate from ORM `TimeWindow` rows — no naming collision in Python if ORM class lives in `models/constraints.py`.

**Locked decision (clarification):** Same DB enforcement policy as [core_plan_orm_models.md](core_plan_orm_models.md): FKs, local single-table CHECKs, and obvious cardinality rules at the DB layer; semantic invariants (fraction sum = 1, TASK vs FREE_TIME source-field pairing, dense ordering, minute alignment) deferred to services and future invariant tests.

Current repo state:
- Prompt 4 plan complete: [`plans.py`](../../calendar_backend/models/plans.py), migration [`be7d178b7c5a`](../../calendar_backend/db/migrations/versions/be7d178b7c5a_create_plan_and_child_chain_tables.py), [`tests/models/test_plans_schema.py`](../../tests/models/test_plans_schema.py).
- [`calendar_backend/db/migrations/env.py`](../../calendar_backend/db/migrations/env.py) imports all model modules (see guide §8.5).
- Domain primitives (IDs, enums including `ConstraintKind`, `CalendarEntryType`, `CalendarRunStatus`, `SolverStatus`, `LastFailureReason`, `FreeTimeWeekStartDay`) are implemented.

Build workflow: use `/build-plan-slice` per slice against this file; stop after each slice for approval.

## Non-goals

- Service-layer behavior (Prompt 6+): `MasterPlanService`, `AppSettingsService`, constraint editing, assignment, free-time assignment, orchestration.
- Domain constraint merge/intersection algorithms (Prompt 7).
- SQLAlchemy polymorphic inheritance, triggers, or cross-table CHECKs requiring subtype pairing.
- Persisting renormalized free-time fractions or conflict payloads on `CalendarRun`.
- Production HTTP/API or CLI beyond schema tests.
- OR-Tools / scheduling code.
- Refactoring [`calendar_backend/settings/`](../../calendar_backend/settings/) service package (empty today); ORM `AppSettings` lives under `models/settings.py`.

## Locked assumptions

- **Ten new tables:** `time_constraint_group`, `time_window`, `repetition_instance`, `calendar_entry`, `free_time_activity`, `free_time_activity_prerequisite`, `calendar_run`, `active_calendar_state`, `app_settings`.
- **`constraint_kind` on `TimeConstraintGroup` only** (design §6); not denormalized onto `time_window`.
- **Table/column names** match design §6 literally (including `prerequisite_id` PK on `free_time_activity_prerequisite`).
- **FK targets:** `repetition_instance.repetition_plan_id` → `repetition_plan.plan_id` (detail table, same pattern as `goal_child_chain.parent_goal_id` → `goal_plan.plan_id`).
- **UUID columns:** `Uuid(as_uuid=True)`; **timestamps:** `DateTime(timezone=True)` with **no DB defaults** — services set via `Clock`.
- **`real_fraction`:** SQLAlchemy `Numeric` (fixed precision, e.g. `Numeric(18, 9)`), not float.
- **Singleton rows:** `app_settings` and `active_calendar_state` use `singleton_id int` PK; optional `CHECK (singleton_id = 1)` when straightforward.
- **FK delete behavior:** conservative (no cascades that could delete master or orphan calendar data unexpectedly); deletion semantics are service-owned.
- **Relationships:** add `relationship()` in the same slice as each table group (read-oriented; no `cascade="all, delete-orphan"` on plan/calendar trees).
- **Migration:** second revision via [`/db-revision-preview`](../../.cursor/commands/db-revision-preview.md) then manual edit and [`/db-revision-continue`](../../.cursor/commands/db-revision-continue.md); extend `env.py` imports incrementally from slice 1 (preview verifies wiring before autogenerate).
- **Tests:** slices 1–5 run ruff + pyright; slice 6 adds pytest. Slice 6 must cover **everything introduced in this chunk** (per [`.cursor/rules/20-testing-and-checks.mdc`](../../.cursor/rules/20-testing-and-checks.mdc)), not only examples below; post **Test catalog** in chat.

## Slices

### Slice 1: Constraints tables

**Objective:** Add `TimeConstraintGroup` and `TimeWindow` ORM mappings with relationships and practical DB constraints.

**Files expected to change:**
- [`calendar_backend/models/constraints.py`](../../calendar_backend/models/constraints.py) (new)
- [`calendar_backend/db/migrations/env.py`](../../calendar_backend/db/migrations/env.py) (add `constraints` import)

**Implementation steps:**
1. Create `models/constraints.py` with mapped classes:
   - **`TimeConstraintGroup`:** `time_constraint_group_id` (UUID PK), `plan_id` (FK → `plan.plan_id`), `constraint_kind` (`ConstraintKind` enum).
   - **`TimeWindow`:** `time_window_id` (UUID PK), `group_id` (FK → `time_constraint_group.time_constraint_group_id`), `start_time`, `end_time` (`DateTime(timezone=True)`).
2. Add relationships:
   - `Plan` ↔ constraint groups (extend [`plans.py`](../../calendar_backend/models/plans.py) with `constraint_groups` on `Plan` and `plan` back-reference on group — minimal cross-module relationship wiring).
   - `TimeConstraintGroup` ↔ `TimeWindow` (`windows` list / `group` back-reference).
3. Add `__table_args__` CHECKs where straightforward:
   - `start_time < end_time` on `time_window`.
4. Wire `from calendar_backend.models import constraints  # noqa: F401` in `env.py`.
5. Persistence-only — no constraint merge/normalization helpers.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- Both tables register on `Base.metadata`.
- FK targets match design §6.
- Relationships allow navigating `plan` → groups → windows in a future ORM test.
- pyright strict passes.

**Risks/edge cases:**
- Cross-module relationship between `plans.Plan` and `constraints.TimeConstraintGroup` may require `TYPE_CHECKING` imports or string relationship targets — follow SQLAlchemy 2 patterns already used in `plans.py`.
- Do **not** add CHECKs tying `constraint_kind` to plan subtype; system vs user ownership is enforced in services (Prompt 7).

---

### Slice 2: Repetition instance table

**Objective:** Add `RepetitionInstance` ORM mapping for concrete repetition occurrences.

**Files expected to change:**
- [`calendar_backend/models/repetitions.py`](../../calendar_backend/models/repetitions.py) (new)
- [`calendar_backend/models/plans.py`](../../calendar_backend/models/plans.py) (optional: `RepetitionPlan.instances` relationship)
- [`calendar_backend/db/migrations/env.py`](../../calendar_backend/db/migrations/env.py) (add `repetitions` import)

**Implementation steps:**
1. Create `models/repetitions.py`:
   - **`RepetitionInstance`:** `repetition_instance_id` (UUID PK), `repetition_plan_id` (FK → `repetition_plan.plan_id`), `instance_index` (int — occurrence slot for cloning/constraint shifting, not priority), `root_clone_id` (FK → `plan.plan_id`), `instance_start_time` (`DateTime(timezone=True)`), `is_critical` (bool), `sort_order` (int — priority within the critical or non-critical bucket, analogous to `GoalChildChain.sort_order`).
2. Relationships:
   - `RepetitionInstance` → `RepetitionPlan`, `RepetitionInstance` → `Plan` (`root_clone`).
   - Optional back-reference `RepetitionPlan.instances`.
3. Practical CHECKs: `instance_index >= 0`, `sort_order >= 0` (dense/unique ordering within each `(repetition_plan_id, is_critical)` partition deferred to services, same as goal child chains).
4. Add `repetitions` import to `env.py`.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- Table on `Base.metadata` with columns per [implementation guide §0.1](../cursor_implementation_guide.md#01-guide-vs-engineering-design-pdf) (not PDF §6 `is_effectively_critical`).
- FK to `repetition_plan.plan_id` (not bare `plan`).
- pyright passes.

**Risks/edge cases:**
- `root_clone_id` points at generated clone subtree root — FK to `plan` only at schema level; clone/template semantics are service-owned.
- `sort_order` affects resolution priority and logical completion ordering (critical instances first, then non-critical); instances still do not impose scheduling precedence on each other (unlike chain item `position`).

---

### Slice 3: Calendar entry table

**Objective:** Add `CalendarEntry` for the unified TASK/FREE_TIME active calendar.

**Files expected to change:**
- [`calendar_backend/models/calendar.py`](../../calendar_backend/models/calendar.py) (new)
- [`calendar_backend/db/migrations/env.py`](../../calendar_backend/db/migrations/env.py) (add `calendar` import)

**Implementation steps:**
1. Create `models/calendar.py`:
   - **`CalendarEntry`:** `calendar_entry_id` (UUID PK), `entry_type` (`CalendarEntryType`), `start_time`, `end_time` (`DateTime(timezone=True)`), `source_plan_id` (nullable FK → `plan.plan_id`), `source_free_time_activity_id` (nullable FK → `free_time_activity.free_time_activity_id` — forward reference OK in SQLAlchemy; table created in slice 4 before migration), `calendar_run_id` (nullable FK → `calendar_run.calendar_run_id` — forward reference to slice 5), `display_label` (str), `created_at`, `updated_at`.
2. Relationships (read-only): optional `source_plan`, deferred FK targets for activity/run until those classes exist.
3. CHECK: `start_time < end_time`.
4. **Do not** add DB CHECK enforcing which nullable source FK is set per `entry_type` — deferred to services.
5. Add `calendar` import to `env.py`.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- Table registered with all design §6 columns.
- Nullable FK columns match design.
- pyright passes.

**Risks/edge cases:**
- Forward FK references to tables from later slices are fine at ORM level before migration; migration runs only after slice 5 tables exist.
- `display_label` is a denormalized snapshot — no automatic update on plan rename (design §13).

---

### Slice 4: Free-time tables

**Objective:** Add `FreeTimeActivity` and `FreeTimeActivityPrerequisite`.

**Files expected to change:**
- [`calendar_backend/models/free_time.py`](../../calendar_backend/models/free_time.py) (new)
- [`calendar_backend/db/migrations/env.py`](../../calendar_backend/db/migrations/env.py) (add `free_time` import)

**Implementation steps:**
1. Create `models/free_time.py`:
   - **`FreeTimeActivity`:** `free_time_activity_id` (UUID PK), `name` (str), `enabled` (bool), `real_fraction` (`Numeric`), `minimum_block_size_minutes` (int), `created_at`, `updated_at`.
   - **`FreeTimeActivityPrerequisite`:** `prerequisite_id` (UUID PK — column name per design §6), `free_time_activity_id` (FK → `free_time_activity.free_time_activity_id`), `source_plan_id` (FK → `plan.plan_id`).
2. Relationships: activity ↔ prerequisites; prerequisite → plan.
3. Optional CHECK: `minimum_block_size_minutes >= 0`.
4. **Do not** CHECK that enabled fractions sum to 1 — service invariant (Prompt 14).
5. Add `free_time` import to `env.py`.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- Both tables on `Base.metadata`.
- `prerequisite_id` used as PK column name (maps to domain `FreeTimeActivityPrerequisiteID` at service boundary).
- pyright passes.

**Risks/edge cases:**
- Prerequisite completion semantics (task vs goal/repetition) are service-evaluated, not stored on the row.

---

### Slice 5: Settings and run metadata tables

**Objective:** Add `CalendarRun`, `ActiveCalendarState`, and `AppSettings`.

**Files expected to change:**
- [`calendar_backend/models/runs.py`](../../calendar_backend/models/runs.py) (new)
- [`calendar_backend/models/settings.py`](../../calendar_backend/models/settings.py) (new)
- [`calendar_backend/models/calendar.py`](../../calendar_backend/models/calendar.py) (complete `CalendarEntry` ↔ `CalendarRun` relationship if deferred)
- [`calendar_backend/db/migrations/env.py`](../../calendar_backend/db/migrations/env.py) (add `runs`, `settings` imports)

**Implementation steps:**
1. Create `models/runs.py`:
   - **`CalendarRun`:** `calendar_run_id` (UUID PK), `run_started_at`, `run_finished_at` (nullable), `status` (`CalendarRunStatus`), `solver_status` (`SolverStatus`, nullable), `conflict_count`, `warning_count`, `runtime_ms` (int), `created_at`.
   - **`ActiveCalendarState`:** `singleton_id` (int PK), `active_calendar_run_id` (nullable FK → `calendar_run.calendar_run_id`), `last_refresh_failed` (bool), `last_failure_at` (nullable), `last_failure_reason` (`LastFailureReason`, nullable), `updated_at`.
2. Create `models/settings.py`:
   - **`AppSettings`:** `singleton_id` (int PK), `local_timezone` (str), `master_horizon_duration_minutes`, `scheduling_granularity_minutes`, `exact_solver_time_limit_seconds`, `exact_solver_model_size_limit` (int), `heuristic_enabled` (bool), `free_time_week_start_day` (`FreeTimeWeekStartDay`), `updated_at`.
3. Relationships: `ActiveCalendarState` → `CalendarRun`; `CalendarEntry` → `CalendarRun` (back_populates optional).
4. Optional CHECK: `singleton_id = 1` on singleton tables.
5. Finalize `env.py` imports to match guide §8.5:
   ```python
   from calendar_backend.models import calendar, chains, constraints, free_time, plans, repetitions, runs, settings  # noqa: F401
   ```

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- All three tables on `Base.metadata` with design §6 columns.
- Enum columns use domain `StrEnum` values.
- `env.py` imports all model modules.
- pyright passes.

**Risks/edge cases:**
- Do not confuse ORM `models/settings.py` with service package `calendar_backend/settings/`.
- `ActiveCalendarState` tracks refresh failure, not plan-tree staleness (design §6, §13).

---

### Slice 6: Alembic migration and schema tests

**Objective:** Create second migration for all ten new tables (via db-revision commands); pytest proving schema shape, constraints, FKs, relationships, and migration applicability.

**Files expected to change:**
- [`calendar_backend/db/migrations/env.py`](../../calendar_backend/db/migrations/env.py) (if preview finds missing imports)
- [`calendar_backend/db/migrations/versions/<revision>_create_remaining_core_tables.py`](../../calendar_backend/db/migrations/versions/) (new, via preview; user may edit before continue)
- [`tests/models/test_core_orm_part2_schema.py`](../../tests/models/test_core_orm_part2_schema.py) (new)

**Implementation steps:**
1. Run [`/db-revision-preview`](../../.cursor/commands/db-revision-preview.md) with message `create remaining core orm tables`. `env.py` should already import all model modules from slice 5; preview adds any missing imports and autogenerates the revision.
2. **Stop for approval.** User edits the migration manually per the preview report (tables, FKs, CHECKs, enum columns, partial indexes).
3. Create `tests/models/test_core_orm_part2_schema.py` using patterns from [`test_plans_schema.py`](../../tests/models/test_plans_schema.py):
   - **Metadata:** all ten table names; key columns; all FK targets; timezone-aware datetime columns; enum columns present.
   - **Constraint integration** (`@pytest.mark.integration`): every CHECK and practical UNIQUE/index added in slices 1–5; FK rejection for each FK column (invalid parent row).
   - **Relationship navigation:** at least one integration test per module group (e.g. plan → constraint groups → windows; repetition plan → instances; calendar entry sources; free-time activity → prerequisites; active calendar state → calendar run).
   - **Migration smoke:** programmatic `alembic upgrade head` on temp DB (same pattern as [`test_plans_schema.py`](../../tests/models/test_plans_schema.py)); assert new tables exist.
4. Run [`/db-revision-continue`](../../.cursor/commands/db-revision-continue.md) after migration approval to apply `upgrade head` to local DB and run pytest (see command for commit workflow).
5. Inline row helpers inside test file only — no shared factory module.
6. **Do not** test service invariants (fraction sum, entry_type/source pairing, prerequisite completion logic).
7. Post **Test catalog** in chat (see [§9 Test-creation slice convention](../cursor_implementation_guide.md#test-creation-slice-convention)).

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -m "not slow and not failure_expected"
```

**Acceptance criteria:**
- All new tests pass; existing [`test_plans_schema.py`](../../tests/models/test_plans_schema.py) still passes.
- Migration upgrades cleanly from `be7d178b7c5a` on fresh SQLite.
- Tests cover **all** schema elements and DB constraints introduced in slices 1–5 (implementation-chunk coverage rule).
- Chat report includes **Test catalog**.

**Risks/edge cases:**
- Use temp-file SQLite (not `:memory:`) for Alembic smoke tests in pytest.
- Autogenerated migration may need manual ordering when forward FKs cross modules (calendar ↔ free_time ↔ runs) — fix in manual edit step after preview.
- Repo-wide ruff may flag autogenerated migration style — fix during manual migration edit (do not silently skip checks).
- `/db-revision-continue` runs pytest before commit; schema tests (step 3) should exist or be stubbed before continue if a green run is required.

## Abstraction check

| Introduced item | Needed now? | Justification |
|-----------------|-------------|---------------|
| Mapped classes (10 tables across 6 modules) | Yes | Design §6 ORM tables |
| SQLAlchemy `relationship()` | Yes | Slice acceptance; enables navigation in tests and later services |
| Cross-module `Plan.constraint_groups` | Yes | Design links constraints to plans |
| Test inline row helpers | Yes | Same pattern as Prompt 4 slice 5; no shared factory |
| Repository / DAO / mixin layers | No | Services use Session directly per design |
| TimestampMixin / BaseModel | No | YAGNI — only some tables have timestamps |
| Separate `models/constraints/` package | No | Single module file per guide import list |

No new domain types — reuse existing enums; map ORM rows to domain at service layer later.

## Dependency changes

None expected — `sqlalchemy` and `alembic` already in [`pyproject.toml`](../../pyproject.toml).

```bash
uv sync   # if fresh clone only
```

## Open questions

None blocking implementation.
