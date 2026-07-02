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
- Use **double-quoted** string literals for revision identifiers, table/column names, and CHECK SQL in migration files (normalize autogenerate single quotes).
- Data backfills or deduplication before constraints: use `connection = op.get_bind()` and `connection.execute(sa.text(...))` in helper functions; keep upgrade/downgrade reversible when practical.

**Examples:**

- [`7e137c1ddfb0`](../../calendar_backend/db/migrations/versions/7e137c1ddfb0_remove_granularity_from_app_settings.py) — `batch_alter_table` for column drop/add.
- [`e6e01e97df46`](../../calendar_backend/db/migrations/versions/e6e01e97df46_add_repetition_plan_check_constraints.py) — `batch_alter_table` for CHECK constraints.
- [`522f4501f06a`](../../calendar_backend/db/migrations/versions/522f4501f06a_add_partial_unique_index_for_system_.py) — data cleanup via `op.get_bind()`, then `op.create_index` with `sqlite_where`.

**Aligns with:** Guide §8.10 (SQLite batch mode); `/db-revision-preview` manual edit step.

---

## 5. Domain vs services placement (session-free vs persistence)

**Scope:** `calendar_backend/domain/` vs `calendar_backend/services/` (same *session-free vs uses `Session`* split applies at `scheduling/`, `deletion/`, and `orchestration/` for their layers).

**Rule:**

- **`services/`** — code that uses `Session`, `transaction()`, or otherwise coordinates persistence (read or write): public service methods, bootstrap/load/save/delete, sibling-service orchestration inside a transaction, and **private helpers used only by that module’s public API** (e.g. `_load_or_create_settings`, `_validate_settings_update` on `update_settings` until extracted).
- **`domain/`** — **session-free** code: enums, IDs, errors, `ServiceResult`, time/constraint helpers, frozen **DTOs** and their **row→DTO mappers** in [`domain/dtos.py`](../../calendar_backend/domain/dtos.py). Domain must **not** import SQLAlchemy `Session` or call `transaction()`.
- **Shared calendar/scheduling semantics** (validate windows, merge OR intervals, write-path constraint helpers) belong in **`domain/`** modules (e.g. [`domain/constraints.py`](../../calendar_backend/domain/constraints.py), [`domain/time.py`](../../calendar_backend/domain/time.py)) even with one caller — not only when reused twice.
- **ORM invariant validation** over loaded graphs belongs in dedicated invariant module(s) per [§9](#9-orm-invariant-validation-ownership) — not in write-path helper modules.
- **ORM mapped classes** may appear in domain **only** for dumb record projection (DTO mappers) or pure checks over already-loaded row/graph data passed in as arguments — never for queries or mutations.
- **Read-only diagnostics** that must load an ORM graph (`PlanTreeInvariantService`) stay in **`services/`**; extract pure violation logic to `domain/` invariant module(s) when testable without a database.

**Examples:**

- **Domain (write-path / shared semantics):** `GoalPlanDTO`, `goal_plan_dto_from_plan`, `validate_time_window`, `merge_or_windows`, `validate_user_group_windows`.
- **Domain (ORM invariants):** `validate_master_tree_graph` in [`domain/invariant_validation.py`](../../calendar_backend/domain/invariant_validation.py) — see [§7](#7-plan-tree-invariant-ideal-shape), [§8](#8-no-db-schema-replay-in-invariants), [§9](#9-orm-invariant-validation-ownership).
- **Services:** `AppSettingsService.get_settings`, `MasterHorizonService.refresh_master_horizon`, `PlanTreeInvariantService.validate_master_tree` (loads full graph in `transaction`, calls domain invariant checks).

**Supersedes:** Vague “services own all validation” readings — services **enforce** rules at persistence boundaries by calling domain semantics; PDF/guide “pure domain layer free of SQLAlchemy **sessions**” (not “ORM-blind DTOs”).

---

## 6. Opinionated collection types (prefer `tuple` over `Sequence`)

**Scope:** Domain and service **public** APIs, DTO fields, and `ServiceResult` payloads in `calendar_backend/`. Does not apply to Alembic revision metadata ([§4](#4-alembic-revision-file-style-sqlite)), generic dev scripts, or stdlib-style utilities with many unknown callers.

**Rule:**

- **Prefer concrete value types** (`tuple[T, ...]`, frozen dataclass fields) when the collection is part of a **domain concept** (e.g. OR windows in one constraint group, `ServiceMessage` bundles). Signatures document intent, not only “iterable.”
- **Do not use `collections.abc.Sequence`** (or `Iterable`) on domain helpers or service public methods merely because the body only loops — that widens types without adding clarity when callers are known.
- **Validate semantics at trust boundaries** (service public API, future HTTP/CLI); **normalize shape once** at that boundary (e.g. `windows = tuple(windows)` if accepting a list literal in tests), then pass **`tuple`** to domain.
- **Bridge ORM `list` relationship collections in `services/`** — convert to `tuple` (or build DTOs) before calling domain; domain does not take `Mapped[list[...]]`.
- **Use `Sequence` only when** the container shape is intentionally not part of the contract:
  - Alembic `down_revision: str | Sequence[str] | None` (single parent vs merge heads)
  - Generic command/script helpers (`run(cmd: Sequence[str])`) with unrelated caller container types
  - A **shared utility** with two or more real callers that pass different read-only container types (abstraction discipline — not hypothetical reuse)

**Examples:**

- **Prefer `tuple`:** `validate_user_group_windows(windows: tuple[TimeWindow, ...])`, `merge_or_windows(...) -> tuple[TimeWindow, ...]`, `TimeConstraintGroupDTO.windows: tuple[_TimeWindowDTO, ...]`, `ServiceResult.errors: tuple[ServiceMessage, ...]`.
- **Keep `Sequence`:** migration `down_revision` fields ([§4](#4-alembic-revision-file-style-sqlite)); `scripts/cursor/commit_changes.py` `run(cmd: Sequence[str])`.

**Aligns with:** [§5](#5-domain-vs-services-placement-session-free-vs-persistence); abstraction discipline (no widening for hypothetical callers).

---

## 7. Plan tree invariant ideal shape

**Scope:** [`PlanTreeInvariantService`](../../calendar_backend/services/plan_tree_invariant.py) and pure ORM invariant checks it calls in [`domain/invariant_validation.py`](../../calendar_backend/domain/invariant_validation.py) (or future [`domain/invariants/`](../../calendar_backend/domain/) modules per [§9](#9-orm-invariant-validation-ownership)).

**Rule:** Plan-tree invariant validation checks the **ideal persisted shape** after operations that are supposed to leave the tree correct — not rules that must hold at every instant or in transient mid-transaction states. This includes **existence and cardinality** constraints (for example master present, master has `SYSTEM_MASTER_HORIZON`, USER groups non-empty) that may legitimately fail before bootstrap or between coordinated service steps.

**Examples:**

- Run after bootstrap + horizon refresh, or after orchestrated mutations expected to yield a valid tree — not as a gate on empty DB before `ensure_master_exists`.
- Flag orphan plans, missing master horizon, empty USER groups, misaligned chain parentage — semantic shape beyond single-row CHECKs.

**Aligns with:** [§8](#8-no-db-schema-replay-in-invariants), [§9](#9-orm-invariant-validation-ownership).

---

## 8. No DB-schema replay in invariants

**Scope:** ORM invariant validation in [`domain/invariant_validation.py`](../../calendar_backend/domain/invariant_validation.py) and sibling invariant modules under [`domain/invariants/`](../../calendar_backend/domain/) if split later.

**Rule:** Do **not** re-check invariants already enforced by SQLite schema on committed rows (CHECK constraints, UNIQUE constraints, partial unique indexes). Invariant validation focuses on **semantic and cross-row** rules the schema does not express. Callers pass the **full committed plan graph** loaded from persistence; invariant modules do not re-validate FK target existence or partial-graph membership.

**Examples:**

- **Do not report:** master must be `GOAL` (`ck_plan_master_is_goal`), duplicate `child_plan_id` in chain items (`UNIQUE(child_plan_id)`), `start_time >= end_time` on windows (`ck_time_window_start_before_end`).
- **Do report:** reachability from master, subtype pairing, dense chain/repetition ordering, chain child parent alignment, clone lineage, master horizon placement/cardinality, minute-aligned merged USER windows.

**Supersedes:** Plan or test guidance that treats invariant diagnostics as a full replay of schema tests on loaded graphs.

---

## 9. ORM invariant validation ownership

**Scope:** Session-free validation of **loaded ORM graph snapshots** in `calendar_backend/domain/`.

**Rule:**

- **All ORM invariant validation** (checks over already-loaded mapped rows passed as arguments) lives in [`domain/invariant_validation.py`](../../calendar_backend/domain/invariant_validation.py) today. If the module grows large, split into a **`domain/invariants/`** subpackage (not a separate top-level package); shared helpers may remain in any other `domain/` module.
- **Other `domain/` modules** (for example [`constraints.py`](../../calendar_backend/domain/constraints.py), [`time.py`](../../calendar_backend/domain/time.py)) may hold shared validation/normalization helpers used at **write boundaries** or by invariant checks — they must **not** define separate ORM invariant entry points.
- Invariant modules may **call** shared helpers (for example `merge_or_windows`, `is_minute_aligned`); invariant orchestration stays in the invariant module(s).

**Examples:**

- **Invariant module:** `validate_master_tree_graph(plans: tuple[Plan, ...])`.
- **Write-path helpers (not ORM invariant owners):** `validate_time_window`, `validate_user_group_windows`, `merge_or_windows`.
- **Service:** `PlanTreeInvariantService` loads graph, calls `validate_master_tree_graph` — does not embed tree rules inline.

**Aligns with:** [§5](#5-domain-vs-services-placement-session-free-vs-persistence), [§7](#7-plan-tree-invariant-ideal-shape), [§8](#8-no-db-schema-replay-in-invariants).

---

## 10. Type-checker-only artifacts (minimal)

**Scope:** Python in `calendar_backend/` where static analysis (Pyright) needs hints beyond what inference provides.

**Rule:**

- Prefer the **most minimal** form that satisfies the checker: inline `# pyright: ...` or `# type: ...` comments when they alone suffice.
- When **code** with little or no runtime purpose exists primarily for type checkers — `@overload` stubs, redundant `assert isinstance(...)` after validation, `cast(...)`, `# pyright: ignore[...]` on an implementation — add a brief comment that it is for type checkers.
- Do not add overloads, asserts, casts, or ignores when a plain union return type or a real runtime `isinstance` branch is simpler and sufficient.

**Examples:**

- **Comment on overload block:** `# Type checker: correlate kind, payload, and return DTO.` above `@overload` stubs; one implementation body below.
- **Comment on narrowing assert:** `assert isinstance(payload, TaskCreatePayload)  # type checker: validate_create_payload already enforced match`
- **Comment on ignore:** `# pyright: ignore[reportInconsistentOverload]  # type checker: implementation wider than overload stubs`
- **Prefer comment over code:** use `# pyright: ignore[reportArgumentType]` on one line instead of a wrapper function when that is the only issue.

**Does not apply to:** Runtime validation at trust boundaries, real control-flow `isinstance` checks, or Alembic typing ([§4](#4-alembic-revision-file-style-sqlite)).

---

## 11. Boundary validators imply persisted-shape enforcement

**Scope:** New or changed **write-path / API boundary** validators in `calendar_backend/domain/` and `calendar_backend/services/` (including helpers called only from mutating service entry points).

**Rule:**

- When adding or changing a boundary validator, ask whether it implies **ideal committed ORM shape** (row fields or loaded graph semantics), not only request convenience.
- If yes:
  1. Add an **ORM `CheckConstraint`** on the mapped table when the rule is single-table and SQLite-friendly; follow with an Alembic migration before relying on DB enforcement in production. Add schema tests marked `failure_expected` per [§13](#13-schema-tests-pending-migration-failure_expected) when the migration is deferred.
  2. Otherwise add a check in [`domain/invariant_validation.py`](../../calendar_backend/domain/invariant_validation.py) (or future [`domain/invariants/`](../../calendar_backend/domain/)) per [§9](#9-orm-invariant-validation-ownership).
- Per [§12](#12-no-utc-timezone-checks-on-loaded-orm-rows-v1), do **not** add UTC timezone checks on loaded ORM datetime fields in invariant modules (minute alignment still applies where scheduling requires it).
- Per [§8](#8-no-db-schema-replay-in-invariants), do **not** duplicate CHECK-covered rules in invariant modules once the CHECK is on the schema.
- If the rule is boundary-only (payload pairing, unsupported API policy, parent existence at create time with no persisted counterpart), stop at the boundary validator.
- Shared predicate logic: prefer [`domain/time.py`](../../calendar_backend/domain/time.py) and sibling helpers; export from boundary modules only when non-trivial duplication would result — not for one-liners.

**Examples:**

- **CHECK:** `TaskPlan.duration_minutes > 0` mirrors `validate_task_create`.
- **Invariant:** repetition template root parented under repetition shell — graph rule, not a single-row CHECK.
- **Boundary only:** `validate_create_payload` kind/payload type pairing — no committed column encodes the mismatch.

**Aligns with:** [§8](#8-no-db-schema-replay-in-invariants), [§9](#9-orm-invariant-validation-ownership), [§12](#12-no-utc-timezone-checks-on-loaded-orm-rows-v1).

---

## 12. No UTC timezone checks on loaded ORM rows (V1)

**Scope:** ORM invariant validation in [`domain/invariant_validation.py`](../../calendar_backend/domain/invariant_validation.py) (or future [`domain/invariants/`](../../calendar_backend/domain/)) and other **read-oriented** checks over loaded mapped rows.

**Rule:**

- Do **not** validate timezone-aware UTC on datetime fields **read from persistence** in invariant modules.
- UTC enforcement belongs at **write boundaries** (domain validators and mutating services before persist) and in shared helpers such as [`require_utc`](../../calendar_backend/domain/time.py) / `validate_time_window`.
- **Do** validate **minute alignment** on loaded ORM timestamps when scheduling semantics require it (same as USER constraint windows in [§8](#8-no-db-schema-replay-in-invariants) examples).
- Revisit UTC-on-read when migrating off SQLite if persistence preserves tzinfo reliably.

**Examples:**

- **Invariant:** `is_minute_aligned` on `RepetitionPlan.start_time` / `end_time` — yes.
- **Invariant:** `require_utc` on loaded `repetition_plan.start_time` — no (V1).
- **Boundary:** `validate_repetition_create` rejects naive `start_time` — yes.

**Aligns with:** [§8](#8-no-db-schema-replay-in-invariants), [§11](#11-boundary-validators-imply-persisted-shape-enforcement).

---

## 13. Schema tests pending migration (`failure_expected`)

**Scope:** ORM or schema changes in `calendar_backend/models/` (and related schema tests) that add or change **database-level** enforcement — `CheckConstraint`, `UNIQUE`, partial indexes, `NOT NULL`, new FK rules — when the Alembic revision is **not** applied in the same change.

**Rule:**

- In the **same change** as the ORM/schema edit, add integration or schema tests that document the intended DB behavior (for example `IntegrityError` on invalid `INSERT`).
- Mark those tests `@pytest.mark.failure_expected` until `alembic upgrade head` applies the matching revision — ORM `CheckConstraint`s on mapped classes do not enforce on SQLite until the migration lands.
- Do **not** use `failure_expected` for rules already enforced without a migration (domain invariants, service/boundary validation, pure unit tests).
- When running `/db-revision-continue`, remove `failure_expected` from tests satisfied by the new revision and confirm they pass (see [db-revision-continue](../commands/db-revision-continue.md)).

**Examples:**

- **Mark:** `test_task_plan_rejects_zero_duration` expecting `IntegrityError` after adding `duration_minutes > 0` CHECK on `TaskPlan` ORM — migration deferred.
- **Do not mark:** `test_validate_task_create_rejects_zero_duration` — boundary validation, no migration required for the test to pass.
- **After migration:** drop marker on schema INSERT-failure tests; they run in the default `pytest -m "not slow and not failure_expected"` suite.

**Aligns with:** [§4](#4-alembic-revision-file-style-sqlite), [§11](#11-boundary-validators-imply-persisted-shape-enforcement).

---

## 14. Plan service ownership boundaries

**Scope:** [`GoalService`](../../calendar_backend/services/goal.py), [`PlanTreeService`](../../calendar_backend/services/plan_tree.py), and sibling plan services (`TaskService`, `RepetitionService`).

**Rule:**

- **`PlanTreeService`** — plan-wide **identity** (`rename_plan`) and **existence** (`preview_delete`, `delete_plan`); repo-internal `make_*` / `attach_under_parent` for sibling services.
- **`GoalService`** — goal-parent **child-chain layout**: `create_child` (initial chain placement) and `move_plan` (within/cross-chain reorder under the same parent goal; no reparenting in V1).
- **`TaskService` / `RepetitionService`** — subtype self-edits on the plan node (scheduling, generation, etc.).
- Goal child-chain persistence helpers (ordering, dense renumbering, bucket-end chain creation) are **module-private to `GoalService`**, not shared via `PlanTreeService`.

**Examples:**

- **GoalService:** `create_child` + `_attach_to_goal_chain`; `move_plan(plan_id, position)` and `move_plan(plan_id, chain_index, position)`.
- **PlanTreeService:** `rename_plan`; future `preview_delete` / `delete_plan`; `make_goal` / `attach_under_parent` called from `GoalService` during create.
- **Not PlanTreeService:** chain reorder, cross-chain move, or empty-chain cleanup after move.

**Supersedes:** Guide §0.1 row “Plan creation vs tree mutations” and [`docs/plans/plan_tree_service.md`](../docs/plans/plan_tree_service.md) assumptions that listed `move_plan` on `PlanTreeService`; PDF §7 monolithic plan-tree service readings for move ownership.
