# Plan: V3 frontend integration

**Finalized plan location:** [`docs/plans/v3_frontend_integration.md`](v3_frontend_integration.md)

## Context

V3 adds FastAPI HTTP APIs, read services, timer/notification persistence, and frontend setup documentation on top of the completed V2 service layer. See [`docs/v3_engineering_design.md`](../v3_engineering_design.md) and [`docs/v3_cursor_implementation_guide.md`](../v3_cursor_implementation_guide.md).

## Non-goals

- Production authentication or multi-user tenancy
- OS-level notification delivery
- Server-side draft sessions for plan tree edit mode
- Mobile native frontend
- Frontend code in this repository (setup guide only)

## Locked assumptions

| Topic | Decision |
|---|---|
| Timers & notifications | Backend-owned ORM + services + APIs |
| Edit mode | Client-side staging; mutations on Save |
| Exposed services | App settings, time constraints, free-time, repetition, master horizon, deletion preview, conflict suggestions |
| Frontend stack | React + Vite + TypeScript separate repo |
| Block calendar | In-app API view in scope |

## Slices

### Slice 1: V3 authority docs

**Objective:** Add V3 engineering design, cursor guide, and update source-of-truth rule.

**Files expected to change:**
- `docs/v3_engineering_design.md`
- `docs/v3_cursor_implementation_guide.md`
- `.cursor/rules/00-project-source-of-truth.mdc`

**Implementation steps:**
1. Write V3 design and guide per locked assumptions.
2. Update source-of-truth rule for V3 precedence.

**Tests/checks:** Doc review only.

**Acceptance criteria:**
- HTTP API in scope in V3 design; V2 non-goals preserved historically.

**Risks/edge cases:** None.

---

### Slice 2: V3 loop infrastructure

**Objective:** Agent-only `/run-v3-implementation` loop with state script and hooks.

**Files expected to change:**
- `.cursor/commands/run-v3-implementation.md`
- `.cursor/v3_implementation_loop.json`
- `scripts/cursor/v3_loop_state.py`
- `.cursor/hooks/v3-loop-auto-resume.sh`
- `.cursor/hooks/v3-loop-clear-on-other-prompt.sh`
- `.cursor/hooks.json`

**Implementation steps:**
1. Implement simplified Agent-only state machine.
2. Add auto-resume hooks mirroring V2.

**Tests/checks:**
```bash
uv run python scripts/cursor/v3_loop_state.py validate
```

**Acceptance criteria:**
- `validate` passes; command doc forbids mid-loop AskQuestion.

**Risks/edge cases:** Hook registration in hooks.json.

---

### Slice 3a-pre-alembic: Notification ORM + domain

**Objective:** Add notification queue ORM and domain types without migration apply.

**Files expected to change:**
- `calendar_backend/models/notifications.py`
- `calendar_backend/domain/enums.py`
- `calendar_backend/domain/dtos.py` or new notification DTO module
- `calendar_backend/models/__init__.py` (Alembic env import if needed)
- `tests/models/test_notification_queue_item.py`

**Implementation steps:**
1. Add `NotificationQueueItem` model and domain enums/DTOs.
2. Add schema tests marked `failure_expected`.

**Tests/checks:** pytest schema tests.

**Acceptance criteria:** ORM maps correctly after migration.

**Risks/edge cases:** Alembic model discovery.

---

### Slice 3b-alembic-preview: Notification migration preview

**Objective:** Autogenerate Alembic revision for notification table.

**Files expected to change:** Migration under `calendar_backend/db/migrations/versions/`

**Implementation steps:** Follow `/db-revision-preview`.

**Tests/checks:** Preview report only.

**Acceptance criteria:** Revision generated.

---

### Slice 3c-migration-script-edits: Notification migration edits

**Objective:** Review and fix generated migration.

**Implementation steps:** Edit migration per preview report.

---

### Slice 3d-alembic-continue: Apply notification migration

**Objective:** `upgrade head`, unmark failure_expected, commit migration.

**Implementation steps:** Follow `/db-revision-continue`.

---

### Slice 3e-post-alembic: Timer + notification services

**Objective:** Implement `TimerService` and `NotificationQueueService`.

**Files expected to change:**
- `calendar_backend/services/timer.py`
- `calendar_backend/services/notification_queue.py`
- `tests/services/test_timer_service.py`
- `tests/services/test_notification_queue_service.py`

**Implementation steps:**
1. TimerService derives active windows from calendar data.
2. NotificationQueueService list/dismiss/enqueue.

**Tests/checks:** Service unit tests.

**Acceptance criteria:** Complete timer enqueues notification for task/block only.

---

### Slice 4: Read services

**Objective:** PlanTreeReadService and CalendarReadService.

**Files expected to change:**
- `calendar_backend/services/plan_tree_read.py`
- `calendar_backend/services/calendar_read.py`
- `tests/services/test_plan_tree_read_service.py`
- `tests/services/test_calendar_read_service.py`

**Tests/checks:** Service tests.

**Acceptance criteria:** Master, plan-by-id, search, calendar entries, schedule state.

---

### Slice 5: FastAPI skeleton

**Objective:** App factory, deps, errors, base schemas, dependencies.

**Files expected to change:**
- `calendar_backend/api/` package
- `pyproject.toml`
- `tests/api/test_health.py`

**Implementation steps:**
1. `uv add fastapi uvicorn pydantic`
2. Health route smoke test.

**Tests/checks:** TestClient health test.

**Acceptance criteria:** App starts via TestClient.

---

### Slice 6: Plan tree + mutation routes

**Objective:** Plans router with read and mutation endpoints.

**Files expected to change:**
- `calendar_backend/api/routers/plans.py`
- `calendar_backend/api/schemas/plans.py`
- `tests/api/test_plans_router.py`

**Tests/checks:** API integration tests.

**Acceptance criteria:** Representative CRUD flows work.

---

### Slice 7: Calendar + schedule routes

**Objective:** Schedule refresh, state, calendar read endpoints.

**Files expected to change:**
- `calendar_backend/api/routers/schedule.py`
- `tests/api/test_schedule_router.py`

**Tests/checks:** Integration tests including refresh failure response.

---

### Slice 8: Timer + notification routes

**Objective:** Timers and notifications HTTP endpoints.

**Files expected to change:**
- `calendar_backend/api/routers/timers.py`
- `calendar_backend/api/routers/notifications.py`
- `tests/api/test_timers_router.py`
- `tests/api/test_notifications_router.py`

---

### Slice 9: Remaining service routes

**Objective:** Settings, constraints, free-time, repetition, deletion routers.

**Files expected to change:**
- `calendar_backend/api/routers/settings.py`
- `calendar_backend/api/routers/constraints.py`
- `calendar_backend/api/routers/free_time.py`
- `calendar_backend/api/routers/repetition.py`
- `calendar_backend/api/routers/deletion.py`
- Corresponding tests

---

### Slice 10: Frontend setup guide

**Objective:** React + Vite setup documentation.

**Files expected to change:**
- `docs/frontend/v1_setup.md`

**Acceptance criteria:** Four views, edit-mode save sequence documented.

---

### Slice 11: API hardening

**Objective:** CORS, OpenAPI tags, README, full checks.

**Files expected to change:**
- `calendar_backend/api/app.py`
- `README.md`

**Tests/checks:**
```bash
scripts/cursor/checks.sh
```

**Acceptance criteria:** Full check suite passes.

## Abstraction check

| Addition | Justification |
|---|---|
| `calendar_backend/api/` | Real HTTP boundary for frontend |
| Read services | Query operations not on mutation services |
| Notification ORM | Persisted queue required by V3 design |

## Dependency changes

- `fastapi`, `uvicorn[standard]`, `pydantic` via uv in Slice 5

## Open questions

None — resolved pre-loop per V3 guide §0.2.
