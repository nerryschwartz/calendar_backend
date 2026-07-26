Drive the V2 single-PR implementation loop using git-tracked state in [`.cursor/v2_implementation_loop.json`](../v2_implementation_loop.json).

**Authority:** [Loop Design Spec](../../docs/plans/v2_implementation_loop_command.md#loop-design-spec); [`docs/v2_cursor_implementation_guide.md`](../../docs/v2_cursor_implementation_guide.md) §2.1.

## CRITICAL — mode-batched execution

**Do not exit after one loop step.** After the mode gate passes, run every step in `steps_in_batch` from `batch-steps` in **one invocation** (lifecycle step 7). Only exit when `batch-exit-check` reports `may_exit: true`.

**Mandatory at invocation start (after recording `batch_mode`):**
```bash
uv run python scripts/cursor/v2_loop_state.py batch-steps
```
Keep `steps_in_batch` for the invocation. Do **not** stop until every listed step is `complete`d (or a hard failure).

**Forbidden before `batch-exit-check` says `may_exit: true`:**
- Posting **“Loop batch complete”** when `next_required_mode` still equals `batch_mode`
- Telling the user to **switch mode** when `next_required_mode` equals the current Cursor mode
- Mid-batch slice summaries, progress tables, or **“re-invoke to continue”**
- Any user-facing exit except mode-gate mismatch, `AskQuestion`, or hard failure

**Between loop steps:** continue silently to step 7a — no chat output.

**Mode assignment:** **Plan** = `plan_bootstrap`, `request_questions` only. **Agent** = `draft_plan`, `finalize_plan`, all slice substeps, `phase_checks`, `phase_0_verify`, `done`.

If a turn ends on **`AskQuestion`**, the user answers and re-invokes `/run-v2-implementation` **once in the same mode** to resume the batch (state unchanged).

## User interaction model (locked)

The user may **only**:

1. Switch Cursor **Plan / Agent** mode when the loop requires it
2. Invoke **`/run-v2-implementation`** (no labeled fields in normal use)
3. Answer **`AskQuestion`** prompts during phase plan clarification or unavoidable slice ambiguity

**Never instruct the user to:** run shell commands, run other slash commands, edit migration files manually, type `Resume: true`, approve slices in chat, override audit findings in chat, **re-invoke mid-batch**, or **switch mode when `next_required_mode` matches the current mode**.

All state scripts, nested command workflows, commits, checks, and migration edits are **agent-internal**.

---

## Parameter hygiene (agent-only; do not ask the user)

- Ignore trailing words attached to the slash command.
- Defaults: `Resume: true`, `Reset: false`.
- Optional labeled fields exist for agent recovery only — **do not** document them to the user.
- Do not infer parameters from previous invocations.

---

## Agent lifecycle (every invocation)

Run these steps silently; report only mode mismatches, AskQuestion prompts, hard failures, or completion.

1. **Initialize state** — if [`.cursor/v2_implementation_loop.json`](../v2_implementation_loop.json) is missing:
   ```bash
   uv run python scripts/cursor/v2_loop_state.py init
   ```
   If agent recovery needs reset: `init --reset` (never ask the user to run this).

2. **Validate:**
   ```bash
   uv run python scripts/cursor/v2_loop_state.py validate
   ```

3. **Mode gate** — compare Cursor mode to `next_required_mode` from state:
   - **`plan`** — Plan mode only
   - **`agent`** — Agent mode only
   - On mismatch: **do not** advance state. Exit with only:
     ```text
     Loop paused: next_step=<id> requires <plan|agent> mode.
     Switch mode and re-invoke: /run-v2-implementation
     ```

4. **Record starting mode** — read `next_required_mode` from state after validation; this is `batch_mode` for the rest of the invocation.

5. **Load batch checklist (mandatory):**
   ```bash
   uv run python scripts/cursor/v2_loop_state.py batch-steps
   ```
   Keep `steps_in_batch` and `remaining_count` for this invocation.

6. **Fast-forward once**, then enter the mode-batched loop (step 7):
   ```bash
   uv run python scripts/cursor/v2_loop_state.py fast-forward
   ```

7. **Mode-batched loop** — repeat until `batch-exit-check` reports `may_exit: true`, a hard failure stops the batch, or `next_step` is `done` and handled:

   a. Re-read state. Run fast-forward, then re-read state:
      ```bash
      uv run python scripts/cursor/v2_loop_state.py fast-forward
      ```
      Run exit check:
      ```bash
      uv run python scripts/cursor/v2_loop_state.py batch-exit-check --batch-mode <batch_mode>
      ```
      If `may_exit` is `false`, continue below. If `may_exit` is `true` and `exit_kind` is `mode_change` or `done`, go to step 8.

   b. If `next_step` is `done`, run the [`done`](#done-agent) handler once, then go to step 8.

   c. **Execute one substantive step** — see [Step handlers](#step-handlers). Inline nested command rules from `.cursor/commands/*.md`; do **not** tell the user to invoke those commands. Apply [Overrides](#overrides-when-running-under-this-loop) — they supersede nested “stop and wait” text. **No user-visible slice summary** — continue silently to step 7d.

   d. **Advance state silently:**
      ```bash
      uv run python scripts/cursor/v2_loop_state.py complete <step_id>
      ```
      Re-read state. Return to step 7a.

   **AskQuestion during a batch:** if a step uses `AskQuestion`, wait for the user's answer, finish that step, `complete` it, then continue the batch — do **not** exit the invocation early unless the turn ends (user re-invokes in same mode to resume).

   **Hard failure:** report the error, do **not** call `complete`, do **not** advance state; exit the invocation. User fixes and re-invokes in the same mode.

8. **Exit** — run exit check again, then post **only** the matching template:

   ```bash
   uv run python scripts/cursor/v2_loop_state.py batch-exit-check --batch-mode <batch_mode>
   ```

   - If `may_exit` is `false`: **protocol violation** — return to step 7a immediately; do **not** post any exit message.
   - If `exit_kind` is `mode_change` (and `next_required_mode` ≠ `batch_mode`):
     ```text
     Loop batch complete (<plan|agent> steps done). next_step=<id> requires <plan|agent> mode.
     Switch mode and re-invoke: /run-v2-implementation
     ```
   - If `exit_kind` is `done`: post [completion notification](#done-agent) only — no mode-switch line.

   Do **not** run `gh pr create`.

---

## Overrides when running under this loop

These override conflicting text in nested commands and [`.cursor/rules/30-planning-slices.mdc`](../rules/30-planning-slices.mdc):

- **Mode-batched execution** — run every step in `steps_in_batch` in one invocation; do not exit after a single substantive step.
- **Silent between steps** — no mid-batch slice reports, progress tables, or re-invoke instructions; continue to step 7a.
- **No slice chat approval** — loop advances via state `complete`; ignore build-plan-slice “stop and wait for approval.”
- **No nested slash commands for the user** — agent follows nested command docs internally.
- **No migration-slice gate in loop build steps** — for `*_pre_alembic`, `*_post_alembic`, and `*_build`, follow [build-plan-slice loop context](build-plan-slice.md#when-called-from-run-v2-implementation-loop-context); Alembic substeps use dedicated loop handlers instead.
- **No manual migration edit by user** — agent applies preview-suggested edits in `migration_manual_edit` (see below); ignore db-revision-preview “wait for manual migration approval.”
- **No db-revision approval prompts** — in `alembic_continue`, follow [db-revision-continue loop context](db-revision-continue.md#when-called-from-run-v2-implementation-loop-context); no AskQuestion for migration approval.
- **Non-interactive commits only** — ignore commit-changes interactive staging prompts; use loop commit flags from step handlers.
- **No audit chat override** — auto-fix blocking findings within current step scope; if still blocked, stop the batch with an error (no override protocol); user re-invokes in the same mode after fixes.
- **Ignore draft-plan “stop after each slice”** — that applies to manual plan building, not loop Plan batches.

---

## Step handlers

Read the linked command file and execute its rules **as the agent**. User invokes nothing.

### `phase_N_plan_bootstrap` (Plan)

Normally **fast-forwarded** before substantive work. If reached: no user-visible output; immediately `complete` via fast-forward or explicit complete in the same invocation.

### `phase_N_request_questions` (Plan)

Follow [`.cursor/commands/request-questions.md`](request-questions.md) with `Mode: plan` against [`docs/v2_engineering_design.md`](../../docs/v2_engineering_design.md) and the phase objective from V2 guide §6.

**Use the `AskQuestion` tool** for all blocking questions — not freeform chat.

Do **not** run phase-plan clarification on any other loop step.

### `phase_N_draft_plan` (Agent)

Follow [`.cursor/commands/draft-plan.md`](draft-plan.md) **loop context**. Target path: `current_plan_path` in state ([phase → plan paths](../../docs/plans/v2_implementation_loop_command.md#phase--plan-paths)).

### `phase_N_finalize_plan` (Agent)

Finalize plan in `docs/plans/`, then commit:

1. Follow [`.cursor/commands/audit-commit-readiness.md`](audit-commit-readiness.md) — auto-fix blocking findings within this step; if still blocked, stop with error (no chat override).
2. Follow [`.cursor/commands/review-abstractions.md`](review-abstractions.md) on diff — auto-fix only in step scope.
3. ```bash
   python scripts/cursor/commit_changes.py --non-interactive --skip-tests --message "Finalize V2 phase N plan: <plan-basename>"
   ```

### `phase_0_verify` (Agent)

Verify Phase 0 authority files (Loop Design Spec skip table). If missing, add them. If changed, commit:

```bash
python scripts/cursor/commit_changes.py --non-interactive --skip-tests --message "Complete V2 phase 0 documentation and authority"
```

If already complete, skip edits.

### Build slice steps — `phase_N_slice_<id>_build` (Agent)

Follow [`.cursor/commands/build-plan-slice.md`](build-plan-slice.md) for `Slice: <id>` from `current_plan_path`, then:

1. [`.cursor/commands/review-validation.md`](review-validation.md) — `Changes only: true`, `Edit: true`
2. [`.cursor/commands/review-consistency.md`](review-consistency.md) — `Changes only: true`, `Edit: true`
3. Audit + abstractions (auto-fix in scope; stop if still blocked)
4. ```bash
   python scripts/cursor/commit_changes.py --non-interactive --skip-tests --message "V2 phase N slice <id>: <short objective>"
   ```

**Slice ambiguity:** use **`AskQuestion`** when unavoidable — not freeform chat; not phase-plan `/request-questions`. Finish the step and continue the batch after the user answers.

### `phase_N_slice_<id>_pre_alembic` (Agent)

Same as build slice; schema tests may use `failure_expected`. Commit with `--skip-tests`.

### `phase_N_slice_<id>_alembic_preview` (Agent)

Follow [`.cursor/commands/db-revision-preview.md`](db-revision-preview.md) **loop context** (report only; no migration file edits in this step).

**No commit.**

Store the preview report in the agent turn (chat) for the next step.

### `phase_N_slice_<id>_migration_manual_edit` (Agent)

Apply all preview-suggested migration edits to the generated revision under `calendar_backend/db/migrations/versions/`:

- CHECKs, FK order, enum columns, SQLite `batch_alter_table`, naming per [repo convention §4](../../.cursor/repo_conventions.md)
- Run ruff format/check on the migration file

Follow the prior step's preview report. **No AskQuestion.** **No user file edit.** **No `/small-change`.**

**No commit.**

### `phase_N_slice_<id>_alembic_continue` (Agent)

Follow [`.cursor/commands/db-revision-continue.md`](db-revision-continue.md) **loop context** apply steps (`alembic upgrade head`, unmark `failure_expected`, pytest).

Commit once with non-interactive staging (SC-7):

```bash
python scripts/cursor/commit_changes.py --non-interactive --skip-checks --message "Apply V2 phase N migration: <revision-summary>"
```

Do **not** run a second loop commit after this step.

### `phase_N_slice_<id>_post_alembic` (Agent)

Follow build-plan-slice + reviews + non-interactive commit with `--skip-tests`.

### `phase_N_phase_checks` (Agent)

```bash
bash scripts/cursor/checks.sh
```

On failure: report and stop without advancing.

### `done` (Agent)

1. Run `bash scripts/cursor/checks.sh` once more.
2. Post **completion notification** (informational only — user opens PR manually when ready):
   - Branch from state
   - Approximate commit count
   - Phases 0–7 completed
   - Reminder: push branch and open PR manually (do not use `gh pr create` from the agent)

---

## Commit policy summary

| Step kind | Commit? | Script flags |
|---|---|---|
| finalize plan | Yes | `--non-interactive --skip-tests --message` |
| ordinary / pre / post alembic build | Yes | `--non-interactive --skip-tests --message` |
| alembic preview | No | — |
| migration manual edit | No | agent edits only |
| alembic continue | Yes (once) | `--non-interactive --skip-checks --message` |
| phase checks / done | No | runs `checks.sh` |

Before every commit step: audit-commit-readiness + review-abstractions (auto-fix in scope; stop if still blocked).

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

Slice order and Alembic five-step expansion: `v2_loop_state.py` parses `## Slices` from the active plan.

---

## Phase 1 dry-run — user interaction table

Verify every **mode stretch** allows **only** mode switch, one `/run-v2-implementation`, and AskQuestion (where marked). Individual step IDs run agent-internally within the batch — the user does not re-invoke per step.

| Mode stretch | Steps run in one invocation | User: switch mode after batch? | User: `/run-v2-implementation` | User: AskQuestion? |
|---|---|---|---|---|
| Plan — phase N clarification | `plan_bootstrap` → `request_questions` | yes → Agent | once per Plan stretch | **yes** (during `request_questions` only) |
| Agent — phase N plan + slices + checks | `draft_plan` → `finalize_plan` → all `phase_N_slice_*` substeps → `phase_N_phase_checks` | yes → Plan (next phase) or done | once per Agent stretch | only if slice ambiguous |

---

## Non-goals

- Opening or merging GitHub PRs
- Running subagents
- Interactive `git add -p`
- User-facing shell or slash-command instructions
- Phase-plan `/request-questions` outside `phase_N_request_questions`
