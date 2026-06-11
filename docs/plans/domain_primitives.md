# Plan: Core domain primitives

**Finalized plan location:** `docs/plans/domain_primitives.md`

## Context

Implement the pure domain foundation for `calendar_backend` per [docs/calendar_backend_v1_engineering_design_updated.pdf](../calendar_backend_v1_engineering_design_updated.pdf) §4 (package layout), §8.1 (ServiceResult), §11–§12 (time/error rules), Appendix §11–§12, and [docs/cursor_implementation_guide.md](../cursor_implementation_guide.md) Prompt 3.

Design-doc constraints:
- [`calendar_backend/domain/`](../../calendar_backend/domain/) owns **NewType IDs, enums, errors, time dataclasses/helpers, clock protocol, and ServiceResult**; no SQLAlchemy sessions or persistence logic (design §4).
- **Frozen dataclasses** where practical for value/result types (design Appendix §11).
- **Time rules:** timezone-aware UTC for persisted timestamps; integer minutes for durations/granularity; minute-aligned window boundaries; half-open intervals `[start, end)`; **no sub-minute scheduling**; invalid non-minute values are rejected, not rounded (Appendix §12).
- **`Clock` protocol + injectable implementation** so core services avoid direct `datetime.now()` (design §11, Appendix §11).
- **Service methods** (later prompts) return `ServiceResult[T]` with structured errors/warnings (design §7, §8.1, §12).

Current repo state:
- Database infrastructure plan is complete ([`docs/plans/database_infrastructure.md`](database_infrastructure.md)): `db/base.py`, `db/session.py`, Alembic wiring, `tests/db/test_session.py`.
- [`calendar_backend/domain/__init__.py`](../../calendar_backend/domain/__init__.py) exists and is empty.
- No domain modules yet under `calendar_backend/domain/`.

Build workflow: use `/build-plan-slice` per slice against this file; stop after each slice for approval.

## Non-goals

- ORM models ([`calendar_backend/models/`](../../calendar_backend/models/)) — Prompts 4–5.
- Service-layer behavior, transactions, or `ServiceResult`-returning public APIs — Prompt 6+.
- Large resolution/assignment DTOs (`ResolvedTask`, `AssignmentResult`, `DeletionPreview`, etc.) — later prompts per design §8.2.
- AND-of-OR constraint group editing, OR-window merge/normalization, or `TimeConstraintService` — Prompt 7.
- `InvariantValidationResult` / tree invariant diagnostics — Prompt 6–7.
- Pydantic or HTTP/API serialization layers — deferred per design Appendix §11.
- OR-Tools or scheduling code.

## Locked assumptions

- **File layout** matches design §4 exactly:
  - [`calendar_backend/domain/ids.py`](../../calendar_backend/domain/ids.py)
  - [`calendar_backend/domain/enums.py`](../../calendar_backend/domain/enums.py)
  - [`calendar_backend/domain/errors.py`](../../calendar_backend/domain/errors.py)
  - [`calendar_backend/domain/time.py`](../../calendar_backend/domain/time.py)
  - [`calendar_backend/domain/results.py`](../../calendar_backend/domain/results.py)
- **ID naming:** `PlanID = NewType("PlanID", UUID)` style with capital `ID` (Appendix §11).
- **Slice 1–4 checks:** ruff format, ruff check, pyright only (no pytest until slice 5).
- **Slice 5 tests** cover all modules from slices 1–4, with extra depth on validation/time behavior.
- **No new runtime dependencies** — stdlib only (`uuid`, `datetime`, `enum`, `dataclasses`, `typing`).
- **Minimal `domain/__init__.py` re-exports** only if consistent with existing `db/__init__.py` style; prefer explicit submodule imports in consumers.

## Slices

### Slice 1: ID NewTypes and UUID helpers

**Objective:** Add all V1 UUID identity NewTypes and small parse/generate helpers.

**Files expected to change:**
- [`calendar_backend/domain/ids.py`](../../calendar_backend/domain/ids.py) (new)
- [`calendar_backend/domain/__init__.py`](../../calendar_backend/domain/__init__.py) (optional minimal re-exports)

**Implementation steps:**
1. Define NewTypes for every UUID PK/FK identity in design §6 data model:
   - `PlanID`, `GoalChildChainID`, `GoalChildChainItemID`, `TimeConstraintGroupID`, `TimeWindowID`, `RepetitionInstanceID`, `CalendarEntryID`, `FreeTimeActivityID`, `FreeTimeActivityPrerequisiteID`, `CalendarRunID`
2. Add helpers (names may vary, keep direct):
   - `new_uuid() -> UUID` or per-type `new_plan_id() -> PlanID` if clearer for pyright
   - `parse_uuid(value: str) -> UUID` with clear `ValueError` on invalid input
   - `as_plan_id(uuid: UUID) -> PlanID` (and similar narrow casts) where useful
3. Keep module free of SQLAlchemy, services, and business rules.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- All listed ID NewTypes exist and are distinct at the type level.
- Helpers round-trip valid UUID strings; invalid strings raise clearly.
- Strict pyright passes on `ids.py`.

**Risks/edge cases:**
- Do not use magic/master IDs; design requires generated UUIDs (Appendix §6).
- Avoid a registry/factory abstraction for ID types — plain NewTypes + functions suffice.

---

### Slice 2: Enums and error/message codes

**Objective:** Add serializable domain enums and the error/message taxonomy used by `ServiceResult`.

**Files expected to change:**
- [`calendar_backend/domain/enums.py`](../../calendar_backend/domain/enums.py) (new)
- [`calendar_backend/domain/errors.py`](../../calendar_backend/domain/errors.py) (new)

**Implementation steps:**
1. In `enums.py`, add V1 enums from design §6 / service signatures (use `StrEnum` or stdlib `Enum` with string values for serialization):
   - `PlanKind`, `CloneStatus`, `RepeatMode`, `ConstraintKind`, `CalendarEntryType`
   - Run/status enums: `CalendarRunStatus`, `SolverStatus`, `LastFailureReason` (nullable cases handled at use sites)
   - Settings-related: `FreeTimeWeekStartDay` (default Monday per design §11)
   - Any assignment-status enum needed by later DTOs only if referenced by message codes — otherwise defer
2. In `errors.py`:
   - `MessageCode` enum covering design §12 categories and §9.5 conflict codes (validation, precondition, conflict, solver warning examples listed in §12)
   - `@dataclass(frozen=True) class ServiceMessage` with at least `code: MessageCode` and `message: str` (optional `details: Mapping[str, str]` if helpful and kept minimal)
   - Programmer/domain exceptions: `WrongPlanTypeError` (and a small base like `DomainError` only if it removes duplication)
3. Do not implement service validation logic here — codes and types only.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- Enums cover the core persisted enum columns needed by upcoming ORM plans.
- `MessageCode` includes the §12 example codes (validation, precondition, conflict, solver warning families).
- `ServiceMessage` and exception types import cleanly from domain layer.

**Risks/edge cases:**
- Enum string values should match future Alembic/ORM storage representation; use **UPPER_SNAKE** matching design doc examples like `INVALID_DURATION`.
- Do not add HTTP status mapping or i18n layers.

---

### Slice 3: Time window dataclass, UTC/minute helpers, clock abstraction

**Objective:** Add domain time value types and helpers enforcing V1 time invariants, plus an injectable clock.

**Files expected to change:**
- [`calendar_backend/domain/time.py`](../../calendar_backend/domain/time.py) (new)

**Implementation steps:**
1. Add frozen `@dataclass` `TimeWindow` with `start_time: datetime`, `end_time: datetime` representing half-open `[start, end)` (design §5.5, Appendix §5).
2. Add pure helpers (functions, not extra classes):
   - `require_utc(dt: datetime) -> datetime` — reject naive datetimes
   - `truncate_to_minute(dt: datetime) -> datetime` — zero seconds/microseconds (for normalization helpers only; design says invalid non-minute values are rejected, not silently rounded in validation paths)
   - `is_minute_aligned(dt: datetime) -> bool`
   - `validate_time_window(window: TimeWindow) -> None` raising `ValueError` when:
     - times not UTC-aware
     - not minute-aligned
     - `start_time >= end_time`
3. Add `Protocol` class `Clock` (or `ClockService` name per design) with `now_utc() -> datetime`.
4. Add concrete `SystemClock` implementing the protocol using timezone-aware UTC `datetime.now(UTC)`.
5. **Do not** implement multi-group `List[List[TimeWindow]]` merge/normalization — deferred to Prompt 7.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- `TimeWindow` is a frozen dataclass with documented half-open semantics.
- Helpers enforce UTC + minute alignment rules from Appendix §12.
- `Clock` protocol and `SystemClock` exist; no SQLAlchemy imports.

**Risks/edge cases:**
- Distinguish **validation** (reject bad input) from **normalization** (truncate) — document which helpers do which; validation paths must not silently accept sub-minute values.
- `TimeWindow` domain type is separate from ORM `TimeWindow` rows (Prompt 5); no naming collision in models until ORM plan aliases if needed.

---

### Slice 4: ServiceResult and common result helpers

**Objective:** Implement the standard service return envelope per design §8.1.

**Files expected to change:**
- [`calendar_backend/domain/results.py`](../../calendar_backend/domain/results.py) (new)
- [`calendar_backend/domain/__init__.py`](../../calendar_backend/domain/__init__.py) (optional re-exports of `ServiceResult`, `ServiceMessage`)

**Implementation steps:**
1. Implement frozen generic dataclass matching design §8.1:
   ```python
   @dataclass(frozen=True)
   class ServiceResult(Generic[T]):
       success: bool
       value: T | None = None
       errors: tuple[ServiceMessage, ...] = ()
       warnings: tuple[ServiceMessage, ...] = ()
       metadata: Mapping[str, Any] = field(default_factory=dict)
   ```
2. Import `ServiceMessage` from `errors.py` (avoid circular imports — `errors.py` must not import `results.py`).
3. Add small factories only if they reduce duplication without becoming pass-through wrappers:
   - e.g. `ok(value: T, *, warnings=..., metadata=...) -> ServiceResult[T]`
   - e.g. `fail(*errors: ServiceMessage, metadata=...) -> ServiceResult[T]` (or untyped `ServiceResult[None]`)
4. No service-specific result DTOs in this slice.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- `ServiceResult` matches design §8.1 fields and is immutable.
- Factory helpers construct consistent success/failure results.
- No SQLAlchemy or service imports in `results.py`.

**Risks/edge cases:**
- Generic pyright typing for `fail()` — use overloads or `ServiceResult[None]` pattern that strict mode accepts.
- Keep `metadata` as plain `Mapping[str, Any]` per design; do not introduce a Metadata DTO.

---

### Slice 5: Domain validation and time helper tests

**Objective:** Add pytest coverage for slices 1–4, emphasizing time validation and result conventions.

**Files expected to change:**
- [`tests/domain/__init__.py`](../../tests/domain/__init__.py) (new, empty)
- [`tests/domain/test_ids.py`](../../tests/domain/test_ids.py) (new)
- [`tests/domain/test_enums_errors.py`](../../tests/domain/test_enums_errors.py) (new, or split if clearer)
- [`tests/domain/test_time.py`](../../tests/domain/test_time.py) (new)
- [`tests/domain/test_results.py`](../../tests/domain/test_results.py) (new)
- optionally [`tests/domain/conftest.py`](../../tests/domain/conftest.py) for shared `FakeClock` if not inlined

**Implementation steps:**
1. Create `tests/domain/` package mirroring [`tests/db/`](../../tests/db/) layout.
2. **IDs:** valid/invalid UUID parse; NewType cast helpers; generated IDs are unique.
3. **Enums/errors:** enum values stable; `ServiceMessage` frozen; sample `MessageCode` members exist for each §12 category.
4. **Time (primary focus):**
   - `require_utc` rejects naive datetimes
   - minute alignment detection
   - `validate_time_window` rejects non-minute-aligned, inverted, and equal start/end windows
   - accepts valid half-open UTC minute-aligned window
   - `FakeClock` (test-only) returns fixed UTC instant; `SystemClock` smoke test optional
5. **Results:** `ok()`/`fail()` produce expected `success`, `value`, `errors`, `warnings`, `metadata` immutability.
6. No database, Alembic, or ORM dependencies.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -m "not slow and not failure_expected"
```

**Acceptance criteria:**
- Pytest collects and passes all new domain tests.
- Tests prove Appendix §12 time validation rules at the helper level.
- Existing [`tests/db/test_session.py`](../../tests/db/test_session.py) continues to pass.

**Risks/edge cases:**
- Use `FakeClock` with fixed instants — avoid flaky wall-clock assertions.
- Do not test service-layer rules (zero task duration, empty constraint groups) until those services exist — only what slice 3 helpers actually enforce.

## Abstraction check

| Introduced item | Needed now? | Justification |
|-----------------|-------------|---------------|
| UUID `NewType` aliases | Yes | Design Appendix §11; prevents ID mix-ups across services |
| `MessageCode` / `ServiceMessage` | Yes | Design §8.1, §12 structured failures |
| `TimeWindow` frozen dataclass | Yes | Shared half-open interval semantics (design §5.5) |
| UTC/minute helper functions | Yes | Centralize Appendix §12 time rules before services |
| `Clock` Protocol | Yes | Design §11 injectable clock; testing seam (abstraction rule #4) |
| `SystemClock` | Yes | Default production implementation of required protocol |
| `ServiceResult[T]` | Yes | Design §8.1 universal service envelope |
| `ok()` / `fail()` factories | Maybe | Allowed only if they remove repeated boilerplate without hiding logic |
| `DomainError` base class | Maybe | Only if multiple exceptions share behavior |
| Separate `clock.py` module | No | Design places clock in `time.py` |
| ID registry / enum registry | No | No variation exists yet |

No scheduling interfaces, ORM adapters, or service factories in this plan.

## Dependency changes

None expected.

```bash
uv sync   # if fresh clone only
```

## Open questions

None blocking implementation.

## Changed in this revision

- Finalized plan to [`docs/plans/domain_primitives.md`](domain_primitives.md) (was draft-only in `~/.cursor/plans/`).
- Added **Finalized plan location** header and pointed build workflow at this file directly.
- Removed draft **Finalization** pending-approval section.
- Locked enum storage convention to **UPPER_SNAKE** in slice 2 (fixed draft typo).
- Clarified slice 3 `validate_time_window` raises `ValueError` at domain layer (not ServiceMessage return).
