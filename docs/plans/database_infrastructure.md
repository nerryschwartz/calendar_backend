# Plan: Database infrastructure

**Finalized plan location:** `docs/plans/database_infrastructure.md`

## Context

Implement the persistence foundation for `calendar_backend` per [docs/calendar_backend_v1_engineering_design_updated.pdf](../calendar_backend_v1_engineering_design_updated.pdf) and [docs/cursor_implementation_guide.md](../cursor_implementation_guide.md) Prompt 2.

Design-doc constraints:
- [`calendar_backend/db/`](../../calendar_backend/db/) owns metadata base, session factories, and migrations; no domain scheduling logic (design doc §4 package layout).
- [`calendar_backend/db/session.py`](../../calendar_backend/db/session.py) owns **session maker, transaction helpers, and engine creation** as transaction entry points (design doc §4).
- Mutating services will run inside **atomic transactions** later; this plan only provides the db-layer entry point (design doc §10).
- **Alembic migration files** are normal from project start, but **no application tables** exist yet in this plan.
- SQLite for V1 with Postgres-friendly schema choices; **foreign keys must be enforced** at connection time (guide §8.10, §8.12).

Current repo state:
- Package skeleton exists under [`calendar_backend/`](../../calendar_backend/) with empty package `__init__.py` files.
- [`calendar_backend/db/__init__.py`](../../calendar_backend/db/__init__.py) exists; no `base.py`, `session.py`, migrations, or `tests/` yet.
- Dependencies `sqlalchemy` and `alembic` already in [`pyproject.toml`](../../pyproject.toml).
- Orphan [`src/calendar_backend/__init__.py`](../../src/calendar_backend/__init__.py) is not imported by `uv run python`; leave untouched unless it causes confusion during build.

Build workflow: use `/build-plan-slice` per slice against this file; stop after each slice for approval.

## Non-goals

- Application ORM models ([`calendar_backend/models/`](../../calendar_backend/models/)) beyond empty package wiring.
- Initial Alembic **table** migration or autogenerate revision (deferred until ORM model plans).
- Domain primitives (ClockService, IDs, enums, ServiceResult) — Prompt 3.
- Service-layer transaction test fixtures — Prompt 6.
- Settings-based configurable database URL (defer; use shared default constant for now).
- Postgres-specific engine configuration.
- OR-Tools or scheduling code.

## Locked assumptions

- Default database URL: `sqlite:///local_data/calendar_backend.sqlite3` (guide §8.4).
- [`NAMING_CONVENTION`](../cursor_implementation_guide.md) in `base.py` matches guide §8.2 exactly.
- Single shared `Base` / `Base.metadata` for all future models.
- SQLite FK pragma registered on `Engine` connect in `session.py`; Alembic `env.py` imports that module for side effect before creating its engine.
- Transaction helper is a **thin context manager** around SQLAlchemy 2 `Session.begin()` — no ServiceResult integration in db layer.
- Alembic `env.py` sets `target_metadata = Base.metadata` only; **no model module imports** until ORM plans add those files.
- `local_data/` remains gitignored; code ensures parent directory exists before opening SQLite file path.
- Remove stale `src/calendar_backend/` only if a slice discovers packaging/import breakage; not a required slice objective.

## Slices

### Slice 1: SQLAlchemy Base and metadata conventions

**Objective:** Add declarative base and shared metadata naming conventions.

**Files expected to change:**
- [`calendar_backend/db/base.py`](../../calendar_backend/db/base.py) (new)
- [`calendar_backend/db/__init__.py`](../../calendar_backend/db/__init__.py) (optional re-exports: `Base`, `NAMING_CONVENTION`)

**Implementation steps:**
1. Create `base.py` with `NAMING_CONVENTION` and `class Base(DeclarativeBase)` using `MetaData(naming_convention=NAMING_CONVENTION)` per guide §8.2.
2. Keep file pure: no engine, session, or business logic.
3. Export `Base` from `db/__init__.py` if that matches existing package import style elsewhere (minimal re-export only).

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- `Base.metadata` exists and uses the locked naming convention keys (`ix`, `uq`, `ck`, `fk`, `pk`).
- No SQLAlchemy session/engine code in this slice.
- Strict pyright passes on new module.

**Risks/edge cases:**
- Do not create a second `Base` or metadata object anywhere.

---

### Slice 2: Session/engine helpers and SQLite FK behavior

**Objective:** Provide engine creation, session factory, shared default URL, SQLite FK enforcement, and a transaction entry-point helper.

**Files expected to change:**
- [`calendar_backend/db/session.py`](../../calendar_backend/db/session.py) (new)
- [`calendar_backend/db/__init__.py`](../../calendar_backend/db/__init__.py) (optional re-exports)

**Implementation steps:**
1. Define module-level `DEFAULT_DATABASE_URL = "sqlite:///local_data/calendar_backend.sqlite3"`.
2. Register `@event.listens_for(Engine, "connect")` handler that runs `PRAGMA foreign_keys=ON` for sqlite3 connections only.
3. Implement `create_engine_for_url(url: str = DEFAULT_DATABASE_URL) -> Engine`:
   - ensure parent directory exists for file-backed SQLite URLs
   - return configured SQLAlchemy engine
4. Implement `create_session_factory(engine: Engine) -> sessionmaker[Session]` (or typed equivalent for strict pyright).
5. Implement transaction helper, e.g. `@contextmanager def transaction(session: Session) -> Iterator[Session]` wrapping `session.begin()`.
6. Avoid importing services, models, or domain code.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```

**Acceptance criteria:**
- New sqlite3 connection reports `foreign_keys=1` when checked via `PRAGMA foreign_keys`.
- `create_engine_for_url()` can open the default local path without manual `mkdir`.
- Transaction helper commits on success and rolls back on exception.
- All db setup lives in `calendar_backend/db/session.py` per layer boundaries.

**Risks/edge cases:**
- In-memory SQLite (`:memory:`) still needs per-connection pragma; handler must not assume file paths only.
- Do not duplicate pragma logic in Alembic yet (slice 3 imports this module).

---

### Slice 3: Alembic initialization/configuration

**Objective:** Wire Alembic under `calendar_backend/db/migrations` sharing db connection behavior with the app.

**Files expected to change:**
- [`alembic.ini`](../../alembic.ini) (new)
- [`calendar_backend/db/migrations/env.py`](../../calendar_backend/db/migrations/env.py) (new)
- [`calendar_backend/db/migrations/script.py.mako`](../../calendar_backend/db/migrations/script.py.mako) (new)
- [`calendar_backend/db/migrations/versions/.gitkeep`](../../calendar_backend/db/migrations/versions/.gitkeep) or empty `versions/` directory
- [`calendar_backend/db/migrations/README`](../../calendar_backend/db/migrations/README) (generated by alembic init, if present)

**Implementation steps:**
1. Run `uv run alembic init calendar_backend/db/migrations`.
2. Set `script_location = calendar_backend/db/migrations` in `alembic.ini`.
3. Set `sqlalchemy.url = sqlite:///local_data/calendar_backend.sqlite3` in `alembic.ini` (same default as slice 2).
4. Configure `env.py`:
   - `from calendar_backend.db.base import Base`
   - `import calendar_backend.db.session  # noqa: F401` to register FK pragma before engine creation
   - `target_metadata = Base.metadata`
   - **Do not import** `calendar_backend.models.*` yet
   - Prefer reusing `create_engine_for_url()` from `session.py` for online migrations when practical
5. Leave `versions/` empty (no baseline table migration).
6. Verify CLI wiring:
   - `uv run alembic current` succeeds on fresh DB
   - `uv run alembic history` shows empty chain

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run alembic current
uv run alembic history
```

**Acceptance criteria:**
- Alembic files are committed paths under `calendar_backend/db/migrations/`.
- `env.py` sees empty but valid `Base.metadata` (no missing import errors).
- App and Alembic share FK-on-connect behavior via shared `session.py` registration.
- No migration revision files with table DDL yet.

**Risks/edge cases:**
- Autogenerate now would produce empty migrations — expected until models exist.
- Keep migrations self-contained; no imports from services/orchestration (guide §8.12).

---

### Slice 4: Database infrastructure tests

**Objective:** Add minimal pytest coverage for engine/session/FK/transaction behavior.

**Files expected to change:**
- [`tests/db/test_session.py`](../../tests/db/test_session.py) (new)
- [`tests/db/__init__.py`](../../tests/db/__init__.py) (new, empty)
- optionally [`tests/conftest.py`](../../tests/conftest.py) only if needed for shared db fixtures in this plan

**Implementation steps:**
1. Create `tests/db/` package.
2. Add focused tests:
   - engine creation succeeds (prefer temp-file SQLite or `:memory:` as appropriate)
   - `PRAGMA foreign_keys` is enabled on connections from project engine factory
   - transaction helper commits persisted changes
   - transaction helper rolls back on exception
   - optional: FK violation test using minimal inline `Table` metadata on `Base` in test module **or** temporary mapped class defined only in test file — only if needed to prove enforcement; do not add production models
3. Mark integration-style tests with `@pytest.mark.integration` if they touch real engine/session boundaries.
4. Keep fixtures minimal; defer service transaction helpers to Prompt 6.

**Tests/checks:**
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -m "not slow and not failure_expected"
```

**Acceptance criteria:**
- Pytest collects and passes new db infrastructure tests.
- Tests validate FK pragma and transaction helper behavior, not business rules.
- No dependency on Alembic revisions or application ORM models.

**Risks/edge cases:**
- FK enforcement test requires at least two related tables; keep definitions test-local to avoid polluting production models.
- If `:memory:` is used, each connection is isolated — structure tests accordingly.

## Abstraction check

| Introduced item | Needed now? | Justification |
|-----------------|-------------|---------------|
| `Base` (`DeclarativeBase`) | Yes | Required ORM/metadata root per design doc |
| `create_engine_for_url()` | Yes | Single engine setup path shared by app/tests/Alembic |
| `create_session_factory()` | Yes | Explicit sessionmaker setup for services/tests later |
| `transaction()` context manager | Yes | Design doc transaction entry point in `session.py` |
| Engine connect event for SQLite pragma | Yes | Required SQLite correctness (guide §8.12) |
| Separate `sqlite_pragmas.py` module | No | Not justified until a second consumer beyond `session.py` exists |

No protocols, registries, factories-with-one-implementation, or service adapters in this plan.

## Dependency changes

None expected — `sqlalchemy` and `alembic` are already in [`pyproject.toml`](../../pyproject.toml).

If Alembic init is run before deps are synced on a fresh clone:
```bash
uv sync
```

## Open questions

None blocking implementation.

## Changed in this revision

- Moved finalized plan storage to repo-local [`docs/plans/database_infrastructure.md`](database_infrastructure.md).
- Draft plans use `~/.cursor/plans/`; build slices against finalized plans in `docs/plans/`.
- Adjusted internal markdown links to be relative to `docs/plans/`.
