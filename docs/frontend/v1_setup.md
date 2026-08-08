# Frontend v1 setup (React + Vite + TypeScript)

This guide scaffolds a **separate** frontend repository that talks to `calendar_backend` over the V3 HTTP API.

## Prerequisites

- Node.js 20+
- Running API server from the backend repo:

```bash
cd calendar_backend
uv run calendar-backend-api
```

Default base URL: `http://127.0.0.1:8000`

OpenAPI docs: `http://127.0.0.1:8000/docs`

## Create the frontend repo

```bash
npm create vite@latest calendar-frontend -- --template react-ts
cd calendar-frontend
npm install
npm install react-router-dom
```

Optional: `npm install @tanstack/react-query` for cached fetching.

## Environment

Create `.env`:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## Suggested folder layout

```text
src/
  api/client.ts          # fetch wrapper + error parsing
  api/plans.ts           # plan tree endpoints
  api/schedule.ts        # calendars + refresh
  api/timers.ts
  api/notifications.ts
  views/
    CalendarsView.tsx
    BlockCalendarView.tsx
    PlanTreeView.tsx
    TimersView.tsx
    NotificationsView.tsx
  App.tsx                # react-router routes
```

## Routes (v1)

| Path | View |
|---|---|
| `/calendars` | Task + free-time calendar |
| `/calendars/blocks` | Block calendar |
| `/plan-tree` | Master plan root |
| `/plan-tree/:planId` | Plan node detail |
| `/timers` | Active timers |
| `/notifications` | Notification queue |

## API client sketch

```typescript
const baseUrl = import.meta.env.VITE_API_BASE_URL;

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`);
  if (!response.ok) {
    const detail = await response.json();
    throw detail;
  }
  return response.json() as Promise<T>;
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    throw await response.json();
  }
  return response.json() as Promise<T>;
}
```

## Plan tree edit mode (client-side staging)

1. Default: **read-only** — render plan from `GET /api/plans/{id}`.
2. Toggle **edit mode** — show mutation controls; accumulate a local `draftEdits[]` queue (do not call API yet).
3. **Save edits**:
   - Run queued mutations in order (POST/PATCH/DELETE endpoints under `/api/plans/...`).
   - `POST /api/plans/validate`
   - `POST /api/schedule/refresh`
   - On 422, show `detail.errors` and assignment conflict payload if present.
4. **Exit edit mode** with unsaved edits — confirm discard vs save (frontend-only dialog).

## Timers view

- Poll `GET /api/timers/active` every 30 seconds (or use `window.setInterval`).
- Render countdown to each `window_end_at`.
- When local clock passes `window_end_at`, call `POST /api/timers/{timer_key}/complete`.
- Request browser notification permission; show `Notification` on completion.
- Non-free-time completions appear in `GET /api/notifications`.

## Notifications view

- Load `GET /api/notifications`.
- **Edit** — navigate to `/plan-tree/{plan_id}` with edit mode enabled.
- **Save** in editor — same save sequence as plan tree.
- **Discard** — `POST /api/notifications/{id}/dismiss`.

## Calendars view

- Task/free-time: `GET /api/calendar/tasks`
- Blocks: `GET /api/calendar/blocks`
- Refresh button: `POST /api/schedule/refresh` then reload calendars.
- Show refresh failures using structured 422 responses from the schedule endpoint.

## CORS

The backend enables CORS for `http://localhost:5173` and `http://127.0.0.1:5173` (Vite default).

## Next steps after v1 scaffold

- Add search bar wired to `GET /api/plans/search?q=`
- Wire settings, constraints, free-time, and repetition panels in plan edit mode
- Use OpenAPI schema to generate TypeScript types if desired
