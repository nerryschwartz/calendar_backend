# calendar_backend V2 Cursor Implementation Guide

Recommended location: `docs/v2_cursor_implementation_guide.md`

This guide turns [`docs/v2_engineering_design.md`](v2_engineering_design.md) into a Cursor-ready workflow for **evolving the existing repository in place**. It supersedes [`docs/cursor_implementation_guide.md`](cursor_implementation_guide.md) wherever they conflict.

**Precedence (highest first):**

1. [`.cursor/repo_conventions.md`](../.cursor/repo_conventions.md)
2. This guide and finalized plans in [`docs/plans/`](plans/)
3. [`docs/v2_engineering_design.md`](v2_engineering_design.md) for architecture when not covered above
4. Archived V1 guide and V1 engineering design PDF

---

## 0. Workflow changes from V1

Read this section first if you have used the V1 implementation guide.

### 0.1 Evolve in place (not greenfield)

V1 assumed a brand-new empty repository. **V2 work modifies the existing `calendar_backend` codebase** with breaking migrations and refactors. Do not re-scaffold the repo; extend and replace V1 modules per approved plans.

### 0.2 Alembic slices split into five steps

V1 sometimes bundled ORM changes, migration autogenerate, manual migration edit, `upgrade head`, and follow-up tests in one plan slice. **V2 requires splitting any schema-changing slice into five separate slices:**

| Step | Slice name | Command | Who implements |
|---|---|---|---|
| 1 | **pre-alembic** | `/build-plan-slice` | ORM/domain/service code **without** autogenerate or `upgrade head`; schema tests marked `failure_expected` per [repo convention §13](../.cursor/repo_conventions.md) |
| 2 | **alembic-preview** | `/db-revision-preview` | Autogenerate only; **review-only** report; **no** `/build-plan-slice` |
| 3 | **migration-script-edits** | Manual (you) + `/small-change` for fixes | Edit generated revision per preview report |
| 4 | **alembic-continue** | `/db-revision-continue` | `upgrade head`, unmark `failure_expected`, commit migration |
| 5 | **post-alembic** | `/build-plan-slice` or `/small-change` | Service wiring, integration tests, orchestration that require applied schema |

**Rules:**

- `/build-plan-slice` **must not** autogenerate or run `upgrade head` when the slice text references db-revision commands (see [`.cursor/commands/build-plan-slice.md`](../.cursor/commands/build-plan-slice.md) migration gate).
- Steps 2–4 follow [`.cursor/commands/db-revision-preview.md`](../.cursor/commands/db-revision-preview.md) and [`.cursor/commands/db-revision-continue.md`](../.cursor/commands/db-revision-continue.md).
- Name slices explicitly in plans, e.g. `Slice 3a-pre-alembic`, `Slice 3b-alembic-preview`, etc.

### 0.3 Goal child chains removed

All V1 references to `GoalChildChain`, `GoalService._attach_to_goal_chain`, and `collect_precedence_constraints` chain walking are **obsolete** in V2 plans. Replace with:

- Direct goal child fields on `Plan`
- Plan prerequisites and immediate prerequisites
- Updated traversal in `domain/resolution.py` and `domain/plan_traversal.py`

### 0.4 Two-phase scheduling

V2 adds block resolution/assignment before task assignment. Orchestration slices must follow the pipeline in [V2 design §10](v2_engineering_design.md#10-refresh_schedule-pipeline).

### 0.5 Authority updates

- [`.cursor/rules/00-project-source-of-truth.mdc`](../.cursor/rules/00-project-source-of-truth.mdc) points at this guide and the V2 design doc.
- [repo convention §20](../.cursor/repo_conventions.md) records V2 supersessions.

### 0.6 What to reuse from the V1 guide

These V1 sections remain valid unless this document or the V2 design says otherwise:

- Tooling: `uv`, ruff, pyright, pytest, WSL
- Layer boundaries (`.cursor/rules/10-layer-boundaries.mdc`)
- Abstraction discipline, testing expectations, planning slice rules
- Cursor commands: `/request-questions`, `/draft-plan`, `/revise-plan`, `/build-plan-slice`, `/small-change`, `/commit-changes`, `/review-validation`, `/review-consistency`, `/review-abstractions`
- Scripts under `scripts/cursor/`
- Alembic tutorial (§8 below) — **except** use the five-step slice split (§0.2)
- Test-creation slice convention (§7 below)
- Repo conventions §1–§19

---

## 1. Locked workflow decisions

- Work in WSL on the **existing** repository.
- Use `uv` for dependencies; install with `uv add` in the slice that needs them.
- Default checks: `uv run ruff format .`, `uv run ruff check .`, `uv run pyright`, `uv run pytest -m "not slow and not failure_expected"`.
- Store draft plans in `~/.cursor/plans/`; finalized plans in `docs/plans/`.
- Stop and review after each implementation slice (not after each file edit).
- Sequential solo branches; no merge-command tooling.
- Defer OR-Tools until the V2 exact block/task solver slice(s).
- Favor deterministic scripts over agentic repo operations.

---

## 2. How to use Cursor for V2

```text
1. Open Cursor in the repo; start a branch for the V2 phase.
2. /request-questions in Plan Mode against docs/v2_engineering_design.md.
3. /draft-plan → finalize in docs/plans/<phase>.md with five-step migration slices where needed.
4. /build-plan-slice for one slice only.
5. For schema slices: pre-alembic → /db-revision-preview → manual edit → /db-revision-continue → post-alembic.
6. /review-validation and /review-consistency on slice diffs.
7. /commit-changes when ready.
8. Repeat until the phase plan is complete.
```

Use expensive models for ambiguous planning; use cheaper models for locked slices.

---

## 3. Repository conventions and V2 supersessions

**Highest precedence:** [`.cursor/repo_conventions.md`](../.cursor/repo_conventions.md) including **§20 V2 design supersessions**.

When a repo convention conflicts with this guide or the V2 design, **follow the convention** and update docs via `/add-repo-convention`.

### 3.1 V2-specific supersessions (summary)

| Topic | V1 guidance | V2 |
|---|---|---|
| Design source | V1 PDF + V1 guide | [`v2_engineering_design.md`](v2_engineering_design.md) |
| Goal child layout | Goal child chains | Direct child `goal_is_critical` / `goal_sort_order` on `Plan` |
| Precedence | Chain order edges | Plan prerequisites + immediate prerequisites |
| Calendar entries | Tasks only | Tasks on `CalendarEntry`; blocks on `BlockCalendarEntry` |
| Migration in one slice | Allowed in older plans | Five-step split (§0.2) |
| Plan service ownership | GoalService chain attach | GoalService direct child ordering (update §14 when implemented) |

### 3.2 Template semantics (V2)

Same as V1 guide §0.1 except:

- Master and template goal children use **direct ordering fields**, not chains.
- `RepetitionService` clone sync copies **goal child order**, not chain rows.
- Template-root delete still includes repetition shell.

### 3.3 ORM and slice consistency

Same spirit as V1 guide §0.2: wire symmetric relationships when target models exist; do not defer wiring only because a later slice number names a different file.

---

## 4. Five-step migration template (copy into plans)

Use this block in any finalized plan slice that changes schema:

```markdown
### Slice N-pre-alembic: <title>
- ORM / domain changes only
- No autogenerate; no upgrade head
- Schema tests with @pytest.mark.failure_expected where INSERT/CHECK requires migration

### Slice N-alembic-preview
- Command: /db-revision-preview
- Message: <alembic message>
- /build-plan-slice MUST NOT run this step

### Slice N-migration-script-edits
- Manual edit of calendar_backend/db/migrations/versions/<rev>_*.py
- User approval before continue

### Slice N-alembic-continue
- Command: /db-revision-continue

### Slice N-post-alembic
- Service wiring, integration tests, remove failure_expected markers if any remain
```

---

## 5. Alembic tutorial (V2)

Follow V1 guide §8 ([`cursor_implementation_guide.md`](cursor_implementation_guide.md) lines ~1102–1323) for SQLite, `env.py`, batch mode, and [repo convention §4](../.cursor/repo_conventions.md).

**V2 difference:** never combine steps 1–5 of §0.2 in a single `/build-plan-slice` invocation.

### 5.1 Common mistakes (V2 additions)

| Mistake | Fix |
|---|---|
| Running autogenerate inside `/build-plan-slice` | Use `/db-revision-preview` slice instead |
| Post-alembic service code in pre-alembic slice | Split slice; keep pre-alembic ORM-only |
| Forgetting eager prerequisite rewrite in `generate_instances` | post-alembic repetition slice must include pass-2 wiring |

---

## 6. V2 implementation phases (recommended plan sequence)

Each phase becomes a finalized plan in `docs/plans/`. Slices within a phase use the five-step migration pattern when schema changes.

### Phase 0: Documentation and authority (complete when these files exist)

- [`docs/v2_engineering_design.md`](v2_engineering_design.md)
- This guide
- Updates to `.cursor/rules/00-project-source-of-truth.mdc` and repo convention §20

### Phase 1: Flat goal children (remove chains)

**Objective:** Replace goal child chains with direct child ordering; migrate data; update traversal, `GoalService`, `RepetitionService` clone sync, invariants, deletion, tests.

**Key modules:** `models/plans.py`, `services/goal.py`, `services/repetition.py`, `domain/plan_traversal.py`, `domain/resolution.py`, drop `models/chains.py`.

### Phase 2: Plan and immediate prerequisites

**Objective:** `plan_prerequisite` table, immediate prereq FKs on task/block, template trace validation in domain, eager rewrite in `generate_instances`, replace `collect_precedence_constraints`.

**Key modules:** `domain/` trace + validation, `services/plan_tree.py`, `services/repetition.py`, `services/task_resolution.py`.

### Phase 3: Block ORM and block calendar

**Objective:** `PlanKind.BLOCK`, `BlockPlan`, `BlockCalendarEntry`, block services (CRUD parity with tasks where applicable).

### Phase 4: Block resolution and phase-1 assignment

**Objective:** `ResolvedBlock`, block solver input, exact/heuristic assignment analogous to tasks, lex objective for downstream task feasibility.

**Key modules:** `scheduling/`, new `services/block_resolution.py`, `services/block_assignment.py`.

### Phase 5: Task family narrowing and phase-2 assignment

**Objective:** `allowed_block_families` on tasks, effective window narrowing, occupied intervals from block calendar, update `TaskAssignmentService`.

### Phase 6: Free-time family semantics

**Objective:** Normalize activity family lists, integrate `"free-time"` blocks with activity assignment, keep `prerequisite_plan_ids` as full blockers.

### Phase 7: Orchestration, deletion, CLI, integration

**Objective:** `refresh_schedule` pipeline, conflict/deletion updates, dev CLI coverage, end-to-end tests.

---

## 7. Test-creation slice convention

Unchanged from V1: test-creation slices must post a **Test catalog** in the chat report (every test function, one line per behavior). Cover **all behavior introduced in the chunk**, not only plan examples.

Schema tests before migration: `@pytest.mark.failure_expected`; remove in `/db-revision-continue` slice.

---

## 8. Cursor rules and commands

Use existing `.cursor/rules/` and `.cursor/commands/` unless a V2 phase plan calls for new conventions.

**Migration gate:** `.cursor/rules/30-planning-slices.mdc` and `build-plan-slice.md` already enforce db-revision ownership; V2 plans must name five-step slices explicitly.

---

## 9. V2 planning prompts (starters)

Use `/request-questions` before each draft plan.

### Prompt V2-1: Flat goal children

```text
Create a finalized plan in docs/plans/v2_flat_goal_children.md to remove GoalChildChain
and use direct goal child ordering per docs/v2_engineering_design.md §3.

Include five-step migration slices for schema change.
Split service/test updates across pre-alembic and post-alembic slices.
```

### Prompt V2-2: Prerequisites and template trace

```text
Create docs/plans/v2_prerequisites.md for plan-level and immediate prerequisites,
template trace validation, and eager clone wiring per V2 design §5–§6.
```

### Prompt V2-3: Blocks and two-phase scheduling

```text
Create docs/plans/v2_blocks_and_scheduling.md for BlockPlan, BlockCalendarEntry,
phase-1 block assignment, task family narrowing, and refresh_schedule pipeline.
Defer OR-Tools until the exact-solver sub-slice within this phase.
```

### Prompt V2-4: Free-time block families

```text
Create docs/plans/v2_free_time_families.md for activity family normalization
and integration with block calendar per V2 design §7.5 and §9.
```

---

## 10. Checklist before marking a V2 slice done

- [ ] Slice scope matches one approved plan slice (not future phases)
- [ ] Schema slices used five-step migration where required
- [ ] `ruff format`, `ruff check`, `pyright`, pytest (with correct markers)
- [ ] `/review-validation` and `/review-consistency` on diff
- [ ] Test catalog posted for test-creation slices
- [ ] No new abstractions without plan or repo convention justification

---

## 11. Archived V1 reference

[`docs/cursor_implementation_guide.md`](cursor_implementation_guide.md) and the V1 PDF remain for historical context. **Do not follow V1 for goal chains, single-slice migrations, or precedence model.**

Prompts 1–20 in the V1 guide describe the completed V1 build path; V2 phases (§6) supersede Prompts 4–8 and portions of 10–15 for changed behavior.
