# Plan: Invariant and integration test hardening

**Finalized plan location:** [`docs/plans/test_hardening.md`](test_hardening.md)

## Context

Implement Prompt 19 from [docs/cursor_implementation_guide.md](../cursor_implementation_guide.md): **test hardening after core services exist**. Review the updated V1 engineering design testing strategy via the guide and finalized per-prompt plans (the PDF is cited but not stored in-repo; guide §0.1 and repo conventions supersede on invariant/template semantics).

The suite already has ~450 tests across [`tests/`](../../tests/); this plan **adds missing coverage** and **removes stale deferrals**—it does not re-implement services.

**Authority:** [`.cursor/repo_conventions.md`](../../.cursor/repo_conventions.md) §7–§9 (invariant semantics, no schema replay), §13 (`failure_expected`), §19 (meaningful guarantees); guide **§0.1** template semantics; guide **§9** Test-creation slice convention.

**Already done (dependencies):**
- Prompts 6–18: foundational services, invariants, plan tree, task/repetition/resolution, deletion preview, heuristic + exact solvers, assignment, free-time, orchestration, dev CLI
- Per-prompt test slices in [`tests/domain/`](../../tests/domain/), [`tests/services/`](../../tests/services/), [`tests/scheduling/`](../../tests/scheduling/), [`tests/deletion/`](../../tests/deletion/), [`tests/orchestration/`](../../tests/orchestration/), [`tests/models/`](../../tests/models/)
- Shared DB patterns in [`tests/services/conftest.py`](../../tests/services/conftest.py), [`tests/orchestration/conftest.py`](../../tests/orchestration/conftest.py), [`tests/orchestration/orch_helpers.py`](../../tests/orchestration/orch_helpers.py)

**Locked clarifications (request-questions):**
- **Shared fixtures:** no Slice 0 — extract helpers **inline** inside category slices when duplication becomes painful (e.g. [`tests/support/repetition_fixtures.py`](../../tests/support/repetition_fixtures.py) first touched in Slice 3).
- **`failure_expected` audit:** 5 of 6 markers are **stale** (tests pass today); 1 test asserts **removed** pre-generation invariant (relaxed in Prompt 10). Cleanup belongs in **Slice 1** — no Alembic work.
- **Assignment scope:** Slice 5 includes [`tests/scheduling/`](../../tests/scheduling/) in addition to service/domain assignment tests.

Build workflow: use `/build-plan-slice` per slice against this file; stop after each slice for approval.

```mermaid
flowchart LR
    s1[Slice1 Invariant]
    s2[Slice2 Domain]
    s3[Slice3 Repetition]
    s4[Slice4 Resolution]
    s5[Slice5 Assignment]
    s6[Slice6 FreeTime]
    s7[Slice7 Deletion]
    s8[Slice8 Integration]
    s1 --> s2 --> s3 --> s4 --> s5 --> s6 --> s7 --> s8
```

## Non-goals

- Production HTTP API, dev CLI changes (Prompt 18 complete)
- New features or Prompt 20 conformance audit
- Alembic revisions / unblocking schema via migration (unless a failing test proves a CHECK gap and user approves a separate `/db-revision-preview` turn)
- Broad test refactors unrelated to coverage gaps (rename-only, style-only)
- Re-testing ORM schema CHECK/FK already covered in [`tests/models/`](../../tests/models/) (invariant slice must not replay §8 schema rules)
- Replacing the entire per-prompt test suites — harden **gaps** only

## Locked assumptions

- **Bug-fix policy:** If a new test exposes a real bug, fix production in the **same slice**; note under **Consistency & divergence**; prefer a separate commit (behavior vs tests) when using `/commit-changes`.
- **Determinism:** Use [`FakeClock`](../../tests/services/conftest.py) / fixed `RUN_AT`; stub heavy orchestration only where existing patterns do ([`tests/orchestration/`](../../tests/orchestration/)); no wall-clock dependence.
- **Markers:** `@pytest.mark.integration` on DB-touching tests; `@pytest.mark.slow` only when full horizon/solver cost warrants exclusion from default CI; never add `failure_expected` unless a deferred migration truly blocks the assertion (repo §13).
- **Template semantics (§0.1):** Each slice that touches repetition/delete/refresh must include at least one case from the mandated trio where category-relevant:
  1. Template-goal chaining (normal `GoalService.create_child` under template goal; refresh materializes on `LINKED` only)
  2. Template-root delete includes repetition shell
  3. Refresh vs `DETACHED` clones (`LINKED` propagates; `DETACHED` subtree untouched)
- **Slice checks:** every slice → ruff format, ruff check, pyright; add pytest + **Test catalog** posted in chat (guide §9). Default: `uv run pytest -m "not slow and not failure_expected"` (targeted paths first; full suite if shared infra touched).

## Baseline (current gaps to close)

| Category | Primary files | Known gaps |
|----------|---------------|------------|
| Invariant | [`test_invariant_validation.py`](../../tests/domain/test_invariant_validation.py), [`test_plan_tree_invariant_service.py`](../../tests/services/test_plan_tree_invariant_service.py) | Stale `failure_expected`; obsolete pre-generation test; limited `DETACHED`/clone-lineage graph cases |
| Domain validation | [`test_plan_create.py`](../../tests/domain/test_plan_create.py), [`test_tasks.py`](../../tests/domain/test_tasks.py), [`test_repetitions.py`](../../tests/domain/test_repetitions.py), [`test_constraints.py`](../../tests/domain/test_constraints.py), [`test_time.py`](../../tests/domain/test_time.py), [`test_enums_errors.py`](../../tests/domain/test_enums_errors.py) | Service-adjacent pure validation edges not covered in per-prompt slices |
| Repetition | [`test_repetition_service.py`](../../tests/services/test_repetition_service.py), [`test_goal_service.py`](../../tests/services/test_goal_service.py) | Settings-update rejections at service layer; template chain reorder → refresh; nested repetition template refresh |
| Resolution | [`test_resolution.py`](../../tests/domain/test_resolution.py), [`test_task_resolution_service.py`](../../tests/services/test_task_resolution_service.py) | Completed-predecessor exclusion at service layer; richer `invalid_incomplete` variety; multi-source constraint intersection |
| Assignment | [`test_assignment.py`](../../tests/domain/test_assignment.py), [`test_task_assignment_service.py`](../../tests/services/test_task_assignment_service.py), [`tests/scheduling/`](../../tests/scheduling/), [`test_conflict_analysis.py`](../../tests/deletion/test_conflict_analysis.py) | Cross-layer precedence from clones; occupied+constraint stress; solver warning propagation; DB-backed conflict path gaps |
| Free-time | [`test_free_time.py`](../../tests/domain/test_free_time.py), [`test_free_time_activity_service.py`](../../tests/services/test_free_time_activity_service.py), [`test_free_time_assignment_service.py`](../../tests/services/test_free_time_assignment_service.py) | Multi-activity proportional split; prerequisite-unblock; repetition logical completeness at service layer |
| Deletion | [`test_deletion_impact.py`](../../tests/domain/test_deletion_impact.py), [`test_deletion_conflict.py`](../../tests/domain/test_deletion_conflict.py), [`tests/deletion/`](../../tests/deletion/), [`test_plan_tree_service.py`](../../tests/services/test_plan_tree_service.py) | Task template-root **execute** parity; `DETACHED` delete impact; template/repetition conflict shapes in suggestions |
| Integration | [`test_refresh_schedule_integration.py`](../../tests/orchestration/test_refresh_schedule_integration.py), [`test_refresh_schedule_state.py`](../../tests/orchestration/test_refresh_schedule_state.py) | Heuristic-enabled refresh; repetition-step failure isolation; multi-repetition; post-delete cascade at orchestration level |

## Slices

### Slice 1: Invariant tests (post Test catalog in chat)

**Objective:** Close invariant coverage gaps and clean up stale `failure_expected` markers; strengthen §0.1 clone-lineage diagnostics without replaying DB CHECKs.

**Files expected to change:**
- [`tests/services/test_plan_tree_invariant_service.py`](../../tests/services/test_plan_tree_invariant_service.py) — remove 4 stale markers; delete or rewrite `test_validate_master_tree_reports_pre_generation_with_instances` (Prompt 10 relaxed rule)
- [`tests/models/test_plans_schema.py`](../../tests/models/test_plans_schema.py) — remove 2 stale markers on passing FK/relationship tests
- [`tests/domain/test_invariant_validation.py`](../../tests/domain/test_invariant_validation.py) — pure graph cases for clone lineage / `DETACHED` subtree shape / mixed sibling clones (if not already covered)

**May also change:**
- [`calendar_backend/domain/invariant_validation.py`](../../calendar_backend/domain/invariant_validation.py) — **only if** a new pure test exposes a real invariant gap (unlikely given Prompt 10 relaxation intent)

**Implementation steps:**
1. Remove `@pytest.mark.failure_expected` from 5 passing tests; confirm they run in default CI marker set.
2. Remove obsolete pre-generation service test **or** replace with a test documenting current semantics (instances allowed when `generated_at is None`; window checks skipped per [`_check_repetition_instance_windows`](../../calendar_backend/domain/invariant_validation.py)).
3. Add pure invariant tests: `DETACHED` clone wrong `cloned_from_id`; `LINKED`/`DETACHED` siblings under same repetition; template descendant must not carry `TEMPLATE` status.
4. Add service integration tests: invariant passes after valid repetition generate+refresh; reports violations for corrupted clone parent (extend existing wrong-parent test patterns).
5. Post **Test catalog** grouped by file.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/domain/test_invariant_validation.py tests/services/test_plan_tree_invariant_service.py tests/models/test_plans_schema.py -m "not slow and not failure_expected"
```

**Acceptance criteria:**
- Zero incorrect `failure_expected` markers in touched files.
- New invariant cases assert product-meaningful guarantees (repo §19), not CHECK replay.
- Test catalog posted in chat.

**Risks/edge cases:** Do not reintroduce pre-generation prohibition contradicting Prompt 10 comment in `invariant_validation.py`.

---

### Slice 2: Domain validation tests (post Test catalog in chat)

**Objective:** Harden session-free boundary validation for create/update payloads and shared helpers—not invariant graph rules (Slice 1) or resolution/assignment/deletion domain modules (later slices).

**Files expected to change:**
- [`tests/domain/test_plan_create.py`](../../tests/domain/test_plan_create.py)
- [`tests/domain/test_tasks.py`](../../tests/domain/test_tasks.py)
- [`tests/domain/test_repetitions.py`](../../tests/domain/test_repetitions.py) — **validation-only** cases (settings locks, template payload rules); generation/refresh scenarios deferred to Slice 3
- [`tests/domain/test_constraints.py`](../../tests/domain/test_constraints.py)
- [`tests/domain/test_time.py`](../../tests/domain/test_time.py)
- [`tests/domain/test_enums_errors.py`](../../tests/domain/test_enums_errors.py)

**Implementation steps:**
1. Audit each module against its production validator in [`calendar_backend/domain/`](../../calendar_backend/domain/); add missing rejection paths (minute alignment, empty OR groups, fraction bounds, enum coverage).
2. Add cross-field pairing tests mirroring service boundaries (e.g. divisible task chunk rules, repetition mode/count/end_time locks).
3. Keep tests pure (no SQLAlchemy session).
4. Post **Test catalog**.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/domain/test_plan_create.py tests/domain/test_tasks.py tests/domain/test_repetitions.py tests/domain/test_constraints.py tests/domain/test_time.py tests/domain/test_enums_errors.py -m "not slow and not failure_expected"
```

**Acceptance criteria:** Each touched validator function has at least one positive and one negative pure test where gap existed; test catalog posted in chat.

**Risks/edge cases:** Do not duplicate Slice 1 invariant graph tests or Slice 3 service generation flows.

---

### Slice 3: Repetition tests (post Test catalog in chat)

**Objective:** Service-level repetition hardening with mandatory §0.1 template semantics.

**Files expected to change:**
- [`tests/services/test_repetition_service.py`](../../tests/services/test_repetition_service.py)
- [`tests/services/test_goal_service.py`](../../tests/services/test_goal_service.py) — touch only for template-goal chaining / create paths
- [`tests/support/repetition_fixtures.py`](../../tests/support/repetition_fixtures.py) (new, optional) — extract shared bootstrap if duplication exceeds ~30 lines in this slice

**Implementation steps:**
1. **Template-goal chaining:** child under template goal via `GoalService.create_child`; generate; refresh materializes on `LINKED` instances (extend existing patterns).
2. **Refresh vs DETACHED:** task detach on one instance; template duration edit; assert `LINKED` updates, `DETACHED` unchanged (service-level, not only orchestration).
3. Add service tests for `update_settings` rejections (mode lock, count decrease, end-time shorten) — domain covered in `test_repetitions.py`, service layer thin.
4. Add nested repetition-as-template refresh/materialize case if gap remains after audit.
5. Post **Test catalog**.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/services/test_repetition_service.py tests/services/test_goal_service.py -m "not slow and not failure_expected"
```

**Acceptance criteria:** All three §0.1 repetition scenarios have explicit service tests; settings-update failures asserted at service API; test catalog posted in chat.

**Risks/edge cases:** Prefer public service APIs over manual ORM seeding except for corruption cases (Slice 1).

---

### Slice 4: Resolution tests (post Test catalog in chat)

**Objective:** Close `TaskResolutionService` and pure resolution gaps: instance-clone traversal, precedence, constraint intersection, template exclusion.

**Files expected to change:**
- [`tests/domain/test_resolution.py`](../../tests/domain/test_resolution.py)
- [`tests/services/test_task_resolution_service.py`](../../tests/services/test_task_resolution_service.py)

**Implementation steps:**
1. Service test: completed predecessor skipped in precedence edges (pure may exist; confirm service path).
2. Service test: `invalid_incomplete` beyond duration tampering (malformed constraint source, orphan under clone subtree).
3. Service test: effective windows with repetition `SYSTEM_REPETITION_WINDOW` + user groups + master horizon (AND-of-OR along ancestor path).
4. Confirm template subtree excluded post-generation; `DETACHED` clone tasks still resolved (extend if only partially covered).
5. Critical-first instance ordering with competing `sort_order` buckets.
6. Post **Test catalog**.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/domain/test_resolution.py tests/services/test_task_resolution_service.py -m "not slow and not failure_expected"
```

**Acceptance criteria:** Resolution buckets exercised at service layer; template/instance clone semantics explicit; test catalog posted in chat.

**Risks/edge cases:** Empty effective windows remain valid on otherwise valid tasks (do not assert invalidity).

---

### Slice 5: Assignment tests (post Test catalog in chat)

**Objective:** Harden assignment across domain, service, scheduling solvers, and conflict analysis—including cross-layer cases called out in Prompts 13–14.

**Files expected to change:**
- [`tests/domain/test_assignment.py`](../../tests/domain/test_assignment.py)
- [`tests/services/test_task_assignment_service.py`](../../tests/services/test_task_assignment_service.py)
- [`tests/scheduling/test_feasibility.py`](../../tests/scheduling/test_feasibility.py)
- [`tests/scheduling/test_heuristic_solver.py`](../../tests/scheduling/test_heuristic_solver.py)
- [`tests/scheduling/test_exact_cp_sat_hard.py`](../../tests/scheduling/test_exact_cp_sat_hard.py) / [`test_exact_cp_sat_objectives.py`](../../tests/scheduling/test_exact_cp_sat_objectives.py) — only if audit finds gaps
- [`tests/deletion/test_conflict_analysis.py`](../../tests/deletion/test_conflict_analysis.py)

**Implementation steps:**
1. Service: precedence edges from repetition clone chains reflected in calendar placement order.
2. Service: occupied past TASK + tight effective windows → infeasible without calendar mutation.
3. Scheduling: add deterministic cases for constraint-window narrowing and stability-hint tie behavior if untested.
4. Conflict analysis: extend beyond mocked solver—optional thin DB seed + stub solver returning infeasible with known task subset.
5. Assert instance-clone tasks only in persisted calendar rows (§0.1); template plan ids never appear.
6. Post **Test catalog**.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/domain/test_assignment.py tests/services/test_task_assignment_service.py tests/scheduling/ tests/deletion/test_conflict_analysis.py -m "not slow and not failure_expected"
```

**Acceptance criteria:** Success/failure/no-calendar-replacement invariants hold; solver + service layers each gain at least one new meaningful case; test catalog posted in chat.

**Risks/edge cases:** Keep OR-Tools tests small/deterministic; mark `slow` if solver time is borderline.

---

### Slice 6: Free-time tests (post Test catalog in chat)

**Objective:** Harden logical completeness, prerequisite blocking, renormalization, and assignment isolation.

**Files expected to change:**
- [`tests/domain/test_free_time.py`](../../tests/domain/test_free_time.py)
- [`tests/services/test_free_time_activity_service.py`](../../tests/services/test_free_time_activity_service.py)
- [`tests/services/test_free_time_assignment_service.py`](../../tests/services/test_free_time_assignment_service.py)

**Implementation steps:**
1. Domain + service: template subtree incomplete for logical completeness (may extend existing `test_is_plan_logically_complete_template_subtree_is_incomplete`).
2. Service: two enabled activities with partial blocker → proportional split across gap.
3. Service: prerequisite becomes complete after task completion → activity unblocks on next assign.
4. Service: second `assign_free_time` replaces future FREE_TIME only; past preserved; TASK rows untouched.
5. Minimum-block tiny gap left unassigned.
6. Post **Test catalog**.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/domain/test_free_time.py tests/services/test_free_time_activity_service.py tests/services/test_free_time_assignment_service.py -m "not slow and not failure_expected"
```

**Acceptance criteria:** Partial failure semantics unchanged; tests assert meaningful guarantees not incidental ORM counts; test catalog posted in chat.

**Risks/edge cases:** Free-time assignment runs after task assignment—seed TASK blockers explicitly.

---

### Slice 7: Deletion tests (post Test catalog in chat)

**Objective:** Close deletion preview/suggest/execute gaps with §0.1 template-root shell expansion.

**Files expected to change:**
- [`tests/domain/test_deletion_impact.py`](../../tests/domain/test_deletion_impact.py)
- [`tests/domain/test_deletion_conflict.py`](../../tests/domain/test_deletion_conflict.py)
- [`tests/deletion/test_preview_service.py`](../../tests/deletion/test_preview_service.py)
- [`tests/deletion/test_conflict_suggestions.py`](../../tests/deletion/test_conflict_suggestions.py)
- [`tests/services/test_plan_tree_service.py`](../../tests/services/test_plan_tree_service.py)

**Implementation steps:**
1. **Template-root delete includes shell:** execute parity for **task** template root (goal path exists; add `test_delete_plan_parity` for task template).
2. Preview/delete parity with calendar entries on instance clones.
3. `DETACHED` clone delete: impact set stays local (no sibling `LINKED` expansion).
4. Suggestions: deterministic ranking with repetition/template-shaped conflict candidates.
5. Post **Test catalog**.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/domain/test_deletion_impact.py tests/domain/test_deletion_conflict.py tests/deletion/ tests/services/test_plan_tree_service.py -m "not slow and not failure_expected"
```

**Acceptance criteria:** Preview IDs == delete IDs for new scenarios; template-root shell rule covered for both goal and task template roots; test catalog posted in chat.

**Risks/edge cases:** Master plan deletion remains forbidden—assert unchanged.

---

### Slice 8: Integration tests (post Test catalog in chat)

**Objective:** End-to-end `refresh_schedule` hardening across resolution + assignment + free-time + repetition refresh side effects.

**Files expected to change:**
- [`tests/orchestration/test_refresh_schedule_integration.py`](../../tests/orchestration/test_refresh_schedule_integration.py)
- [`tests/orchestration/test_refresh_schedule_state.py`](../../tests/orchestration/test_refresh_schedule_state.py)
- [`tests/orchestration/orch_helpers.py`](../../tests/orchestration/orch_helpers.py) — extend helpers inline if needed

**Implementation steps:**
1. Re-run §0.1 E2E trio at orchestration level (extend existing tests if thin).
2. Heuristic-enabled refresh path (`heuristic_enabled: True` in settings) with deterministic small horizon.
3. Partial free-time failure + multi-activity scenario.
4. Invalid incomplete blocks before assignment (calendar unchanged)—confirm stage ordering.
5. Multi-repetition refresh before resolve affects resolved task set.
6. Mark `@pytest.mark.slow` only when necessary; document in Test catalog.
7. Post **Test catalog**.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/orchestration/ -m "not slow and not failure_expected"
```

**Acceptance criteria:** Happy path + primary failure paths covered; repetition refresh ordering verified; default CI suite green; test catalog posted in chat.

**Risks/edge cases:** Stub vs real solver tradeoff—prefer real heuristic/exact on tiny fixtures; avoid flaky OR-Tools timeouts.

---

## Abstraction check

| Item | Verdict |
|------|---------|
| [`tests/support/repetition_fixtures.py`](../../tests/support/repetition_fixtures.py) | Allowed if duplication warrants it in Slice 3+; functions only, no framework |
| New test base classes / registries | **No** — prefer fixtures and module-private helpers |
| Production abstractions | **No** unless bug fix |

## Dependency changes

None.

## Open questions

None — blocking questions resolved in request-questions.

## Changed in this revision

- Finalized draft from `~/.cursor/plans/` into [`docs/plans/test_hardening.md`](test_hardening.md).
- Normalized all file links to `../../` paths from `docs/plans/` (matches sibling finalized plans).
- Added **Already done (dependencies)**, **Build workflow**, and per-slice **Tests/checks** bash blocks.
- Recorded request-questions outcomes: inline fixture extraction (no Slice 0), Slice 1 `failure_expected` cleanup (no migration), Slice 5 includes scheduling tests.
- Documented `failure_expected` audit findings: 5 stale markers to remove; 1 obsolete pre-generation invariant test to delete or rewrite per Prompt 10 relaxation.
