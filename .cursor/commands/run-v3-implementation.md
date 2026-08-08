Drive the V3 single-PR implementation loop using git-tracked state in [`.cursor/v3_implementation_loop.json`](../v3_implementation_loop.json).

**Authority:** [`docs/v3_cursor_implementation_guide.md`](../../docs/v3_cursor_implementation_guide.md) §2.1; [`docs/plans/v3_frontend_integration.md`](../../docs/plans/v3_frontend_integration.md).

## CRITICAL — Agent-only single batch

**Do not exit after one substep.** Finish the entire `v3_agent_block` in **one user invocation**. Only exit when `batch-exit-check` reports `may_exit: true`.

**Pre-loop requirement:** All ambiguities resolved and plan approved before first invoke. **No `AskQuestion` during the loop.**

**Mandatory at invocation start:**
```bash
uv run python scripts/cursor/v3_loop_state.py batch-steps
uv run python scripts/cursor/v3_loop_state.py current-substep
uv run python scripts/cursor/v3_loop_state.py set-pause-auto-resume false
uv run python scripts/cursor/v3_loop_state.py set-active-batch-mode agent
```

**Auto-resume:** [`.cursor/hooks/v3-loop-auto-resume.sh`](../hooks/v3-loop-auto-resume.sh) submits `/run-v3-implementation` when batch incomplete.

## User interaction model (locked)

The user may **only** invoke **`/run-v3-implementation`**. No mode switches, no slice approval, no AskQuestion.

## Agent lifecycle

1. **Initialize** — `uv run python scripts/cursor/v3_loop_state.py init` if state missing
2. **Validate** — `uv run python scripts/cursor/v3_loop_state.py validate`
3. **Fast-forward** — `uv run python scripts/cursor/v3_loop_state.py fast-forward`
4. **Loop** until `batch-exit-check --batch-mode agent` returns `may_exit: true`:
   - `current-substep` → execute slice → reviews → non-interactive commit (`--skip-tests`)
   - `substep-complete <id>`
5. **`done` handler** — run full `scripts/cursor/checks.sh`; post PR checklist

## Overrides

- No per-slice chat summaries mid-batch
- Migration manual edits: agent applies inline (no user file edit)
- Hard failure: `set-pause-auto-resume true`, stop without substep-complete

## Step handlers

| Substep pattern | Action |
|---|---|
| `phase_0_verify` | Verify V3 docs exist |
| `slice_*_build` | `/build-plan-slice` for slice |
| `slice_*_pre_alembic` etc. | Alembic five-step group |
| `v3_final_checks` | Full `checks.sh` |

## done

Run `scripts/cursor/checks.sh`. Notify: commits complete, open PR manually.
