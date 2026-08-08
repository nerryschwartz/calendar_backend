# calendar_backend V3 Engineering Design

Recommended location: `docs/v3_engineering_design.md`

This document is the **architectural source of truth** for V3 behavior. It extends [`docs/v2_engineering_design.md`](v2_engineering_design.md) with HTTP API exposure, read services, and timer/notification persistence for frontend integration.

**Precedence (highest first):**

1. [`.cursor/repo_conventions.md`](../.cursor/repo_conventions.md)
2. This document and finalized plans in [`docs/plans/`](plans/)
3. [`docs/v3_cursor_implementation_guide.md`](v3_cursor_implementation_guide.md)
4. [`docs/v2_engineering_design.md`](v2_engineering_design.md) for V2 scheduling and plan-tree behavior not superseded here
5. Archived V1 PDF and V1 implementation guide

**Relationship to V2:** V3 evolves the existing V2 codebase in place. V2 service-layer behavior, scheduling pipeline, and persisted plan/calendar shape remain unless this document explicitly changes them.

---

## 1. Overview

V3 adds a **FastAPI HTTP boundary** so a separate React frontend can:

- View task, free-time, and block calendars
- Navigate and mutate the plan tree (client-side edit staging; mutations on Save)
- Display backend-computed active timers
- Manage a persisted notification queue for task/block timer completions

Core V2 principles unchanged: correctness, readability, deterministic behavior, explicit invariants, minute-granularity scheduling, frozen domain DTOs, mutations through services inside transactions.

---

## 2. Non-goals (V3)

Do not implement unless explicitly requested:

- Production authentication or multi-user tenancy (local single-user dev only)
- OS-level notification delivery (browser Notification API is frontend responsibility)
- External calendar sync
- Undo / audit / soft-delete / history system
- Server-side draft sessions for plan tree edit mode (client stages edits locally)
- Mobile native frontend
- Sub-minute scheduling
- Blocks on user-facing task calendar **export** (V2 non-goal preserved; in-app block calendar view is in scope)

---

## 3. HTTP API layer

### 3.1 Package ownership

`calendar_backend/api/` owns:

- FastAPI application factory and router registration
- Request/response Pydantic schemas (HTTP serialization only)
- Dependency injection for DB session and clock
- Mapping `ServiceResult` failures to HTTP responses

The API layer **does not** own business rules, scheduling algorithms, or direct SQLAlchemy queries.

### 3.2 Session lifecycle

Each request obtains a SQLAlchemy session via FastAPI dependencies. Mutations use existing service methods that manage transactions internally.

### 3.3 Error contract

`ServiceResult` failures map to HTTP **422 Unprocessable Entity** with body:

```json
{
  "errors": ["human-readable message"],
  "code": "optional_machine_code"
}
```

Refresh/assignment failures may include structured fields (`last_failure_reason`, assignment conflict details) for frontend pop-ups.

### 3.4 CORS

Enable permissive CORS for local Vite dev (`http://localhost:5173`). No auth middleware in V3.

---

## 4. Read services

V2 services are mutation-oriented. V3 adds read services that wrap existing internal loaders:

| Service | Responsibility |
|---|---|
| `PlanTreeReadService` | Master bootstrap, plan-by-id with ancestry/children/properties, search |
| `CalendarReadService` | Task/free-time entries, block calendar entries, active schedule state |

Read services return existing frozen DTOs from `calendar_backend/domain/`.

---

## 5. Timers

### 5.1 Purpose

Timers show countdown until the end of the **current** schedulable window for block, task, or free-time entries on the active calendar run.

### 5.2 Backend ownership

`TimerService.get_active_timers(clock)` computes authoritative active windows from calendar data + clock. The frontend displays countdowns; on expiry it calls `TimerService.complete_timer(...)`.

### 5.3 Completion

Completing a task or block timer enqueues a `NotificationQueueItem`. Free-time timer completion does **not** enqueue (frontend may show a local browser notification only).

Completion is idempotent per `(timer_key, window_end_at)`.

---

## 6. Notification queue

### 6.1 Persistence

`NotificationQueueItem` stores pending notifications for task/block timer completions:

- Source kind: `TASK` or `BLOCK`
- Reference plan and optional calendar entry
- Window end timestamp
- Created/dismissed timestamps

### 6.2 Lifecycle

- **Create:** `TimerService.complete_timer` for non-free-time timers
- **List pending:** `NotificationQueueService.list_pending`
- **Dismiss:** `NotificationQueueService.dismiss` (discard from queue)

The notifications view deep-links to plan-tree editor by `plan_id`.

---

## 7. Frontend integration contract

### 7.1 Edit mode (client-side staging)

Plan tree edit mode accumulates mutations in frontend state. On Save:

1. Apply ordered mutation API calls
2. `POST /api/plans/validate` (optional pre-check)
3. `POST /api/schedule/refresh` (master horizon + orchestration)
4. Surface tree validation or refresh failures to the user

Edits do not hit the database until Save.

### 7.2 Schedule refresh

`POST /api/schedule/refresh` runs `MasterHorizonService.refresh_master_horizon` then `OrchestrationService.refresh_schedule`.

### 7.3 Exposed mutation scope

All V2 public service methods needed by the four frontend views are exposed, including:

- Plan tree, goal, task, block, repetition mutations
- Time constraints, free-time activities, app settings
- Deletion preview and conflict deletion suggestions

---

## 8. API surface summary

See finalized plan [`docs/plans/v3_frontend_integration.md`](plans/v3_frontend_integration.md) for route inventory. Routers group by: plans, schedule, timers, notifications, settings, constraints, free-time, deletion.

---

## 9. Entry points

| Command | Purpose |
|---|---|
| `calendar-backend-dev` | Existing dev CLI |
| `calendar-backend-api` | Uvicorn serving FastAPI app (default `localhost:8000`) |

---

## 10. Locked V3 decisions

| Topic | Decision |
|---|---|
| Timers & notifications | Backend-owned ORM + services + APIs |
| Edit mode | Client-side staging; mutations on Save |
| Frontend stack | React + Vite + TypeScript (separate repo; setup guide in `docs/frontend/`) |
| Block calendar | In-app API view in scope; export non-goal unchanged |
