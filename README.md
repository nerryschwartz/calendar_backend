# calendar_backend

Python service-layer backend for task planning, scheduling, calendar assignment, and free-time allocation.

The implementation source of truth is the updated V1 engineering design document. Finalized implementation plans live in `docs/plans/`.

## V3 HTTP API

Run the FastAPI server (local single-user dev):

```bash
uv run calendar-backend-api
```

- Health: `GET http://127.0.0.1:8000/health`
- OpenAPI: `http://127.0.0.1:8000/docs`

See [`docs/v3_engineering_design.md`](docs/v3_engineering_design.md) for the API contract and [`docs/frontend/v1_setup.md`](docs/frontend/v1_setup.md) for frontend repo setup.

### Free-Time Drafts

`POST /api/free-time/activities/draft-edits` accepts an `edits` array. Edits have
an `op` discriminator: `create`, `update`, `set_enabled`, `set_block_families`,
`clear_block_families`, `add_prerequisite`, `remove_prerequisite`, or `delete`.
Create supplies a unique non-UUID `draft_ref` and the usual activity fields.
Subsequent edits use `activity_ref`, either an existing UUID or an earlier draft
reference. Prerequisite edits identify the plan with `prerequisite_plan_id`.
See OpenAPI for each operation's required fields.

The batch validates the final enabled fraction sum and applies atomically.
Success returns `applied_count` (including accepted no-ops) and final `activities`.
HTTP 422 returns `detail: {applied_count: 0, errors: [...]}` without persisting any
of the batch. Deleting an unsaved draft reference is a no-op. Existing activity
UUIDs must exist. Existing individual mutation routes remain available.

`DELETE /api/free-time/activities/{activity_id}` removes the activity,
prerequisites, and bookings starting at or after the backend clock. Earlier and
already-started bookings retain their timestamps and display labels, with the
activity reference cleared. Remaining enabled fractions must still sum to one;
use a draft to delete and rebalance together. Mutations do not trigger an
automatic schedule refresh.

### Timer Diagnostics

`GET /api/timers/active` returns `timers` plus `diagnostics` containing
`backend_now`, `active_calendar_run_id`, `last_refresh_failed`, `last_failure_at`,
`last_failure_reason`, and `nearby_entries`. Nearby entries use the timer DTO and
contain at most 10 current or future windows, ordered by start time. Queries
filter current windows and limit nearby rows in SQL. Empty timers may be normal
when no window overlaps now; diagnostics do not change scheduling behavior.

Calendar, timer, schedule-state, and activity timestamps include an explicit UTC
offset (`Z` or `+00:00`), including timestamps read back from SQLite.

### Validation

```bash
uv run ruff format .
uv run pytest -m 'not slow and not failure_expected'
```

Relevant slow API regressions can be run explicitly with
`uv run pytest tests/api/test_constraints.py tests/api/test_timers.py`.
