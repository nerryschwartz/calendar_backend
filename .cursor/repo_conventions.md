# Repository code conventions

These conventions take precedence over `docs/calendar_backend_v1_engineering_design_updated.pdf`, `docs/cursor_implementation_guide.md`, finalized plans in `docs/plans/`, and existing code.

Add or change conventions only via [`/add-repo-convention`](commands/add-repo-convention.md).

---

## 1. Colocate service bootstrap defaults with the mutating service

**Scope:** Service-layer bootstrap and persisted-setting defaults only (not `db/`, domain, or test fixtures).

**Rule:** Define `DEFAULT_*` and similar bootstrap constants in the same module as the service that inserts or updates those persisted values. Do not create separate defaults/config packages for service bootstrap values.

**Examples:**
- `MASTER_PLAN_NAME` in `calendar_backend/services/master_plan.py`
- `DEFAULT_LOCAL_TIMEZONE`, `DEFAULT_MASTER_HORIZON_DURATION_MINUTES`, etc. in `calendar_backend/services/app_settings.py`

**Supersedes:** Engineering design PDF §4 “static defaults package” for service bootstrap defaults; finalized plans that reference `calendar_backend/settings/defaults.py`.

---

## 2. No pre-transaction persistence reads in mutating service methods

**Scope:** Service methods that mutate persistence (insert, update, delete) inside `transaction(session)`.

**Rule:** Do not read from `self._session` (or the injected session) before entering `with transaction(self._session)`. Perform reads inside the transaction block, including idempotency checks.

**Rationale:** Avoids split-brain between a pre-transaction read and transactional writes; keeps bootstrap/mutate paths consistent.

**Example:** `MasterPlanService.ensure_master_exists()` selects the master plan inside `transaction(...)`, not on `self._session` first.

**Does not apply to:** Read-only service methods that never mutate persistence in that call.
