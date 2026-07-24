Drive the V2 single-PR implementation loop using git-tracked state in [`.cursor/v2_implementation_loop.json`](../v2_implementation_loop.json).

**Authority:** [Loop Design Spec](../../docs/plans/v2_implementation_loop_command.md#loop-design-spec); [`docs/v2_cursor_implementation_guide.md`](../../docs/v2_cursor_implementation_guide.md) §2.

## Parameter hygiene

- Ignore trailing words attached to the slash command.
- Use only labeled fields in the current user message.
- Required field: none (defaults apply).
- Optional fields:
  - `Resume: true|false` (default `true`)
  - `Reset: true|false` (default `false`)
  - `Phase: 0-7` (optional debug jump — only with `Reset: true`)
- Do not infer parameters from previous invocations.

## Before every invocation

1. Read [`.cursor/v2_implementation_loop.json`](../v2_implementation_loop.json). If missing or `Reset: true`, run:
   ```bash
   uv run python scripts/cursor/v2_loop_state.py init
   ```
   With optional reset jump:
   ```bash
   uv run python scripts/cursor/v2_loop_state.py init --reset --phase 1
   ```
2. Validate state:
   ```bash
   uv run python scripts/cursor/v2_loop_state.py validate
   ```
3. Compare Cursor mode to `next_required_mode`:
   - **`plan`** — Plan mode only.
   - **`agent`** — Agent mode only.
   - On mismatch: **do not** run the step or advance state. Exit with:
     ```text
     Loop paused: next_step=<id> requires <plan|agent> mode.
     Switch mode and re-invoke: /run-v2-implementation with Resume: true
     ```
4. If `Resume: false`, print current state (`v2_loop_state.py show`) and stop without executing.

## One step per invocation (C6)

Execute **exactly one** `next_step`, then update state and exit. Do not chain multiple steps in one turn.

After the step completes successfully:

```bash
uv run python scripts/cursor/v2_loop_state.py complete <step_id>
```

If `skip_if_done` applies (see [Loop Design Spec](../../docs/plans/v2_implementation_loop_command.md#skip-if-done-predicates)), skip work, still run `complete`, and exit.

When `next_step` is `done`, run final checks and post the completion notification (below). Do **not** run `gh pr create`.

---

## Step handlers

### Mode gate only — `phase_N_plan_bootstrap` (Plan)

No command work. Tell the user to stay in Plan mode; the next step is `phase_N_request_questions`.

### `phase_N_request_questions` (Plan)

Run `/request-questions` with `Mode: plan` against [`docs/v2_engineering_design.md`](../../docs/v2_engineering_design.md) and the phase objective from the V2 guide §6.

**Do not** run `/request-questions` on any other loop step.

### `phase_N_draft_plan` (Plan)

Run `/draft-plan` using the phase starter from guide §9 and resolved questions. Target path from `current_plan_path` in state (see [phase → plan paths](../../docs/plans/v2_implementation_loop_command.md#phase--plan-paths)).

### `phase_N_finalize_plan` (Plan)

Finalize the plan in `docs/plans/`, then commit (non-interactive):

1. `/audit-commit-readiness` — stop on blocking findings.
2. `/review-abstractions` on diff — no edits unless auto-fix in this step scope.
3. ```bash
   python scripts/cursor/commit_changes.py --non-interactive --skip-tests --message "Finalize V2 phase N plan: <plan-basename>"
   ```

### `phase_0_verify` (Agent)

Verify Phase 0 authority files exist (Loop Design Spec skip table). If anything is missing, add it in this step. If changes were made, commit:

```bash
python scripts/cursor/commit_changes.py --non-interactive --skip-tests --message "Complete V2 phase 0 documentation and authority"
```

If already complete, skip edits; still `complete phase_0_verify`.

### Build slice steps — `phase_N_slice_<id>_build` (Agent)

1. `/build-plan-slice` with `Slice: <id>` from the active plan (`current_plan_path`).
2. `/review-validation` with `Changes only: true`, `Edit: true`.
3. `/review-consistency` with `Changes only: true`, `Edit: true`.
4. `/audit-commit-readiness` then `/review-abstractions`.
5. ```bash
   python scripts/cursor/commit_changes.py --non-interactive --skip-tests --message "V2 phase N slice <id>: <short objective>"
   ```

**Slice ambiguity:** ask in Agent-mode chat; do **not** use `/request-questions`.

### `phase_N_slice_<id>_pre_alembic` (Agent)

Same as build slice — `/build-plan-slice` for the pre-alembic slice; schema tests may use `failure_expected`. Commit with `--skip-tests`.

Set `alembic_group` via state advance (handled by `v2_loop_state.py complete`).

### `phase_N_slice_<id>_alembic_preview` (Agent)

Run `/db-revision-preview` with the plan’s autogenerate message.

**No commit.** **No** `/request-questions`.

### `phase_N_slice_<id>_migration_manual_edit` (Agent)

**Manual gate:** user edits the generated revision under `calendar_backend/db/migrations/versions/`, or runs `/small-change` for fixes.

**No commit.** Pause until migration file is approved; then `complete` and resume.

### `phase_N_slice_<id>_alembic_continue` (Agent)

Run `/db-revision-continue` apply steps (`alembic upgrade head`, unmark `failure_expected`, pytest).

For the commit at the end of continue, use **non-interactive** staging (SC-7 — no second loop commit):

```bash
python scripts/cursor/commit_changes.py --non-interactive --skip-checks --message "Apply V2 phase N migration: <revision-summary>"
```

Do **not** run a separate loop commit after this step.

### `phase_N_slice_<id>_post_alembic` (Agent)

`/build-plan-slice` for post-alembic slice; reviews; non-interactive commit with `--skip-tests`.

### `phase_N_phase_checks` (Agent)

Run full suite (C4):

```bash
bash scripts/cursor/checks.sh
```

On success, `complete phase_N_phase_checks`. On failure, report and stop without advancing.

### `done` (Agent)

1. Run `bash scripts/cursor/checks.sh` once more.
2. Post completion notification:
   - Branch (`branch` from state)
   - Approximate commit count (`git rev-list --count` vs loop start or note unknown)
   - Phases 0–7 completed
   - Manual PR checklist (push branch, open PR, do not use `gh pr create` from the agent)

---

## Commit policy summary

| Step kind | Commit? | Script flags |
|---|---|---|
| finalize plan | Yes | `--non-interactive --skip-tests --message` |
| ordinary / pre / post alembic build | Yes | `--non-interactive --skip-tests --message` |
| alembic preview | No | — |
| migration manual edit | No | — |
| alembic continue | Yes (once) | `--non-interactive --skip-checks --message` |
| phase checks / done | No | runs `checks.sh` instead |

Before every commit step: `/audit-commit-readiness` then `/review-abstractions` (C8).

---

## Phase → plan paths

| Phase | `current_plan_path` |
|---|---|
| 0 | *(none — verify only)* |
| 1 | `docs/plans/v2_flat_goal_children.md` |
| 2 | `docs/plans/v2_prerequisites.md` |
| 3 | `docs/plans/v2_block_orm.md` |
| 4 | `docs/plans/v2_block_assignment.md` |
| 5 | `docs/plans/v2_task_families.md` |
| 6 | `docs/plans/v2_free_time_families.md` |
| 7 | `docs/plans/v2_orchestration_integration.md` |

Slice order and migration five-step expansion come from the active plan file (`v2_loop_state.py` parses `## Slices` headings).

---

## Runtime `/request-questions` policy

- **Only** at `phase_N_request_questions` in Plan mode.
- **Never** on build slices, Alembic preview/manual-edit/continue, post-alembic, or commit substeps.
- **`plan_mode_escape_hatches`:** always `[]`; mid-phase plan fixes use `/small-change` in Agent mode.

---

## State file

Path: `.cursor/v2_implementation_loop.json` (git-tracked).

Helpers:

```bash
uv run python scripts/cursor/v2_loop_state.py show
uv run python scripts/cursor/v2_loop_state.py validate
uv run python scripts/cursor/v2_loop_state.py complete <step_id>
```

Do not hand-edit JSON unless recovering from an error; prefer `init --reset` for a clean restart.

---

## Non-goals

- Opening or merging GitHub PRs
- Running subagents
- Interactive `git add -p` (use `--non-interactive` commits only)
- Implementing V2 domain features outside the active plan slice
- `/request-questions` before ordinary build slices
