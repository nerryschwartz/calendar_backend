# calendar_backend V3 Cursor Implementation Guide

Recommended location: `docs/v3_cursor_implementation_guide.md`

This guide turns [`docs/v3_engineering_design.md`](v3_engineering_design.md) into a Cursor-ready workflow for **frontend integration** on top of the completed V2 service layer.

**Precedence (highest first):**

1. [`.cursor/repo_conventions.md`](../.cursor/repo_conventions.md)
2. This guide and finalized plans in [`docs/plans/`](plans/)
3. [`docs/v3_engineering_design.md`](v3_engineering_design.md)
4. [`docs/v2_cursor_implementation_guide.md`](v2_cursor_implementation_guide.md) for Alembic five-step splits and testing conventions
5. Archived V1 guide and V1 engineering design PDF

---

## 0. Workflow changes from V2

### 0.1 V3 scope

V3 adds HTTP API, read services, timer/notification persistence, and frontend setup documentation. V2 scheduling and plan-tree behavior is preserved.

### 0.2 Pre-loop ambiguity resolution

**All material ambiguities must be resolved before the first `/run-v3-implementation` invoke.** Locked decisions live in:

- [`docs/v3_engineering_design.md`](v3_engineering_design.md) §10
- Finalized plan [`docs/plans/v3_frontend_integration.md`](plans/v3_frontend_integration.md) §Locked assumptions

During the runtime loop: **no `AskQuestion`**, no Plan mode, no per-slice chat approval. Hard failures stop the loop.

### 0.3 Single-pass Agent loop

Unlike V2 (Plan ↔ Agent per phase), V3 uses **Agent-only** execution:

```text
Pre-loop (manual): ambiguity resolution → draft/finalize plan → user approval
/run-v3-implementation (Agent): phase_0_verify → all slices → done
```

### 0.4 Alembic slices

Reuse V2 five-step migration split (§0.2 of V2 guide) for notification ORM schema changes.

### 0.5 What to reuse from V2

- Layer boundaries, abstraction discipline, testing expectations
- Cursor commands: `/build-plan-slice`, `/db-revision-preview`, `/db-revision-continue`, `/small-change`, review commands
- [`scripts/cursor/commit_changes.py`](../../scripts/cursor/commit_changes.py) `--non-interactive`
- Alembic tutorial from V2 guide §8

---

## 1. Locked workflow decisions

- Work in WSL on the existing repository.
- Use `uv` for dependencies; `uv add fastapi uvicorn pydantic` in the FastAPI slice.
- Default checks: `uv run ruff format .`, `uv run ruff check .`, `uv run pyright`, `uv run pytest -m "not slow and not failure_expected"`.
- Finalized plan: [`docs/plans/v3_frontend_integration.md`](plans/v3_frontend_integration.md).
- Single consolidated plan (not multi-phase V2 plans).
- Full [`scripts/cursor/checks.sh`](../../scripts/cursor/checks.sh) at Slice 11 and loop `done`.

---

## 2. How to use Cursor for V3

### 2.1 Single-PR loop (recommended)

**Pre-loop (before first invoke):**

1. Resolve ambiguities (locked in V3 design §10)
2. Finalize [`docs/plans/v3_frontend_integration.md`](plans/v3_frontend_integration.md)
3. User approves plan

**Runtime:**

```text
/run-v3-implementation
```

**Your only interactions:**

1. Invoke **`/run-v3-implementation`** once (auto-resume hook continues mid-batch)
2. Hard failure messages only — no slice approval, no mode switches, no AskQuestion

Progress lives in git-tracked [`.cursor/v3_implementation_loop.json`](../.cursor/v3_implementation_loop.json). Auto-resume via [`.cursor/hooks/v3-loop-auto-resume.sh`](../.cursor/hooks/v3-loop-auto-resume.sh). Other prompts clear batch state via [`.cursor/hooks/v3-loop-clear-on-other-prompt.sh`](../.cursor/hooks/v3-loop-clear-on-other-prompt.sh).

At loop `done`, open your PR manually.

### 2.2 Manual alternative

Follow [`docs/plans/v3_frontend_integration.md`](plans/v3_frontend_integration.md) slice by slice with `/build-plan-slice` and stop for approval after each slice (standard [`.cursor/rules/30-planning-slices.mdc`](../.cursor/rules/30-planning-slices.mdc) behavior).

---

## 3. V3 implementation slices

See [`docs/plans/v3_frontend_integration.md`](plans/v3_frontend_integration.md) for the full slice list:

1. V3 authority docs
2. V3 loop infrastructure
3. Notification ORM + migration + timer/notification services
4. Read services
5. FastAPI skeleton
6. Plan tree + mutation routes
7. Calendar + schedule routes
8. Timer + notification routes
9. Remaining service routes
10. Frontend setup guide
11. API hardening + full checks

---

## 4. Testing conventions

- Service tests for read services, timer, and notification services
- API integration tests with FastAPI `TestClient`
- Schema tests for notification ORM (mark `failure_expected` until migration applied)
- Invariant tests before complex API wiring

---

## 5. Frontend documentation

[`docs/frontend/v1_setup.md`](frontend/v1_setup.md) describes scaffolding a separate React + Vite + TypeScript repo and wiring the four views to this API.
