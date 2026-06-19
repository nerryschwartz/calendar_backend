# Plan: Time constraint service and invariant validation

**Finalized plan location:** `docs/plans/time_constraint_invariant_validation.md`

## Context

Implement Prompt 7 from [docs/cursor_implementation_guide.md](../cursor_implementation_guide.md): user time-constraint editing and master-tree invariant diagnostics per [docs/calendar_backend_v1_engineering_design_updated.pdf](../calendar_backend_v1_engineering_design_updated.pdf) §7 (service layer), §8.1–§8.2 (ServiceResult / DTOs), Appendix §12 (time rules), and guide §0.1 (no `group_order` on constraint groups; AND groups unordered).

Design constraints:
- [`calendar_backend/services/`](../../calendar_backend/services/) owns public service methods, validation, transactions, and persistence-changing behavior; domain stays session-free ([repo convention §5](../../.cursor/repo_conventions.md)).
- Public methods return **`ServiceResult[T]`** via [`calendar_backend/domain/results.py`](../../calendar_backend/domain/results.py); mutations run inside [`transaction(session)`](../../calendar_backend/db/session.py) ([repo convention §2](../../.cursor/repo_conventions.md)).
- ORM in [`calendar_backend/models/constraints.py`](../../calendar_backend/models/constraints.py): `TimeConstraintGroup` + `TimeWindow`; `constraint_kind` on group only; partial unique index on master `SYSTEM_MASTER_HORIZON` per plan ([`522f4501f06a`](../../calendar_backend/db/migrations/versions/522f4501f06a_add_partial_unique_index_for_system_.py)).
- **Constraint semantics:** AND-of-OR groups per plan; empty outer USER set = no local restriction; **empty inner group invalid**; windows half-open `[start, end)` UTC minute-aligned (reject, do not truncate — reuse [`validate_time_window`](../../calendar_backend/domain/time.py)).
- **System-owned groups:** `SYSTEM_MASTER_HORIZON` (writer: [`MasterHorizonService`](../../calendar_backend/services/master_horizon.py)); `SYSTEM_REPETITION_WINDOW` (future `RepetitionService`). **`TimeConstraintService` is USER-only**; direct mutation of system groups via this service is forbidden ([`MessageCode.SYSTEM_CONSTRAINT_DIRECT_EDIT_FORBIDDEN`](../../calendar_backend/domain/errors.py)).
- **Persistence access:** filtered writes via explicit SQL; invariant tree walks via ORM relationships + eager load ([repo convention §3](../../.cursor/repo_conventions.md)).
- **Plan-tree invariants:** validate **ideal persisted shape** after operations expected to leave the tree correct ([repo convention §7](../../.cursor/repo_conventions.md)); do not replay DB CHECK/UNIQUE ([§8](../../.cursor/repo_conventions.md)); ORM invariant checks only in [`domain/invariant_validation.py`](../../calendar_backend/domain/invariant_validation.py) ([§9](../../.cursor/repo_conventions.md)).
- **Prompt 6 complete:** master/settings/horizon services and service test fixtures exist ([`master_plan_app_settings_master_horizon_services.md`](master_plan_app_settings_master_horizon_services.md)).

**Locked clarifications (request-questions):**
- **`PlanTreeInvariantService.validate_master_tree()`** covers the **full master-tree invariant suite** in this prompt (master existence/root, subtype pairing, reachability, chain ordering/alignment, repetition instance shape, system-constraint cardinality, USER constraint persisted shape). Prompt 8 adds deletion/cascade-specific checks only. Checks **ideal post-change shape**, not transient mid-bootstrap state ([repo convention §7](../../.cursor/repo_conventions.md)).
- **`TimeConstraintService` API:** group CRUD — `add_user_group`, `update_user_group`, `remove_user_group`; window edits within a USER group — `add_user_window` (validate + merge with existing), `remove_user_window` (auto-deletes group when last window removed). No plan-level replace-all in V1.
- **Invariant layout:** **`PlanTreeInvariantService` in `services/`** loads the full committed ORM graph inside `transaction()`; **ORM invariant checks** in [`calendar_backend/domain/invariant_validation.py`](../../calendar_backend/domain/invariant_validation.py) ([repo convention §9](../../.cursor/repo_conventions.md)). [`domain/constraints.py`](../../calendar_backend/domain/constraints.py) and [`domain/time.py`](../../calendar_backend/domain/time.py) are write-path/shared helpers only. Split to `domain/invariants/` later if the module grows; no separate top-level package.

**Slice order note:** Guide Prompt 7 lists APIs before helpers; this plan **reorders** so validation/normalization helpers land before mutating service methods (dependency order).

Build workflow: use `/build-plan-slice` per slice against this file; stop after each slice for approval.

## Non-goals

- Task resolution, assignment, repetition refresh/generation, `PlanTreeService` mutations, deletion preview/cascade — later prompts.
- Plan-level `set_user_constraints(replace-all)` API — deferred (group CRUD only).
- Production HTTP API, dev CLI (Prompt 18), Alembic revisions (no schema changes expected).
- OR-Tools / scheduling solver code.
- Guarding or changing [`MasterHorizonService`](../../calendar_backend/services/master_horizon.py) / future system writers — they remain legitimate direct mutators of system groups.
- Pydantic / HTTP serialization layers.

## Locked assumptions

- **Service modules:**
  - [`calendar_backend/services/time_constraint.py`](../../calendar_backend/services/time_constraint.py) (new)
  - [`calendar_backend/services/plan_tree_invariant.py`](../../calendar_backend/services/plan_tree_invariant.py) (new)
  - [`calendar_backend/services/__init__.py`](../../calendar_backend/services/__init__.py) — docstring only (no barrel re-exports).
- **Domain pure helpers:**
  - [`calendar_backend/domain/constraints.py`](../../calendar_backend/domain/constraints.py) (new) — OR-window merge/normalization and USER-group window list validation for **write paths** (no SQLAlchemy; not ORM invariant entry points — [repo convention §9](../../.cursor/repo_conventions.md)).
  - [`calendar_backend/domain/invariant_validation.py`](../../calendar_backend/domain/invariant_validation.py) (new, slice 4) — session-free **ORM invariant** checks over the full loaded plan/chain/constraint graph ([repo conventions §7–§9](../../.cursor/repo_conventions.md)).
- **DTOs** in [`calendar_backend/domain/dtos.py`](../../calendar_backend/domain/dtos.py) (import from `domain.dtos`, not [`domain/__init__.py`](../../calendar_backend/domain/__init__.py) barrel per rule 25):
  - `TimeConstraintGroupDTO` — `constraint_group_id`, `plan_id`, `constraint_kind`, `windows: tuple[_TimeWindowDTO, ...]` (`_TimeWindowDTO` is module-private: `time_window_id`, `start_time`, `end_time`; projected inline in `time_constraint_group_dto_from_rows`, no standalone window service)
- **`TimeConstraintService` public methods:**
  - `add_user_group(plan_id, windows)` — validate + merge windows; insert `ConstraintKind.USER` group + windows; return `TimeConstraintGroupDTO`.
  - `update_user_group(group_id, windows)` — reject non-USER groups; replace windows after validate + merge.
  - `remove_user_group(group_id)` — reject non-USER groups; delete windows then group.
  - `add_user_window(group_id, window)` — reject non-USER groups; validate window; merge with existing windows and replace persisted set; return `TimeConstraintGroupDTO`.
  - `remove_user_window(group_id, time_window_id)` — reject non-USER groups; delete window; if last window, delete group and return `ServiceResult[None]`, else return updated `TimeConstraintGroupDTO`.
  - All accept domain [`TimeWindow`](../../calendar_backend/domain/time.py) dataclass inputs (start/end only on add; IDs assigned on persist).
- **`PlanTreeInvariantService.validate_master_tree()`** — read-only; loads master tree graph inside `transaction(session)`; returns `ServiceResult[None]` with `success=True` and empty errors when clean, else `success=False` and one `ServiceMessage` per violation (add `MessageCode` values in slice 4 as needed, e.g. tree/subtype/chain/constraint-specific codes or a small set of invariant codes with `details` carrying path/plan_id).
- **OR-window merge:** within each group, sort by `start_time`, merge intervals that overlap or touch at a minute boundary; persist merged set; deterministic output.
- **Empty outer list:** removing all USER groups (via repeated `remove_user_group`) yields no USER groups — valid “no local restriction.” `add_user_group` with empty `windows` fails with `EMPTY_CONSTRAINT_GROUP`.
- **Slice checks:** slices 1–4 → ruff format, ruff check, pyright only; slice 5 adds pytest + **Test catalog**.
- **Test DB:** reuse [`tests/services/conftest.py`](../../tests/services/conftest.py) fixtures (temp-file SQLite, full schema via `create_all`).

## Slices

### Slice 1: Constraint validation and normalization helpers

**Objective:** Add pure domain functions for USER-group window validation and OR-window merge before any service CRUD.

**Files expected to change:**
- [`calendar_backend/domain/constraints.py`](../../calendar_backend/domain/constraints.py) (new)

**May also change:**
- [`tests/domain/test_constraints.py`](../../tests/domain/test_constraints.py) (new — unit tests for helpers; optional in slice 1 if slice 5 consolidates; prefer small unit tests here to keep slice 1 reviewable)

**Implementation steps:**
1. Add functions (names illustrative):
   - `validate_user_group_windows(windows: tuple[TimeWindow, ...]) -> ServiceMessage | None` — reject empty group (`EMPTY_CONSTRAINT_GROUP`); delegate each window to `validate_time_window`.
   - `merge_or_windows(windows: tuple[TimeWindow, ...]) -> tuple[TimeWindow, ...]` — merge overlapping/adjacent half-open intervals; assume inputs already validated ([repo convention §6](../../.cursor/repo_conventions.md)).
2. Keep module free of SQLAlchemy and service imports.
3. Document in module docstring: AND-of-OR semantics; merge applies **within** one group only.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/domain/test_constraints.py -m "not slow and not failure_expected"  # if unit tests added here
```

**Acceptance criteria:**
- Empty window list returns `EMPTY_CONSTRAINT_GROUP` validation result.
- Invalid UTC/minute/order windows surface via existing time helpers.
- Merge produces minimal equivalent OR set (overlap and touch-merge cases covered by tests).

**Risks/edge cases:**
- Touching intervals `[a,b)` and `[b,c)` merge to `[a,c)` at minute boundaries.
- Do not truncate sub-minute input — validation rejects first.

---

### Slice 2: TimeConstraintService USER group CRUD

**Objective:** Implement `TimeConstraintService` add/update/remove for `ConstraintKind.USER` groups only.

**Files expected to change:**
- [`calendar_backend/domain/dtos.py`](../../calendar_backend/domain/dtos.py) (add `TimeConstraintGroupDTO` and `time_constraint_group_dto_from_rows`; private `_TimeWindowDTO` nested in group projection)
- [`calendar_backend/services/time_constraint.py`](../../calendar_backend/services/time_constraint.py) (new)

**Implementation steps:**
1. Add frozen DTOs and mapping helpers from `TimeConstraintGroup` + `TimeWindow` rows.
2. Implement `TimeConstraintService(session, clock=None)`:
   - `add_user_group(plan_id, windows)` — inside `transaction`: verify plan exists; run domain validate + merge; insert USER group (`new_id`) + merged windows; flush; return DTO.
   - `update_user_group(group_id, windows)` — load group; **slice 3 adds system guard** (for now assume USER-only test data or stub guard if group missing).
   - `remove_user_group(group_id)` — load group; delete windows then group.
3. Use explicit `select` / `delete` / `session.get` for writes ([repo convention §3](../../.cursor/repo_conventions.md)).
4. Map domain validation failures to `fail(...)` with appropriate `MessageCode`; missing plan/group → structured failure (not bare ORM exceptions across public API).

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- Can add a USER group with merged windows on an existing plan.
- Update replaces windows (merged); remove deletes group and windows.
- Empty windows on add/update return failed `ServiceResult` without persisting partial group/window rows.
- Returns `ServiceResult[TimeConstraintGroupDTO]` on success paths.

**Risks/edge cases:**
- Plan must exist — bootstrap master in tests via `MasterPlanService` before constraint tests.
- Multiple USER groups per plan allowed (AND semantics); no `group_order` column.

---

### Slice 3: System-owned constraint edit rejection

**Objective:** Ensure `TimeConstraintService` mutating methods reject `SYSTEM_MASTER_HORIZON` and `SYSTEM_REPETITION_WINDOW` groups.

**Files expected to change:**
- [`calendar_backend/services/time_constraint.py`](../../calendar_backend/services/time_constraint.py)

**Implementation steps:**
1. Add private `_load_user_group(txn, group_id)` (load, not-found, and USER-kind guard) on `update_user_group`, `remove_user_group`, `add_user_window`, and `remove_user_window`.
2. If `constraint_kind != ConstraintKind.USER`, return `fail(ServiceMessage(code=SYSTEM_CONSTRAINT_DIRECT_EDIT_FORBIDDEN, ...))` without mutating.
3. `add_user_group` always creates `USER` — no guard needed beyond kind assignment.
4. Do **not** block [`MasterHorizonService`](../../calendar_backend/services/master_horizon.py) or other system writers.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- Updating or removing a system horizon group via `TimeConstraintService` fails with `SYSTEM_CONSTRAINT_DIRECT_EDIT_FORBIDDEN`.
- USER group operations still succeed.
- System group rows unchanged after rejected calls.

**Risks/edge cases:**
- Tests seed system horizon via `MasterHorizonService.refresh_master_horizon`, then attempt edit via `TimeConstraintService`.
- `SYSTEM_REPETITION_WINDOW` groups may not exist until RepetitionService — insert test row inline if needed for rejection test.

---

### Slice 4: PlanTreeInvariantService diagnostics

**Objective:** Implement read-only `validate_master_tree()` covering full structural diagnostics under master.

**Files expected to change:**
- [`calendar_backend/domain/errors.py`](../../calendar_backend/domain/errors.py) (add invariant-related `MessageCode` values if needed)
- [`calendar_backend/domain/invariant_validation.py`](../../calendar_backend/domain/invariant_validation.py) (new — pure structural checks)
- [`calendar_backend/services/plan_tree_invariant.py`](../../calendar_backend/services/plan_tree_invariant.py) (new)

**May also change:**
- [`tests/domain/test_invariant_validation.py`](../../tests/domain/test_invariant_validation.py) (new — unit tests for pure checks; optional here if deferred to slice 5)

**Implementation steps:**
1. Add **`domain/invariant_validation.py`** with session-free functions that take the full loaded graph and return `ServiceMessage` tuples — rules below. Do **not** re-check DB-enforced CHECK/UNIQUE ([repo convention §8](../../.cursor/repo_conventions.md)).
2. Implement **`PlanTreeInvariantService(session)`** with `validate_master_tree() -> ServiceResult[None]`:
   - Inside `transaction`: load **all** `Plan` rows and graph via ORM + `selectinload` ([repo convention §3](../../.cursor/repo_conventions.md)).
   - Call `validate_master_tree_graph` on the loaded graph; aggregate violations.
   - **Master / root:** master exists; master has `GoalPlan`; `parent_id is None` (`plan_kind == GOAL` enforced by DB — do not re-report).
   - **Reachability:** every `Plan` row reachable from master via `parent_id` tree (no orphan plans).
   - **Subtype pairing:** each plan has exactly one matching detail row for its `plan_kind` and no conflicting detail rows.
   - **Chains:** dense `sort_order` per `(goal, is_critical)` bucket; dense `position` per chain; chain item child must be direct child of parent goal (`child_plan_id` global uniqueness enforced by DB — do not re-report).
   - **Repetition instances:** dense `instance_index` and `sort_order` per bucket; global unique `root_clone_id`; root clone parented under repetition plan with `cloned_from_id == template_root_id` (when instances are loaded).
   - **Constraints:** master must have exactly one `SYSTEM_MASTER_HORIZON` group with exactly one window; horizon only on master; USER and `SYSTEM_REPETITION_WINDOW` groups non-empty when present; persisted windows minute-aligned and merged canonical form (`start < end` enforced by DB — do not re-report).
3. Read-only — no mutations. Service module keeps graph-load and `ServiceResult` wiring; violation rules live in `domain/invariant_validation.py`.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- Clean DB after Prompt 6 bootstrap + horizon refresh returns `success=True`.
- Seeded **semantic** violations (wrong subtype, orphan plan, empty USER group, misaligned chain child, broken dense ordering) produce `success=False` with actionable messages.
- Does not implement Prompt 8 deletion/cascade rules.

**Risks/edge cases:**
- Large trees: V1 solo use — full load acceptable; avoid N+1 via eager load.
- Repetition/template subtrees may exist outside chains — reachability via `parent_id` only, not chain membership.

---

### Slice 5: Service and invariant tests

**Objective:** Pytest coverage for all behavior introduced in slices 1–4.

**Files expected to change:**
- [`tests/domain/test_constraints.py`](../../tests/domain/test_constraints.py) (if not added in slice 1)
- [`tests/domain/test_invariant_validation.py`](../../tests/domain/test_invariant_validation.py) (new — pure invariant checks)
- [`tests/services/test_time_constraint_service.py`](../../tests/services/test_time_constraint_service.py) (new)
- [`tests/services/test_plan_tree_invariant_service.py`](../../tests/services/test_plan_tree_invariant_service.py) (new)
- [`tests/services/test_foundational_invariants.py`](../../tests/services/test_foundational_invariants.py) (extend — system-edit rejection note from Prompt 6)

**Implementation steps:**
1. **`test_time_constraint_service.py`:** add/update/remove USER groups; add/remove USER windows (merge on add, auto-delete group on last remove); merge behavior persisted; empty group rejected; naive/non-UTC/non-minute windows rejected; system group update/remove/window mutations forbidden; no partial persistence on failure.
2. **`test_plan_tree_invariant_service.py`:** clean tree passes after bootstrap + horizon refresh; violations for orphan plan, subtype mismatch, empty USER group, misaligned chain child, broken dense ordering/uniqueness (schema CHECK/UNIQUE cases belong in model schema tests, not invariant replay).
3. **`test_constraints.py` (domain):** merge and validate unit cases if not done in slice 1.
4. **`test_invariant_validation.py` (domain):** pure structural violation cases without DB.
5. **`test_foundational_invariants.py`** docstring / add test that `TimeConstraintService` cannot mutate system horizon created by `MasterHorizonService`.
6. Mark `@pytest.mark.integration` where using engine/session; inline row helpers only (no shared factory registry).
7. Post **Test catalog** in chat per guide §9.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/domain/test_constraints.py tests/domain/test_invariant_validation.py tests/services/test_time_constraint_service.py tests/services/test_plan_tree_invariant_service.py tests/services/test_foundational_invariants.py -m "not slow and not failure_expected"
```

**Acceptance criteria:**
- All new tests pass; existing suite still green.
- Tests cover **all** public behavior from slices 1–4 (implementation-chunk coverage rule).
- Chat report includes grouped **Test catalog**.

**Risks/edge cases:**
- Use [`tests/services/conftest.py`](../../tests/services/conftest.py); do not depend on local `local_data/calendar_backend.sqlite3`.
- SQLite datetime comparisons may need `.replace(tzinfo=UTC)` when reading ORM rows (same as Prompt 6 tests).

---

## Abstraction check

| Introduced item | Needed now? | Justification |
|-----------------|-------------|---------------|
| `TimeConstraintService` | Yes | Design §7 named USER constraint mutation path |
| `PlanTreeInvariantService` | Yes | Design-deferred tree/subtype/constraint diagnostics ([`core_plan_orm_models.md`](core_plan_orm_models.md) locked decision) |
| `domain/constraints.py` pure functions | Yes | Write-path constraint semantics; shared helpers callable from invariant checks ([repo convention §9](../../.cursor/repo_conventions.md)) |
| `domain/invariant_validation.py` pure functions | Yes | ORM invariant checks over loaded graph ([repo conventions §7–§9](../../.cursor/repo_conventions.md)); service loads graph |
| `TimeConstraintGroupDTO` (with private `_TimeWindowDTO` window elements) | Yes | Design §8.2 group service return type; no standalone window CRUD in V1 |
| Private `_load_user_group` in service | Yes | Single load + system-edit rejection for USER-group mutations |
| Repository / DAO / service base class | No | Matches existing services (`Session` direct) |
| Plan-level replace-all API | No | Explicitly deferred (group CRUD sufficient for V1) |
| Separate top-level `invariant_validation` package | No | Use `domain/invariant_validation.py`; split to `domain/invariants/` subpackage if needed ([repo convention §9](../../.cursor/repo_conventions.md)) |
| Constraint merge registry/strategy | No | One merge algorithm suffices |

## Dependency changes

None expected — stdlib + existing SQLAlchemy stack.

```bash
uv sync   # if fresh clone only
```

## Open questions

None blocking implementation.
