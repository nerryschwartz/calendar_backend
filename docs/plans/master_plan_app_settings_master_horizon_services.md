# Plan: Master plan, app settings, and master horizon services

**Finalized plan location:** `docs/plans/master_plan_app_settings_master_horizon_services.md`

## Context

Implement Prompt 6 from [docs/cursor_implementation_guide.md](../cursor_implementation_guide.md): foundational V1 services per [docs/calendar_backend_v1_engineering_design_updated.pdf](../calendar_backend_v1_engineering_design_updated.pdf) §7 (service layer), §8.1–§8.2 (ServiceResult / DTOs), §11 (settings and time handling), Appendix §12 (time/error rules), and locked architectural notes (master plan, master horizon constraint).

Design constraints:
- [`calendar_backend/services/`](../../calendar_backend/services/) owns **public service methods, validation, transactions, and persistence-changing behavior** (design §4); no SQLAlchemy sessions in domain ([repo convention §5](../../.cursor/repo_conventions.md)).
- Public methods return **`ServiceResult[T]`** via [`calendar_backend/domain/results.py`](../../calendar_backend/domain/results.py); mutations run inside [`transaction(session)`](../../calendar_backend/db/session.py).
- ORM models in [`calendar_backend/models/`](../../calendar_backend/models/) are persistence records only — services own bootstrap and updates.
- **`Clock` protocol** ([`calendar_backend/domain/time.py`](../../calendar_backend/domain/time.py)) stamps `created_at` / `updated_at`; services inject `Clock` (default `SystemClock`).
- **Service bootstrap defaults** live in the mutating service module ([repo convention §1](../../.cursor/repo_conventions.md)); e.g. `DEFAULT_*` in [`app_settings.py`](../../calendar_backend/services/app_settings.py), `MASTER_PLAN_NAME` in [`master_plan.py`](../../calendar_backend/services/master_plan.py).
- **Master plan:** normal `GoalPlan` row with `name="master"`, `parent_id=NULL`, `is_master=True`, generated UUID (design §5 / Section 6 master plan notes).
- **Master horizon:** `[run_started_at, run_started_at + master_horizon_duration_minutes)` as half-open UTC window; materialized as **exactly one** `SYSTEM_MASTER_HORIZON` constraint group with **exactly one window** on the master plan; updated only via `MasterHorizonService` (design §5.5, §7, Section 8 assignment intent).
- **App settings:** singleton row (`singleton_id=1`); `local_timezone` is IANA string for local-period logic. V1 has no configurable scheduling granularity setting (removed from ORM).
- **Prompt 7 boundary:** `TimeConstraintService` rejects direct edits to system constraints; this plan implements the legitimate writer (`MasterHorizonService`) but not user-edit rejection tests (note in slice 5).

**Locked clarification:** Slice 3 implements the **full** `AppSettingsService` (`get_settings`, `update_settings`), not bootstrap/read-only.

Current repo state:
- ORM complete through Prompt 5 ([`core_plan_orm_models_part2.md`](core_orm_models_part2.md)): plans, constraints, settings, etc.
- Domain primitives complete ([`domain_primitives.md`](domain_primitives.md)): IDs, enums, errors, time helpers, `ServiceResult`.
- [`calendar_backend/services/`](../../calendar_backend/services/) exists (`master_plan.py`, `app_settings.py`); `master_horizon.py` deferred to slice 4.
- DB layer: [`tests/db/test_session.py`](../../tests/db/test_session.py) covers `transaction()`; service-layer test fixtures deferred to this plan slice 1.

Build workflow: use `/build-plan-slice` per slice against this file; stop after each slice for approval.

## Non-goals

- `PlanTreeService`, `TaskService`, `RepetitionService`, resolution, assignment, free-time, orchestration — later prompts.
- `TimeConstraintService` user constraint APIs and **direct-edit rejection** for system constraints — Prompt 7 (slice 5 may document deferral).
- `PlanTreeInvariantService.validate_master_tree()` — Prompt 7; slice 5 tests **local** master/settings/horizon invariants only.
- Production HTTP API, dev CLI commands (Prompt 18), Alembic revisions (handled separately when schema changes).
- OR-Tools / scheduling package code.
- Pydantic / HTTP serialization layers.
- Strict IANA timezone catalog beyond `zoneinfo.ZoneInfo` parse validation.
- `ActiveCalendarState` mutation (orchestration / assignment prompts).

## Locked assumptions

- **Service modules (design §4):**
  - [`calendar_backend/services/master_plan.py`](../../calendar_backend/services/master_plan.py)
  - [`calendar_backend/services/app_settings.py`](../../calendar_backend/services/app_settings.py)
  - [`calendar_backend/services/master_horizon.py`](../../calendar_backend/services/master_horizon.py)
  - [`calendar_backend/services/__init__.py`](../../calendar_backend/services/__init__.py) — docstring only (large-package rule: no barrel re-exports).
- **Bootstrap defaults in service modules** ([repo convention §1](../../.cursor/repo_conventions.md)):
  - [`master_plan.py`](../../calendar_backend/services/master_plan.py): `MASTER_PLAN_NAME = "master"`
  - [`app_settings.py`](../../calendar_backend/services/app_settings.py): `DEFAULT_LOCAL_TIMEZONE = "UTC"`, `DEFAULT_MASTER_HORIZON_DURATION_MINUTES = 1_051_200` (two × 365-day years in minutes), `DEFAULT_EXACT_SOLVER_TIME_LIMIT_SECONDS = 30`, `DEFAULT_EXACT_SOLVER_MODEL_SIZE_LIMIT = 1000`, `DEFAULT_HEURISTIC_ENABLED = True`, `DEFAULT_FREE_TIME_WEEK_START_DAY = FreeTimeWeekStartDay.MONDAY`
- **DTOs:** frozen dataclasses in [`calendar_backend/domain/dtos.py`](../../calendar_backend/domain/dtos.py) (new), added incrementally per slice:
  - `GoalPlanDTO` — `plan_id: PlanID`, `name`, `is_master`, `parent_id`, `created_at`, `updated_at`
  - `AppSettingsDTO` — all persisted settings fields + `updated_at`
  - `MasterHorizonDTO` — `horizon_start`, `horizon_end`, `constraint_group_id: TimeConstraintGroupID`, `time_window_id: TimeWindowID`
- **`MasterPlanService.ensure_master_exists()`** — idempotent; reads master inside `transaction(session)` only ([repo convention §2](../../.cursor/repo_conventions.md)); creates `Plan` (`GOAL`, `is_master=True`, `name="master"`, `parent_id=None`, `CloneStatus.NOT_CLONED`) + `GoalPlan` row if absent; returns existing master otherwise; does **not** create horizon constraints.
- **`AppSettingsService.get_settings()`** — if singleton row missing, insert defaults inside transaction (lazy bootstrap), then return DTO.
- **`AppSettingsService.update_settings(...)`** — keyword-only optional fields; validate positive ints and `local_timezone` via `zoneinfo.ZoneInfo`; stamp `updated_at` via `Clock`; return updated DTO.
- **`MasterHorizonService.refresh_master_horizon(run_started_at)`** — validate `run_started_at` (UTC + minute-aligned, reject not truncate); call `MasterPlanService.ensure_master_exists()` and load settings (bootstrap via `get_settings`); upsert **one** `SYSTEM_MASTER_HORIZON` group on master; replace its windows with single `[run_started_at, run_started_at + duration)` window; return `MasterHorizonDTO`.
- **Slice checks:** slices 1–4 → ruff format, ruff check, pyright only; slice 5 adds pytest + Test catalog.
- **Test DB:** temp-file SQLite, import all model modules, `Base.metadata.create_all(engine)` (same pattern as schema tests; avoid `:memory:` FK isolation pitfalls).
- **Service construction:** `__init__(self, session: Session, clock: Clock | None = None)`; mutating methods perform persistence reads and writes inside `with transaction(self._session)` ([repo convention §2](../../.cursor/repo_conventions.md)).

## Slices

### Slice 1: Service test fixtures and transaction test helpers

**Objective:** Add shared pytest infrastructure for service-layer integration tests (schema + session + clock).

**Files expected to change:**
- [`tests/services/__init__.py`](../../tests/services/__init__.py) (new, empty)
- [`tests/services/conftest.py`](../../tests/services/conftest.py) (new)
- [`tests/services/test_fixtures_smoke.py`](../../tests/services/test_fixtures_smoke.py) (new — minimal smoke proving fixtures work)

**May also change:**
- [`tests/conftest.py`](../../tests/conftest.py) only if a one-line pytest path hook is required (prefer keeping fixtures local to `tests/services/`).

**Implementation steps:**
1. Create `tests/services/conftest.py` with:
   - `service_db_url` / `service_db_engine` fixture — temp-directory SQLite file URL.
   - Import all ORM modules (`plans`, `chains`, `constraints`, `repetitions`, `calendar`, `free_time`, `runs`, `settings`) so mappers register; `Base.metadata.create_all(engine)`.
   - `service_db_session` fixture — `create_session_factory(engine)()` with teardown `session.close()`.
   - `fake_clock` fixture — frozen `Clock` implementation returning configurable UTC instant.
   - `service_transaction` helper or fixture documenting pattern: `with transaction(session) as txn:` for service calls under test.
2. Add smoke test(s) verifying engine connects, FK pragma enabled, empty DB has no master/settings rows, transaction helper commits.
3. No production service code in this slice.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/services/test_fixtures_smoke.py -m "not slow and not failure_expected"
```

**Acceptance criteria:**
- Fixtures create full V1 schema on temp SQLite without Alembic.
- Smoke tests pass; chat report includes **Test catalog** for new helpers/fixtures tests.

**Risks/edge cases:**
- Must import every models module before `create_all` or FK tables are missing.
- Do not share ORM row factories with schema tests yet — inline helpers in service tests come in slice 5.

---

### Slice 2: MasterPlanService

**Objective:** Implement `MasterPlanService.ensure_master_exists()` and `GoalPlanDTO`.

**Files expected to change:**
- [`calendar_backend/domain/dtos.py`](../../calendar_backend/domain/dtos.py) (new — `GoalPlanDTO` only in this slice)
- [`calendar_backend/services/__init__.py`](../../calendar_backend/services/__init__.py) (new)
- [`calendar_backend/services/master_plan.py`](../../calendar_backend/services/master_plan.py) (new)

**Implementation steps:**
1. Add frozen `GoalPlanDTO` with fields listed in locked assumptions; map from `Plan` + `GoalPlan` ORM rows.
2. Implement `MasterPlanService`:
   - Query master via `Plan.is_master.is_(True)` (partial unique index enforces at most one).
   - If found: map to DTO, return `ok(dto)`.
   - If absent: insert `Plan` + `GoalPlan` with design locked fields; UUID via `new_id(PlanID)`; timestamps from `Clock`; return `ok(dto)`.
   - Wrap mutation path in `transaction(session)`.
3. Keep module free of horizon/settings logic.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- `ensure_master_exists()` is idempotent (second call returns same `plan_id`).
- Created master satisfies ORM CHECK `master_is_goal` and partial unique master index.
- Returns `ServiceResult[GoalPlanDTO]`; no bare exceptions across public API for expected paths.

**Risks/edge cases:**
- Concurrent first-create could race on partial unique index — acceptable for V1 solo use; document as known limitation.
- Do not add `PlanTreeInvariantService` calls here.

---

### Slice 3: AppSettingsService

**Objective:** Implement full `AppSettingsService` (`get_settings`, `update_settings`) with module-level bootstrap defaults.

**Files expected to change:**
- [`calendar_backend/domain/dtos.py`](../../calendar_backend/domain/dtos.py) (add `AppSettingsDTO`)
- [`calendar_backend/services/app_settings.py`](../../calendar_backend/services/app_settings.py) (new)

**Implementation steps:**
1. Add `DEFAULT_*` constants at module top in `app_settings.py` (repo convention §1).
2. Add frozen `AppSettingsDTO` mirroring persisted [`AppSettings`](../../calendar_backend/models/settings.py) fields exposed to callers.
3. Implement `AppSettingsService`:
   - `get_settings()` — within transaction, select singleton row; if missing insert row with defaults + `singleton_id=1` + `updated_at=clock.now_utc()`; return DTO.
   - `update_settings(...)` — keyword-only optional params for each editable field; load row (bootstrap if needed); validate:
     - positive integer checks for duration/limits where applicable
     - `local_timezone` parses via `zoneinfo.ZoneInfo`
     - `free_time_week_start_day` is valid enum
   - Apply changes; set `updated_at`; return `ok(AppSettingsDTO)`.
4. Map validation failures to `ServiceResult` failures with appropriate `MessageCode` values (reuse existing codes where possible).

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- `get_settings()` bootstraps exactly one row with defaults on empty DB.
- `update_settings` is the only service path that mutates persisted settings.
- Returns `ServiceResult[AppSettingsDTO]` for both methods.

**Risks/edge cases:**
- `singleton_id` CHECK prevents second row — rely on bootstrap inserting only when missing.
- Do not add HTTP/env-based configuration overrides in this slice.

---

### Slice 4: MasterHorizonService

**Objective:** Implement `MasterHorizonService.refresh_master_horizon(run_started_at)` and `MasterHorizonDTO`.

**Files expected to change:**
- [`calendar_backend/domain/dtos.py`](../../calendar_backend/domain/dtos.py) (add `MasterHorizonDTO`)
- [`calendar_backend/services/master_horizon.py`](../../calendar_backend/services/master_horizon.py) (new)

**May also change:**
- [`calendar_backend/services/master_plan.py`](../../calendar_backend/services/master_plan.py) / [`app_settings.py`](../../calendar_backend/services/app_settings.py) — only if a minimal shared internal import is needed (prefer constructing sibling services with same `session` + `clock`).

**Implementation steps:**
1. Add frozen `MasterHorizonDTO`.
2. Implement `MasterHorizonService.refresh_master_horizon(run_started_at: datetime)`:
   - Validate `run_started_at`: `require_utc` + `is_minute_aligned`; map failures to `ServiceMessage` (`INVALID_TIME_WINDOW` / `NON_MINUTE_ALIGNED_WINDOW`).
   - Within transaction: call `MasterPlanService.ensure_master_exists()` and `AppSettingsService.get_settings()` on same session.
   - Compute `horizon_end = run_started_at + timedelta(minutes=settings.master_horizon_duration_minutes)`.
   - Locate existing `TimeConstraintGroup` for master plan with `constraint_kind=SYSTEM_MASTER_HORIZON`; if absent create group; delete/replace existing windows so group has **exactly one** window `[run_started_at, horizon_end)`.
   - Return `ok(MasterHorizonDTO(...))`.
3. Do not expose user constraint editing; do not implement `TimeConstraintService` guards here.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- Refresh creates/updates single system horizon group + window on master plan.
- Re-running refresh replaces window bounds (does not accumulate groups/windows).
- Invalid `run_started_at` returns failed `ServiceResult` without persisting partial changes.
- Horizon end uses settings duration at refresh time.

**Risks/edge cases:**
- If multiple stale `SYSTEM_MASTER_HORIZON` groups exist (manual DB edit), partial unique index on `(plan_id)` WHERE `constraint_kind = SYSTEM_MASTER_HORIZON` rejects a second row (ORM in place; Alembic migration follow-up). Service no longer deletes duplicate groups.
- Window must satisfy ORM CHECK `start_time < end_time` — ensure duration > 0 (validate settings on bootstrap/update).

---

### Slice 5: Invariant and service tests

**Objective:** Add pytest coverage for all service behavior introduced in slices 2–4.

**Files expected to change:**
- [`tests/services/test_master_plan_service.py`](../../tests/services/test_master_plan_service.py) (new)
- [`tests/services/test_app_settings_service.py`](../../tests/services/test_app_settings_service.py) (new)
- [`tests/services/test_master_horizon_service.py`](../../tests/services/test_master_horizon_service.py) (new)
- [`tests/services/test_foundational_invariants.py`](../../tests/services/test_foundational_invariants.py) (new — cross-service bootstrap/refresh flows)

**Implementation steps:**
1. **`test_master_plan_service.py`:** idempotent ensure; master field invariants (`name`, `is_master`, `parent_id`, `plan_kind`); returns `GoalPlanDTO`.
2. **`test_app_settings_service.py`:** bootstrap defaults match `app_settings.py` module constants; get after bootstrap; update each field; reject bad timezone / non-positive limits; `updated_at` advances with `FakeClock`.
3. **`test_master_horizon_service.py`:** refresh after bootstrap; window bounds; single group/window; second refresh replaces bounds; reject naive/non-minute `run_started_at`; horizon end tracks updated `master_horizon_duration_minutes`.
4. **`test_foundational_invariants.py`:** empty DB → ensure master + get settings + refresh horizon succeeds; SYSTEM constraint visible on master via ORM navigation; note Prompt 7 will add user-edit rejection.
5. Mark integration tests `@pytest.mark.integration` where they use engine/session boundaries.
6. Post **Test catalog** in chat per guide §9.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/services/ -m "not slow and not failure_expected"
```

**Acceptance criteria:**
- All new tests pass; existing suite still green.
- Tests cover **all** public behavior from slices 2–4 (implementation-chunk coverage rule).
- Chat report includes grouped **Test catalog**.

**Risks/edge cases:**
- Tests must use slice 1 fixtures; avoid depending on local `local_data/calendar_backend.sqlite3`.
- Do not import Prompt 7 services — stub/document deferred system-constraint edit rejection.

## Abstraction check

| Introduced item | Needed now? | Justification |
|-----------------|-------------|---------------|
| `MasterPlanService` | Yes | Design §7 named service with single responsibility |
| `AppSettingsService` | Yes | Design §7 sole settings mutation path |
| `MasterHorizonService` | Yes | Design §7 sole system horizon writer |
| `GoalPlanDTO`, `AppSettingsDTO`, `MasterHorizonDTO` | Yes | Design §7–§8.2 public service return types |
| Module-level `DEFAULT_*` in service files | Yes | Repo convention §1; design bootstrap values colocated with mutator |
| `FakeClock` in tests | Yes | Testing seam for deterministic timestamps (abstraction rule #4) |
| Repository / DAO / service base class | No | Design: services use `Session` directly |
| Generic `BootstrapService` | No | Two ensure/bootstrap paths remain explicit |
| `TimeConstraintService` stub | No | Prompt 7 |

## Dependency changes

None expected — stdlib `zoneinfo` only; existing `sqlalchemy` stack already present.

```bash
uv sync   # if fresh clone only
```

## Open questions

None blocking implementation.
