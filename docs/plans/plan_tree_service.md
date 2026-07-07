# Plan: Plan tree service

**Finalized plan location:** `docs/plans/plan_tree_service.md`

## Context

Implement Prompt 8 from [docs/cursor_implementation_guide.md](../cursor_implementation_guide.md): **`GoalService`** for goal-parent plan creation and child-chain layout (`create_child`, `move_plan`); **`PlanTreeService`** for plan-wide identity and existence (`rename_plan`, `preview_delete`, `delete_plan`), per engineering design §5.2 (goal child chains), §5.3 (repetition shell), §7 (service layer), §9.6 (deletion impact), and Appendix invariants. **Guide + this plan supersede PDF §7** on service ownership ([guide §0.1](../cursor_implementation_guide.md), [repo convention §14](../../.cursor/repo_conventions.md)).

Design constraints:
- [`calendar_backend/services/`](../../calendar_backend/services/) owns public mutation methods, validation, transactions, and persistence-changing behavior; ORM models are persistence records only ([layer boundaries](../../.cursor/rules/10-layer-boundaries.mdc)).
- Public methods return **`ServiceResult[T]`** via [`calendar_backend/domain/results.py`](../../calendar_backend/domain/results.py); mutations run inside [`transaction(session)`](../../calendar_backend/db/session.py) ([repo convention §2](../../.cursor/repo_conventions.md)).
- **Tree invariants:** chain-member children under goals are reachable from master and appear in exactly one `GoalChildChainItem`; repetition/template subtrees use `parent_id` only (no chain); master child chains are non-critical; subtype pairing is service-enforced ([`PlanTreeInvariantService`](../../calendar_backend/services/plan_tree_invariant.py) + [`domain/invariant_validation.py`](../../calendar_backend/domain/invariant_validation.py) per [repo conventions §7–§9](../../.cursor/repo_conventions.md)).
- **Deletion semantics (design):** ordinary deletion cascades to descendants; deleting any plan inside a goal child chain deletes the **whole chain** (and its member plans per cascade rules); deleting a **critical** chain deletes the parent goal and may cascade upward; master is never deleted.
- **Persistence access:** relationship navigation + eager load for graph reads (previews, cascade planning); explicit `select` / `delete` / `session.get` for filtered writes ([repo convention §3](../../.cursor/repo_conventions.md)). No ORM `cascade="all, delete-orphan"` on plan trees.
- **Prompt 7 complete:** master/settings/horizon services, `TimeConstraintService`, and `PlanTreeInvariantService` exist ([`time_constraint_invariant_validation.md`](time_constraint_invariant_validation.md)).

**Locked clarifications (request-questions):**
- **Deletion preview boundary:** keep Prompt 8 **minimal** — cascade computation and `preview_delete` / `delete_plan` live in `PlanTreeService` (private helpers in [`plan_tree.py`](../../calendar_backend/services/plan_tree.py)); defer `calendar_backend/deletion/` package and `DeletionPreviewService` class to Prompt 12 (extract/refactor only; semantics unchanged).
- **Chain on create:** caller supplies `is_critical`; service always creates a **new** `GoalChildChain` at the end of that bucket (`sort_order = max + 1` among chains with same `parent_goal_id` and `is_critical`) with a single `GoalChildChainItem` at `position = 0`. Reject `is_critical=True` when parent is master.
- **create_child (REPETITION, minimal):** via `GoalService.create_child` — persist repetition shell + empty goal template stub (`clone_status=TEMPLATE`); set `template_root_id`; `generated_at` null; zero `RepetitionInstance` rows; no `SYSTEM_REPETITION_WINDOW` materialization; **goal template only** until Prompt 10 (no template subtree in payload).
- **move_plan (GoalService):** chain reordering under the **same parent goal** only — **not reparenting** (reparenting out of scope for V1). API shape:
  - **Single index** `position`: reorder within the plan’s current chain (dense `position` renumbering); `position = -1` append within current chain.
  - **Pair** `(chain_index, position)`: move to another chain under the same parent; `position = -1` append; `chain_index = -1` create new chain at end of bucket, **inferring `is_critical` from the plan’s current chain**.
- **rename_plan (PlanTreeService):** update `plan.name` and `updated_at` only.

Build workflow: use `/build-plan-slice` per slice against this file; stop after each slice for approval.

## Non-goals

- **Reparenting** (`parent_id` changes) — not V1.
- `RepetitionService` refresh/generation, instance materialization, `SYSTEM_REPETITION_WINDOW` constraints, repetition lock rules — Prompt 10.
- `TaskService` (`mark_complete`, `reopen`, scheduling-field updates, clone detachment) — Prompt 9.
- `DeletionPreviewService`, `ConflictDeletionSuggestionService`, conflict ranking — Prompt 12 (Prompt 8 owns minimal preview + real delete only).
- `PlanTreeInvariantService` expansion beyond post-mutation validation in tests (existing invariant suite is sufficient for ideal persisted shape).
- Production HTTP API, dev CLI commands, Alembic revisions (no schema changes expected).
- OR-Tools / scheduling solver code.
- Pydantic / HTTP serialization layers.

## Locked assumptions

- **Service modules:** [`calendar_backend/services/goal.py`](../../calendar_backend/services/goal.py) with `GoalService(session, clock=None)`; [`calendar_backend/services/plan_tree.py`](../../calendar_backend/services/plan_tree.py) with `PlanTreeService(session, clock=None)`.
- **External public API (V1):**
  - **`GoalService`:** `create_child(parent_id, kind, payload, is_critical)` — single entry for creating goal/task/repetition under a goal parent; typed payloads in [`domain/plan_create.py`](../../calendar_backend/domain/plan_create.py); `@overload` return types per `PlanKind`; **`move_plan`** for goal child-chain reorder.
  - **`PlanTreeService`:** `rename_plan`, `preview_delete`, `delete_plan` (slices 2–4 rename; slices 3–4 delete).
- **Repo-internal sibling API on `PlanTreeService` (not external/CLI):** `make_goal`, `make_task`, `make_repetition`, `attach_under_parent` — take active `txn: Session`; insert orphan plan + subtype rows or set `parent_id` only (no chain).
- **Goal-chain layout:** private to `GoalService` (`_attach_to_goal_chain`, chain move helpers); not on `PlanTreeService`.
- **Contract:** goal parents own child-chain layout (`GoalService` create + move); plans edit their own attributes (`rename_plan` on `PlanTreeService`; `TaskService`, `RepetitionService` for subtype fields — Prompts 9–10).
- **Parent validation:** `parent_id` must reference an existing `GoalPlan` (not task/repetition leaf unless design allows — **parent must be GOAL**); child becomes direct tree child of that goal; chain item aligns with same parent ([`invariant_validation.py`](../../calendar_backend/domain/invariant_validation.py) chain alignment rule).
- **Master rules:** master cannot be deleted, moved, or renamed away from `MASTER_PLAN_NAME` convention; plans created under master must use `is_critical=False`.
- **Task defaults on create:** `user_completed=False`, `completed_at=None`, `clone_status=NOT_CLONED`, `cloned_from_id=None`.
- **Repetition template stub:** template root is a child `GoalPlan` of the repetition plan node (`attach_under_parent`, not in chain); `template_root_id` points at that child; template subtree expansion deferred to `RepetitionService` (Prompt 10) — slice 1 creates empty goal shell only; payload must not include template children.
- **Deletion order:** explicit deletes for dependent rows (chain items/chains, constraint windows/groups, repetition instances, calendar entries with `source_plan_id`, subtype rows, plan rows) respecting SQLite FK RESTRICT — no reliance on DB ON DELETE CASCADE.
- **Preview/delete parity:** `delete_plan` must delete exactly the plan IDs (and linked calendar entries) computed by the same private cascade function used by `preview_delete`.
- **Slice checks:** slices 1–4 → ruff format, ruff check, pyright; slice 5 adds pytest + **Test catalog** posted in chat.
- **Test DB:** reuse [`tests/services/conftest.py`](../../tests/services/conftest.py) (`service_db_session`, `fake_clock`, `service_transaction`).

## Slices

### Slice 1: Create operations

**Objective:** Implement `GoalService.create_child` and repo-internal `PlanTreeService.make_*` / `attach_under_parent` with chain placement, subtype row creation, and parent/tree validation.

**Files expected to change:**
- [`calendar_backend/services/goal.py`](../../calendar_backend/services/goal.py) (new)
- [`calendar_backend/services/plan_tree.py`](../../calendar_backend/services/plan_tree.py) — insert/attach primitives (no public create methods)
- [`calendar_backend/domain/plan_create.py`](../../calendar_backend/domain/plan_create.py) (new) — frozen create payloads + kind/payload validation
- [`calendar_backend/domain/repetitions.py`](../../calendar_backend/domain/repetitions.py) (new) — `validate_repetition_create`
- [`calendar_backend/domain/dtos.py`](../../calendar_backend/domain/dtos.py) — `TaskPlanDTO`, `RepetitionPlanDTO`, mappers (if not already present)
- [`calendar_backend/domain/errors.py`](../../calendar_backend/domain/errors.py) — e.g. `INVALID_CREATE_PAYLOAD`, mutation codes
- [`calendar_backend/domain/tasks.py`](../../calendar_backend/domain/tasks.py) — `validate_task_create`

**May also change:**
- [`docs/cursor_implementation_guide.md`](../cursor_implementation_guide.md) — §0.1 supersession row; Prompt 8/10 text

**Implementation steps:**
1. Add frozen create payloads in `domain/plan_create.py` (`GoalCreatePayload`, `TaskCreatePayload`, `RepetitionCreatePayload` with `template_type` / `template_payload`) and `validate_create_payload`; add `validate_repetition_create` in `domain/repetitions.py`.
2. Move repetition settings validation to `domain/repetitions.py`.
3. Implement `PlanTreeService.make_goal`, `make_task`, `make_repetition`, `attach_under_parent` (orphan inserts / tree link only; docstring: sibling services only).
4. Implement `GoalService.create_child` with `@overload` returns (annotate type-checker-only code per repo convention §10); private `_load_parent_goal`, `_attach_to_goal_chain`.
5. **`GOAL` / `TASK` branches:** `make_*` → `_attach_to_goal_chain` (sets `parent_id` + new chain at end of `(parent_goal_id, is_critical)` bucket, single item at `position=0`).
6. **`REPETITION` branch:** validate settings; `PlanTreeService.make_repetition` (repetition scalars + `template_type` / `template_payload`) creates template via `_make_template_root`, shell via `_insert_repetition_plan`, and `attach_under_parent` (template under shell); `GoalService` only `_attach_to_goal_chain` (shell under goal parent).
7. Map validation failures to `fail(...)`; `GoalService` opens `transaction()`, passes `txn` to `PlanTreeService` helpers.
8. Do **not** call `PlanTreeInvariantService` in production path yet (optional in slice 5 tests).
9. Persisted-shape enforcement for create validators follows [repo convention §11](../../.cursor/repo_conventions.md): ORM CHECKs on subtype tables + invariant catch-up in `domain/invariant_validation.py` (migration for CHECKs is a separate db-revision step).

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- `GoalService.create_child(GOAL|TASK|REPETITION, …)` under a goal persists correct `parent_id`, subtype row, and new chain at end of `is_critical` bucket with one item (repetition shell in chain; template attached via `attach_under_parent` only).
- Creating under master succeeds with `is_critical=False`; `is_critical=True` rejected.
- Invalid parent, kind/payload mismatch, invalid task/repetition fields fail without partial persistence.
- Repetition leaves `generated_at` null and creates no `RepetitionInstance` rows.

**Risks/edge cases:**
- First child under a goal: `sort_order` starts at 0.
- Orphan `parent_id=None` on `make_*` rows is transient within transaction until attach.
- Template stub must be reachable in tree (child of repetition plan) so reachability invariant holds.

---

### Slice 2: Move and rename operations

**Objective:** Implement `GoalService.move_plan` (chain reorder / cross-chain under same parent) and `PlanTreeService.rename_plan`.

**Files expected to change:**
- [`calendar_backend/services/goal.py`](../../calendar_backend/services/goal.py) — `move_plan` and goal child-chain layout helpers
- [`calendar_backend/services/plan_tree.py`](../../calendar_backend/services/plan_tree.py) — `rename_plan` only

**May also change:**
- [`calendar_backend/domain/errors.py`](../../calendar_backend/domain/errors.py) — e.g. `INVALID_MOVE`, `PLAN_NOT_IN_CHAIN`

**Implementation steps:**
1. **`rename_plan` (PlanTreeService):** load plan by id; reject master rename if name change forbidden (allow master name update only if design permits — V1: allow rename of any non-master; master rename optional/minimal: reject or no-op per test choice — **reject renaming master** to keep `MASTER_PLAN_NAME` stable); update `name`, `updated_at`.
2. **`move_plan(plan_id, position)` (GoalService):** locate plan’s current `GoalChildChainItem` and chain; validate `position` in range or implement insert-shift semantics with dense renumbering; reject if plan has no chain item (orphan) or is master.
3. **`move_plan(plan_id, chain_index, position)` (GoalService):** enumerate parent goal’s chains in stable order (e.g. critical bucket first, then `sort_order`, tie-break `goal_child_chain_id`); resolve `chain_index=-1` → new chain at end of current item’s `is_critical` bucket; resolve `position=-1` → append; move item between chains: remove from source (renumber source chain); insert into target; if source chain empty, delete chain header.
4. **Guards:** parent goal unchanged; cannot move master; cannot move template/repetition nodes in ways that break invariants (if move applies only to chain-member children, document and reject repetition template roots without chain items).
5. Maintain dense `position` per chain; reuse `_create_chain_at_bucket_end` from create path where applicable.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- Single-index move reorders within chain with dense positions.
- Pair-index move can move to another chain, append (`position=-1`), or create new chain (`chain_index=-1`) inferring `is_critical` from current chain.
- `rename_plan` updates persisted name; master delete/move/rename guards consistent with slice 4.
- No `parent_id` changes in any move path.

**Risks/edge cases:**
- Moving the only item out of a chain deletes empty chain header.
- Cross-bucket move (critical → non-critical) requires `chain_index` targeting a chain in the other bucket or `-1` with inferred bucket from **current** chain — moving across buckets may need explicit target chain in bucket; **`-1` only creates within inferred `is_critical` bucket** (no cross-bucket `-1`).
- Repetition plan nodes that are chain members follow same rules as goals/tasks.

---

### Slice 3: Deletion preview foundations

**Objective:** Add minimal deletion-impact types and pure/session-free cascade planning over a loaded graph; implement `preview_delete` without persisting deletes.

**Files expected to change:**
- [`calendar_backend/domain/dtos.py`](../../calendar_backend/domain/dtos.py) — `PlanDeletionPreviewDTO` (minimal: `root_plan_id`, `affected_plan_ids: tuple[PlanID, ...]`, `affected_calendar_entry_ids: tuple[...]`, optional `warnings`)
- [`calendar_backend/services/plan_tree.py`](../../calendar_backend/services/plan_tree.py) — private `_compute_deletion_impact(root_plan_id, graph) -> PlanDeletionPreviewDTO`

**May also change:**
- [`calendar_backend/domain/deletion.py`](../../calendar_backend/domain/deletion.py) (new, optional) — **only if** cascade logic is large enough to test without DB; otherwise keep private functions in `plan_tree.py` per abstraction discipline

**Implementation steps:**
1. Define minimal `PlanDeletionPreviewDTO` (Prompt 12 will extend toward full design `DeletionPreview`).
2. Inside `transaction`, load plan subtree graph with eager loads sufficient for cascade: `Plan.children` (recursive or iterative BFS), chains/items, repetition instances, constraint groups, calendar entries by `source_plan_id` (explicit query).
3. **`_compute_deletion_impact`** (pure over loaded data):
   - Reject master root.
   - **Chain rule:** if root plan appears in any `GoalChildChainItem`, expand to **all plans referenced by items in that chain** (whole chain), then apply descendant cascade from each chain member.
   - **Descendant rule:** union all plans reachable via `parent_id` children from every plan in the delete set.
   - **Critical chain rule (preview):** if deleting a set that equals all items of a critical chain, include parent goal; recursively apply critical-chain-upward rule on included goals.
   - Collect `CalendarEntry` rows with `source_plan_id` in affected plan set.
   - Return stable-sorted ID tuples for determinism.
4. **`preview_delete(plan_id)`:** load graph, compute impact, return `ok(preview)`; `PLAN_NOT_FOUND` on missing id.
5. No persistence changes in this slice.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- `preview_delete` returns deterministic affected plan sets for: leaf delete, descendant cascade, whole-chain expansion, critical-chain parent inclusion.
- Master delete attempt returns structured failure without mutation.
- Preview logic is shared by reference from slice 4 `delete_plan` (same function).

**Risks/edge cases:**
- Plans in multiple chains impossible by schema (`UNIQUE(child_plan_id)`).
- Repetition template subtrees: descendants of repetition/template nodes included in ordinary descendant cascade.
- Clone lineage (`cloned_from_id`) does not block deletion; linked clones delete with subtree unless design carves out — **ordinary deletion cascades through descendants** includes clone subtrees.

---

### Slice 4: Real deletion and cascade parity

**Objective:** Implement `delete_plan` using slice 3 impact computation; explicit SQL deletes in FK-safe order; parity with preview.

**Files expected to change:**
- [`calendar_backend/services/plan_tree.py`](../../calendar_backend/services/plan_tree.py)

**May also change:**
- [`calendar_backend/domain/errors.py`](../../calendar_backend/domain/errors.py) — e.g. `MASTER_DELETE_FORBIDDEN`

**Implementation steps:**
1. **`delete_plan(plan_id)`:** inside `transaction`, compute impact via `_compute_deletion_impact`; if master in set, abort with `MASTER_DELETE_FORBIDDEN`.
2. **Delete order (illustrative — adjust to actual FK graph):**
   - `CalendarEntry` where `source_plan_id` in affected set
   - `GoalChildChainItem` / `GoalChildChain` rows touching affected plans (may already be subsumed by chain expansion)
   - `TimeWindow` → `TimeConstraintGroup` for affected plans
   - `RepetitionInstance` for affected repetition plans
   - Subtype rows (`task_plan`, `goal_plan`, `repetition_plan`)
   - `Plan` rows in reverse topological order (children before parents) or repeated passes until clear
3. Use explicit `delete(...)` / `session.delete` per [repo convention §3](../../.cursor/repo_conventions.md); do not rely on ORM cascades.
4. After delete, affected plans absent from DB; no orphan `parent_id` pointers among remaining rows.
5. Optional dev assertion: re-load graph and run `PlanTreeInvariantService.validate_master_tree()` in tests only (slice 5).

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- `delete_plan` removes exactly the IDs returned by `preview_delete` for the same DB state.
- Whole-chain, descendant, critical-chain-upward, and calendar-entry cases work end-to-end.
- Master cannot be deleted.
- Transaction rolls back entirely on mid-delete failure (no partial tree).

**Risks/edge cases:**
- FK RESTRICT ordering bugs surface as `IntegrityError` — test ordering thoroughly in slice 5.
- Deleting goal that still has children **not** in delete set should be impossible if cascade set computed correctly.
- Empty DB after deleting all non-master plans still leaves master reachable.

---

### Slice 5: Tests for tree invariants and deletion behavior

**Objective:** Add pytest coverage for slices 1–4; post **Test catalog** in chat per guide §9.

**Files expected to change:**
- [`tests/services/test_goal_service.py`](../../tests/services/test_goal_service.py) (new — create + move)
- [`tests/services/test_plan_tree_service.py`](../../tests/services/test_plan_tree_service.py) (new — rename/delete)
- [`tests/domain/test_deletion_impact.py`](../../tests/domain/test_deletion_impact.py) (new, optional — if pure cascade helper extracted to `domain/`)
- [`tests/services/test_foundational_invariants.py`](../../tests/services/test_foundational_invariants.py) (extend — bootstrap via create APIs if useful)

**May also change:**
- [`tests/domain/test_tasks.py`](../../tests/domain/test_tasks.py) (new, if `domain/tasks.py` added)

**Implementation steps:**
1. **Create tests:** via `GoalService.create_child` — goal/task/repetition under master and nested goal; chain `sort_order`/`position` after sequential creates; master non-critical enforcement; invalid parents and invalid task/repetition fields.
2. **Move tests (GoalService):** within-chain reorder; cross-chain move; `chain_index=-1` / `position=-1`; master move guards.
3. **Rename tests (PlanTreeService):** rename persistence; master rename guard.
4. **Preview/delete parity tests:** leaf, subtree, chain-member triggers whole chain, critical chain upward, calendar entries removed, master delete forbidden.
5. **Invariant integration:** after successful mutations, `PlanTreeInvariantService.validate_master_tree()` passes on clean cases.
6. Inline ORM builders only (no shared factory registry); use `service_db_session` + `fake_clock`; mark `@pytest.mark.integration` where appropriate.
7. Post grouped **Test catalog** in chat.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/services/test_goal_service.py tests/services/test_plan_tree_service.py tests/domain/test_deletion_impact.py tests/domain/test_tasks.py -m "not slow and not failure_expected"
```

**Acceptance criteria:**
- All new tests pass; existing suite still green.
- Tests cover **all** public behavior from slices 1–4 (implementation-chunk coverage rule).
- Chat report includes grouped **Test catalog**.

**Risks/edge cases:**
- Build helpers to seed chains/goals/tasks without going through service for failure cases only when service path is insufficient.
- SQLite datetime timezone consistency (match Prompt 6/7 tests).

---

## Abstraction check

| Introduced item | Needed now? | Justification |
|-----------------|-------------|---------------|
| `GoalService` | Yes | External create-under-goal API; goal child-chain layout (create + move) |
| `PlanTreeService` | Yes | Plan identity/existence (rename, delete slices 2–4) + repo-internal insert/attach |
| `domain/plan_create.py`, `domain/repetitions.py` | Yes | Typed create payloads and session-free validation |
| `TaskPlanDTO`, `RepetitionPlanDTO`, `PlanDeletionPreviewDTO` | Yes | Design §8.2 service return types |
| `domain/tasks.py` validate helper | Yes | Shared task field rules before `TaskService` |
| Private `_compute_deletion_impact` | Yes | Preview/delete parity requirement |
| Private `_attach_to_goal_chain` / chain layout helpers on `GoalService` | Yes | Goal-context chain placement and move |
| `calendar_backend/deletion/` package | No | Deferred to Prompt 12 per locked clarification |
| `DeletionPreviewService` class | No | Prompt 12 extraction from working `PlanTreeService` logic |
| Repository / DAO / service base class | No | Matches existing services |
| Chain index registry / strategy pattern | No | Direct enumeration suffices |

## Dependency changes

None expected — stdlib + existing SQLAlchemy stack.

```bash
uv sync   # if fresh clone only
```

## Open questions

None blocking implementation.

**Prompt 12 note:** When implementing `DeletionPreviewService`, refactor `_compute_deletion_impact` into `calendar_backend/deletion/` without changing semantics; extend DTO toward full design `DeletionPreview` (depth counts, ranking keys).
