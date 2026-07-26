Drive the V2 single-PR implementation loop using git-tracked state in [`.cursor/v2_implementation_loop.json`](../v2_implementation_loop.json).

**Authority:** [Loop Design Spec](../../docs/plans/v2_implementation_loop_command.md#loop-design-spec); [`docs/v2_cursor_implementation_guide.md`](../../docs/v2_cursor_implementation_guide.md) §2.1.

## User interaction model (locked)

The user may **only**:

1. Switch Cursor **Plan / Agent** mode when the loop requires it
2. Invoke **`/run-v2-implementation`** (no labeled fields in normal use)
3. Answer **`AskQuestion`** prompts during phase plan clarification or unavoidable slice ambiguity

**Never instruct the user to:** run shell commands, run other slash commands, edit migration files manually, type `Resume: true`, approve slices in chat, or override audit findings in chat.

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

4. **Fast-forward skip-if-done steps** (same invocation, before substantive work):
   ```bash
   uv run python scripts/cursor/v2_loop_state.py fast-forward
   ```
   Re-read state after fast-forward. This auto-completes no-op steps (e.g. `phase_N_plan_bootstrap`) and other satisfied predicates per [Loop Design Spec](../../docs/plans/v2_implementation_loop_command.md#skip-if-done-predicates).

5. **Execute one substantive step** — see [Step handlers](#step-handlers). Inline nested command rules from `.cursor/commands/*.md`; do **not** tell the user to invoke those commands.

6. **Advance state silently:**
   ```bash
   uv run python scripts/cursor/v2_loop_state.py complete <step_id>
   ```

7. **Exit** — one substantive step per invocation (C6). Exception: fast-forward may complete multiple skip-if-done steps before substantive work.

When `next_step` is `done`, run final checks and post the [completion notification](#done-agent). Do **not** run `gh pr create`.

---

## Overrides when running under this loop

These override conflicting text in nested commands and [`.cursor/rules/30-planning-slices.mdc`](../rules/30-planning-slices.mdc):

- **No slice chat approval** — loop advances via state `complete`; user does not approve each slice in chat.
- **No nested slash commands for the user** — agent follows nested command docs internally.
- **No manual migration edit by user** — agent applies preview-suggested edits in `migration_manual_edit` (see below).
- **No audit chat override** — auto-fix blocking findings within current step scope; if still blocked, stop with an error report; user re-invokes `/run-v2-implementation` after agent fixes in a later turn (no override protocol).

---

## Step handlers

Read the linked command file and execute its rules **as the agent**. User invokes nothing.

### `phase_N_plan_bootstrap` (Plan)

Normally **fast-forwarded** before substantive work. If reached: no user-visible output; immediately `complete` via fast-forward or explicit complete in the same invocation.

### `phase_N_request_questions` (Plan)

Follow [`.cursor/commands/request-questions.md`](request-questions.md) with `Mode: plan` against [`docs/v2_engineering_design.md`](../../docs/v2_engineering_design.md) and the phase objective from V2 guide §6.

**Use the `AskQuestion` tool** for all blocking questions — not freeform chat.

Do **not** run phase-plan clarification on any other loop step.

### `phase_N_draft_plan` (Plan)

Follow [`.cursor/commands/draft-plan.md`](draft-plan.md). Target path: `current_plan_path` in state ([phase → plan paths](../../docs/plans/v2_implementation_loop_command.md#phase--plan-paths)).

### `phase_N_finalize_plan` (Plan)

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

**Slice ambiguity:** use **`AskQuestion`** (max one per invocation) — not freeform chat; not phase-plan `/request-questions`.

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

Follow [`.cursor/commands/db-revision-continue.md`](db-revision-continue.md) apply steps (`alembic upgrade head`, unmark `failure_expected`, pytest).

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

Verify every step allows **only** mode switch, `/run-v2-implementation`, and AskQuestion (where marked).

| Step ID | Mode | User: switch mode? | User: `/run-v2-implementation` | User: AskQuestion? |
|---|---|---|---|---|
| `phase_1_plan_bootstrap` | plan | maybe | yes | no (fast-forward) |
| `phase_1_request_questions` | plan | maybe | yes | **yes** |
| `phase_1_draft_plan` | plan | maybe | yes | no |
| `phase_1_finalize_plan` | plan | maybe | yes | no |
| `phase_1_slice_*_pre_alembic` | agent | maybe | yes | only if ambiguous |
| `phase_1_slice_*_alembic_preview` | agent | maybe | yes | no |
| `phase_1_slice_*_migration_manual_edit` | agent | maybe | yes | no |
| `phase_1_slice_*_alembic_continue` | agent | maybe | yes | no |
| `phase_1_slice_*_post_alembic` | agent | maybe | yes | only if ambiguous |
| `phase_1_slice_*_build` | agent | maybe | yes | only if ambiguous |
| `phase_1_phase_checks` | agent | maybe | yes | no |
| `phase_2_plan_bootstrap` | plan | yes | yes | no (fast-forward) |

---

## Non-goals

- Opening or merging GitHub PRs
- Running subagents
- Interactive `git add -p`
- User-facing shell or slash-command instructions
- Phase-plan `/request-questions` outside `phase_N_request_questions`
