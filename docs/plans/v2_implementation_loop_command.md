# Plan: V2 implementation loop command

**Finalized plan location:** [`docs/plans/v2_implementation_loop_command.md`](v2_implementation_loop_command.md)

## Context

Build a **single-PR, multi-plan** Cursor workflow for all V2 phases in [`docs/v2_cursor_implementation_guide.md`](../v2_cursor_implementation_guide.md). One command (`.cursor/commands/run-v2-implementation.md`) drives a **state machine** that:

- Runs **`/request-questions` once per V2 phase plan** (before `/draft-plan`), then **all build slices in Agent mode** without another `/request-questions` pass
- Per phase: Plan mode block → `/request-questions` → `/draft-plan` → finalize in `docs/plans/` → commit plan → Agent mode slice loop until the next phase plan
- Uses **non-interactive commits for every commit**; you open the PR manually after all commits
- Collapses Alembic five-step groups: no commit on preview / manual-edit middle steps; `db-revision-continue` owns its commit (no `/request-questions` on any build slice)
- Uses **`--skip-tests` until the last slice of the entire PR**, then runs full checks once
- Persists progress in a **git-tracked** [`.cursor/v2_implementation_loop.json`](../../.cursor/v2_implementation_loop.json) with `next_required_mode: plan|agent`
- **Pauses** when the current Cursor mode cannot run the next step; you switch mode and re-invoke the command (Plan mode mainly at **new phase plan** boundaries; rare escape hatches from subagent audit only)

This plan covers **audit → design → command build** only. It does **not** implement V2 domain features (blocks, prerequisites, etc.).

**Authority:** [`.cursor/repo_conventions.md`](../../.cursor/repo_conventions.md) §20; [`docs/v2_cursor_implementation_guide.md`](../v2_cursor_implementation_guide.md).

```mermaid
flowchart TD
    subgraph metaPlan [This plan: manual build of loop command]
        A1[A1 Subagent audits]
        Synth[Slice B Audit Report]
        Spec[Slice C Loop Design Spec]
        Build[Slice D-E Build command]
        A1 --> Synth --> Spec --> Build
    end
    subgraph runtimeLoop [run-v2-implementation runtime per V2 phase]
        planBlock[Plan mode: request-questions draft-plan commit plan]
        agentSlices[Agent mode: all build slices and commits]
        nextPhase[Next phase plan]
        planBlock --> agentSlices --> nextPhase
        nextPhase --> planBlock
    end
```

## Non-goals

- Implementing V2 feature code (flat goal children, blocks, prerequisites, etc.)
- Opening or merging a GitHub PR automatically
- Replacing individual slash commands (`/build-plan-slice`, `/db-revision-preview`, etc.) — the loop **invokes** them
- Running subagents inside `/run-v2-implementation` (subagents run only during Slice A)
- Interactive `git add -p` in the loop (all commits non-interactive)
- MCPs, new skills, or custom subagent types

## Locked assumptions

| Topic | Decision |
|---|---|
| Scope | All V2 guide phases 0–7 (each phase → finalized plan in `docs/plans/`) |
| Loop start | Full pipeline including draft + finalize plan docs per phase |
| Commits | Fully non-interactive for every commit |
| State file | Git-tracked `.cursor/v2_implementation_loop.json` |
| Mode handoff | State records `next_required_mode` + `next_step`; command exits with resume instruction |
| PR | Single branch; many atomic commits; manual PR at end |
| skip-tests | Entire multi-plan PR until final slice; then full pytest (audit may confirm ruff/pyright scope) |
| Phase 0 | Skip when V2 docs + authority files already exist (Slice D initializes state accordingly) |
| AskQuestion | Plan mode only — loop must not invoke in agent steps |
| Slice clarification | **No `/request-questions` before build slices** — only before drafting each phase plan |
| Plan mode frequency | Plan mode at **phase plan bootstrap**; Agent mode for all slices within a plan; **rare** return to Plan mode only for step IDs listed in `plan_mode_escape_hatches` (populated by Slice A audit; empty if none found) |
| Slice ambiguity | `/build-plan-slice` blocking questions use **Agent-mode chat**, not `/request-questions` |

Build workflow: use `/build-plan-slice` per slice **of this plan**; stop after each slice for approval.

### Slice-type taxonomy (baseline — Slice A must confirm)

| Slice kind | Plan mode | Agent mode | Commit? |
|---|---|---|---|
| Phase plan bootstrap | request-questions, draft-plan, finalize | — | Yes (plan doc) |
| Ordinary build slice | — | build-plan-slice + reviews | Yes |
| pre-alembic | — | build-plan-slice (ORM only) | Yes |
| alembic-preview | — | db-revision-preview | No |
| migration-script-edits | — | Human edit or `/small-change` | No |
| alembic-continue | — | db-revision-continue | Yes (internal) |
| post-alembic | — | build-plan-slice | Yes |
| plan_mode_escape_hatch | per Slice A catalog (rare) | — | varies |

## Slices

### Slice A: Subagent audit battery

**Objective:** Run four read-only subagent audits in parallel; collect structured findings for the main-agent Audit Report (Slice B). Do not edit files.

**Files expected to change:** None (read-only).

**Implementation steps:**

1. Launch **A1** (`explore`, very thorough): [`.cursor/commands/`](../../.cursor/commands/), [`scripts/cursor/commit_changes.py`](../../scripts/cursor/commit_changes.py), [`scripts/cursor/checks.sh`](../../scripts/cursor/checks.sh), [`.cursor/rules/30-planning-slices.mdc`](../../.cursor/rules/30-planning-slices.mdc), [`.cursor/rules/05-command-invocation-hygiene.mdc`](../../.cursor/rules/05-command-invocation-hygiene.mdc).
   - Return: command → mode → mutates? → tests? → commit? table; loop blockers; non-interactive commit extension points; **`plan_mode_escape_hatches`** — any command or stop that requires Plan mode beyond phase plan bootstrap (may be empty).

2. Launch **A2** (`explore`, very thorough): [`docs/plans/*.md`](../../docs/plans/), [`docs/v2_cursor_implementation_guide.md`](../v2_cursor_implementation_guide.md) §4, §6, §9.
   - Return: slice naming patterns; per V2 phase expected plan filename and migration groups; `/build-plan-slice` deviations.

3. Launch **A3** (`explore`, medium): [`calendar_backend/db/migrations/`](../../calendar_backend/db/migrations/), `failure_expected` in `tests/`, [`calendar_backend/models/chains.py`](../../calendar_backend/models/chains.py).
   - Return: migration head; failure_expected inventory; Phase 1 migration risks.

4. Launch **A4** (`generalPurpose`): agent transcript `8dc9c240-72b2-41c9-81aa-0f4929e5626e` — search commit-changes, skip-tests, db-revision, build-plan-slice, mode switches, user stops.
   - Return: friction points; override patterns; idempotency recommendations.

**Tests/checks:** None (read-only).

**Acceptance criteria:**
- All four subagent reports captured in chat or attached notes
- Known special cases from this plan §Locked assumptions baseline table are explicitly confirmed or contradicted
- **plan_mode_escape_hatches** list included in A1 output (explicitly empty if none)

**Risks/edge cases:**
- Transcript may be large — scope searches to workflow keywords
- Subagent findings may conflict — Slice B resolves

---

### Slice B: Main-agent audit synthesis

**Objective:** Consolidate A1–A4 into a single **Audit Report** in chat; stop for user review before Slice C.

**Files expected to change:** None.

**Implementation steps:**

1. Merge subagent outputs into:
   - **Special-case catalog** (numbered; severity: blocker / adapt / ignore)
   - **Proposed state machine** (step → mode → command → commit? → tests?)
   - **plan_mode_escape_hatches** (from A1; step IDs requiring rare Plan-mode return)
   - **Alembic group template** (collapsed vs manual loop)
   - **Non-interactive commit spec** (staging rubric vs [`commit splitting rubric`](../cursor_implementation_guide.md))
   - **Open design forks** for Slice C (optional `/request-questions` — manual meta-plan step only)

2. Post report; do not implement command yet.

**Tests/checks:** None.

**Acceptance criteria:**
- Audit Report posted with all sections above
- State machine shows **Agent-only** stretches between phase plan bootstraps unless an escape hatch applies

**Risks/edge cases:**
- Do not silently drop contradictions between A1 and A2

---

### Slice C: Loop design spec

**Objective:** Incorporate Audit Report findings and any user clarifications into a **Loop Design Spec** (chat addendum or short markdown in plan notes). No command file yet.

**Files expected to change:** None (optional: append **Loop Design Spec** section to this plan file if user approves).

**Implementation steps:**

1. Resolve open forks from Audit Report (user may use `/request-questions` — **manual meta-plan step**; not part of `/run-v2-implementation` runtime).
2. Record resolved forks, e.g.:
   - Non-interactive staging rubric (one commit per slice vs split)
   - Whether `/revise-plan` is a loop step or manual-only escape hatch
   - Phase 0 skip predicate
   - Final check suite after skip-tests period (pytest only vs full checks)
   - **`plan_mode_escape_hatches`** finalized for state file
3. Publish Loop Design Spec; unblock Slice D.

**Tests/checks:** None.

**Acceptance criteria:**
- No unresolved **blocker**-severity forks remain for command implementation
- Loop Design Spec references Audit Report catalog IDs where applicable
- Spec states **`/request-questions` only at phase plan bootstrap** in runtime loop

**Risks/edge cases:**
- If blockers remain, stop and re-ask — do not start Slice D

---

### Slice D: Build loop infrastructure

**Objective:** Implement the loop command, git-tracked state file, non-interactive commit support, and V2 guide cross-reference.

**Files expected to change:**
- [`.cursor/commands/run-v2-implementation.md`](../../.cursor/commands/run-v2-implementation.md) (new)
- [`.cursor/v2_implementation_loop.json`](../../.cursor/v2_implementation_loop.json) (new, git-tracked)
- [`scripts/cursor/commit_changes.py`](../../scripts/cursor/commit_changes.py) — add `--non-interactive`, `--message`, deterministic staging
- [`docs/v2_cursor_implementation_guide.md`](../v2_cursor_implementation_guide.md) — §2 single-PR loop + mode handoff

**May also change:**
- [`scripts/cursor/v2_loop_state.py`](../../scripts/cursor/v2_loop_state.py) (new, optional) — validate/advance state JSON
- [`docs/cursor_implementation_guide.md`](../cursor_implementation_guide.md) — one-line pointer to loop command (optional)

**Implementation steps:**

1. Implement state schema per Loop Design Spec (draft fields):
   - `version`, `branch`, `next_required_mode`, `next_step`, `current_phase`, `current_plan_path`, `current_slice_id`, `skip_tests_until_final`, `completed_steps`, `alembic_group`, `plan_mode_escape_hatches`
2. Write `run-v2-implementation.md` with labeled parameters: `Resume`, `Reset`, optional `Phase` (if spec allows).
3. Document step handlers: mode check → run one step → advance state → exit or notify done.
   - **Default:** `next_required_mode: agent` for all build/db-revision/commit steps within a phase plan.
   - **Plan mode steps:** `phase_N_request_questions`, `phase_N_draft_plan`, `phase_N_finalize_plan` (names illustrative), plus any escape-hatch step IDs.
4. Extend `commit_changes.py`:
   - `--non-interactive`: stage all changed files matching rubric OR explicit pathspec list; no `input()` prompts
   - `--message` for commit message; honor `--skip-tests` / `--skip-checks` as today
5. Initialize state: Phase 0 complete if V2 docs exist; else first step `phase_0_verify`.
6. Map V2 phases 1–7 to plan prompt names from guide §9 (filenames finalized when each phase plan is drafted).
7. Update V2 guide §2 with loop usage, **request-questions only before each phase plan**, and Plan/Agent resume instructions.

**Tests/checks:**
```bash
uv run ruff format scripts/cursor/commit_changes.py
uv run ruff check scripts/cursor/commit_changes.py
uv run pyright scripts/cursor/commit_changes.py
# Manual: python scripts/cursor/commit_changes.py --help shows --non-interactive
```

**Acceptance criteria:**
- Command doc covers every step, mode gate, pause/resume, and completion notification
- **No `/request-questions` on build slices** — only at phase plan bootstrap (+ documented escape hatches)
- Alembic groups: no double-commit; no request-questions on preview/manual-edit
- Long **Agent-only** stretches between phase plan bootstraps in documented state machine
- `skip_tests_until_final` honored until last PR slice
- State file is valid JSON and git-tracked (not gitignored)
- Non-interactive commit path works on a trivial doc-only change (smoke test in Slice E or manual note)

**Risks/edge cases:**
- `db-revision-continue` already calls commit — loop must set `commit: false` for redundant commit steps
- Parameter hygiene: loop command must not infer trailing untrusted text ([rule 05](../rules/05-command-invocation-hygiene.mdc))

---

### Slice E: Dry-run validation

**Objective:** Walk state transitions for **V2 Phase 1** (flat goal children) without executing real domain implementation; fix command doc ambiguities.

**Files expected to change:**
- [`.cursor/commands/run-v2-implementation.md`](../../.cursor/commands/run-v2-implementation.md) — clarifications from dry-run
- [`.cursor/v2_implementation_loop.json`](../../.cursor/v2_implementation_loop.json) — example snapshot for Phase 1 start (optional)

**Implementation steps:**

1. Simulate state progression: **Plan mode** at `phase_1_plan_bootstrap` only → **Agent mode** through pre-alembic → preview → manual-edit pause → continue → post-alembic → **Plan mode** at `phase_2_plan_bootstrap`.
2. Verify each transition sets correct `next_required_mode` and `next_step` (Agent for all build slices).
3. Verify mode-mismatch exit message is actionable.
4. Document manual gates (migration-script-edits approval) in command doc.
5. Verify slice ambiguity is documented as Agent-mode chat, not `/request-questions`.
6. Post **Dry-run report** in chat: transitions tested, issues fixed, ready to run loop on real V2 work.

**Tests/checks:**
```bash
# If v2_loop_state.py exists:
uv run python scripts/cursor/v2_loop_state.py validate
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- Dry-run report posted
- No ambiguous `next_step` IDs for Phase 1 migration group
- Command doc lists explicit user actions at manual gates
- Dry-run confirms **one Plan-mode block per phase plan**, not per slice

**Risks/edge cases:**
- Dry-run does not invoke real `/build-plan-slice` on Phase 1 code

---

## Target command surface (reference for Slice D)

**Command:** `/run-v2-implementation`

**Parameters:**
- `Resume: true|false` (default true)
- `Reset: true|false` (reinitialize from guide phase list)
- `Phase: 0-7` (optional — implement only if Loop Design Spec approves)

---

## Runtime loop pattern (reference for Slice D)

Per V2 phase (after loop command exists):

```text
[Plan mode] /request-questions → /draft-plan → finalize plan → commit plan doc
[Agent mode] for each slice in plan:
  /build-plan-slice (or db-revision-preview / manual edit / db-revision-continue)
  → reviews → non-interactive commit
[Plan mode] only when starting next phase plan (or rare escape hatch)
```

**Completion:** When `next_step` is `done`, notify in chat: total commits, final check run, manual PR checklist. Do not run `gh pr create`.

## Abstraction check

| Addition | Justification |
|---|---|
| `.cursor/v2_implementation_loop.json` | Cross-mode resume requires persisted cursor; git-tracked for single-PR continuity |
| `scripts/cursor/v2_loop_state.py` (optional) | Deterministic validate/advance; avoids agent drift on JSON edits — include only if command doc stays readable without it |
| `--non-interactive` on `commit_changes.py` | Loop requirement; extends existing script rather than parallel commit path |

No new subagent types, registries, or MCPs.

## Dependency changes

None.

## Open questions

Resolve in Slice C from Audit Report (optional `/request-questions` — manual meta-plan step only):

- Exact non-interactive staging rubric (one commit per slice vs atomic splits within slice)
- Whether `/revise-plan` is a manual-only escape hatch or a documented `plan_mode_escape_hatch` step
- Phase 0 skip predicate (file existence vs explicit state flag)
- Final check suite after skip-tests period (pytest only vs ruff + pyright + pytest)

Do not start Slice D until Slice C closes blocker-severity forks.

---

## Changed in this revision

- **`/request-questions` only at phase plan bootstrap** — removed from ordinary, pre-alembic, and Alembic middle slices in the slice-type taxonomy.
- **Plan mode frequency:** Plan mode when starting each V2 phase plan; Agent mode for all build slices within a plan; rare Plan-mode return only via `plan_mode_escape_hatches` (from Slice A audit).
- **Slice ambiguity:** documented as Agent-mode chat, not `/request-questions`.
- **Locked assumptions:** added rows for slice clarification, plan mode frequency, and slice ambiguity handling.
- **Slice A1 / B:** A1 catalogs `plan_mode_escape_hatches`; Audit Report includes them in the state machine.
- **Slice C:** `/request-questions` labeled manual meta-plan step, not runtime loop behavior.
- **Slice D / E:** state schema, acceptance criteria, dry-run, and **Runtime loop pattern** updated for Agent-only stretches between phase plans.
- **Diagram:** replaced meta-plan vs runtime-loop mermaid to separate building the command from running it.
