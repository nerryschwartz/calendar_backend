# calendar_backend Cursor Implementation Guide

Recommended location in the repo: `docs/cursor_implementation_guide.md`

This guide is for implementing `calendar_backend` from a brand-new empty repo using Cursor, WSL, `uv`, SQLAlchemy, Alembic, pytest, ruff, and pyright.

The updated engineering design PDF is the source of truth for architecture and behavior. This guide does not replace that design document. It turns the design into a Cursor-ready workflow: repo setup, rules, commands, scripts, an Alembic tutorial, and implementation-planning prompts.

## 0. Locked workflow decisions

Use these decisions throughout the project:

- Start from a brand-new empty repository.
- Work in WSL.
- Use `uv` for environment and dependency management.
- Use `ruff format`, `ruff check`, `pyright`, and `pytest`.
- Use SQLAlchemy ORM with SQLite and Alembic migrations.
- Keep Cursor files repo-local unless a file is explicitly described as optional/global.
- Store draft Cursor plans in `~/.cursor/plans/`.
- Store finalized plans in `docs/plans/`.
- Stop and review after each implementation slice, not after each file edit.
- Work on sequential solo branches. Merge-command tooling is intentionally omitted.
- Defer OR-Tools until the exact-solver implementation slice.
- Let implementation prompts tell Cursor to install required dependencies with `uv` when needed.
- Create a minimal `tools/dev_cli.py` placeholder early, but defer real CLI commands until services exist.
- Avoid MCPs, custom subagents, and skills unless a specific later bottleneck creates a massive benefit.
- Favor deterministic scripts over agentic repo operations whenever practical.

## 0.1 Guide vs engineering design PDF

When this guide or a finalized plan in `docs/plans/` conflicts with `docs/calendar_backend_v1_engineering_design_updated.pdf` on the topics below, **this guide and finalized plans take precedence**. The PDF is not updated for these deviations (update the PDF manually when desired).

## 0.3 Repository code conventions

**Highest precedence:** numbered conventions in [`.cursor/repo_conventions.md`](../.cursor/repo_conventions.md), enforced by [`.cursor/rules/01-repo-conventions.mdc`](../.cursor/rules/01-repo-conventions.mdc).

Add or change conventions only via [`/add-repo-convention`](../.cursor/commands/add-repo-convention.md).

When a repo convention conflicts with this guide, a finalized plan, the PDF, or existing code, **follow the convention** and update downstream docs/code per the command workflow. Do not edit the PDF in automation; record superseded PDF points here instead.

### Convention supersessions (PDF / guide)

| Topic | Superseded guidance | Repo convention |
|---|---|---|
| Service bootstrap defaults | PDF §4 separate static defaults package; guide §2.3 `calendar_backend/settings/` placeholder | **§1** — colocate `DEFAULT_*` with the mutating service module (e.g. `app_settings.py`, `master_plan.py`) |
| Pre-transaction service reads | Informal outer-read “fast path” before `transaction()` | **§2** — mutating service methods read persistence only inside `transaction(session)` |

### TimeConstraintGroup

- **No `group_order` column.** AND constraint groups are unordered for scheduling semantics (intersection is commutative). Groups are distinguished by `time_constraint_group_id` only.

### RepetitionInstance

- **`is_critical` + `sort_order`**, not PDF §6 `is_effectively_critical` alone.
- **`instance_index`** — occurrence slot for cloning, constraint shifting, and generation identity (`start_time + n * repeat_interval`). Not a priority field.
- **`is_critical`** — whether this instance counts toward repetition logical completion (analogous to `GoalChildChain.is_critical`). New instances inherit from `RepetitionPlan.default_instance_critical` at generation; `RepetitionService` may update per instance later.
- **`sort_order`** — priority within the critical or non-critical bucket under one `RepetitionPlan` (analogous to `GoalChildChain.sort_order`: separate dense 0..n-1 sequences per `(repetition_plan_id, is_critical)`). Affects resolution traversal and assignment priority; **does not** impose scheduling precedence between instances (instances still do not precedence-constrain each other).

### 0.2 ORM and slice consistency

When building ORM or schema slices, **plans guide sequencing and review — they do not cap principled wiring**.

**Defer** an ORM `relationship()` only when the **target mapped class does not exist yet**. When the target exists (same chunk or an earlier merged slice), wire navigation symmetrically with sibling patterns in the repo.

Examples:
- If `CalendarEntry` has `source_plan_id` → `relationship()` to `Plan`, then `source_free_time_activity_id` → `FreeTimeActivity` once `FreeTimeActivity` exists — not “slice 3 vs slice 4 file lists.”
- Nullable source FK pairs (`source_plan_id` / `source_free_time_activity_id`) are symmetric by design; both should be navigable when both targets exist.
- One-way leaf pointers (e.g. `GoalChildChainItem.child_plan`, `RepetitionInstance.root_clone`) are fine without a `Plan` inverse unless a slice objective requires it.

Slice **file lists** name minimum touch points. Completing obvious symmetric wiring in modules the slice already touches is **in scope**, not scope creep.

See also `.cursor/rules/30-planning-slices.mdc` and `/review-consistency` after `/review-validation` on slice builds.

## 1. How to use Cursor for this project

Use a two-stage workflow for every meaningful change:

1. Plan in Cursor Plan Mode.
2. Build one approved slice at a time.

A typical loop:

```text
1. Open Cursor in the repo.
2. Start a new branch for a logical implementation unit.
3. Use /request-questions in Plan Mode.
4. Answer questions until there are no blocking questions.
5. Use /draft-plan to create a draft plan in ~/.cursor/plans/, then finalize it in docs/plans/.
6. Use /revise-plan until the plan is acceptable.
7. Use /build-plan-slice for slice 1 only.
8. Review the diff and run checks.
9. Repeat /build-plan-slice for the next slice.
10. Use /review-abstractions if the diff feels over-engineered.
11. Use /commit-changes when the slice group is ready to commit.
```

Use the more expensive model only for ambiguity-heavy planning. Use cheaper/Auto/Composer-style execution once the plan is locked.

Suggested model split:

| Workflow stage | Suggested model | Reason |
|---|---|---|
| Ambiguous requirements, plan creation, plan revision | GPT 5.5 Medium | Best for clarification and design judgment. |
| Building approved slices | Cheaper coding model / Auto / Composer | The plan constrains the work. |
| Commit, format, test, branch cleanup | Scripts first | Deterministic and lower-token. |

## 2. Repository setup

### 2.1 Create the repo

From WSL:

```bash
mkdir calendar_backend
cd calendar_backend
git init
uv init --package --name calendar-backend
```

This creates a minimal Python package project. The import package should be named `calendar_backend`.

### 2.2 Add dependencies

Install runtime dependencies that are needed from the beginning:

```bash
uv add sqlalchemy alembic
```

Install development dependencies:

```bash
uv add --dev pytest pytest-cov ruff pyright
```

Defer OR-Tools until the exact-solver slice:

```bash
# Do not run this during initial setup.
# Run it only when implementing the exact CP-SAT solver.
uv add ortools
```

### 2.3 Recommended initial file layout

Create the directories:

```bash
mkdir -p \
  calendar_backend/db/migrations/versions \
  calendar_backend/models \
  calendar_backend/domain \
  calendar_backend/services \
  calendar_backend/scheduling \
  calendar_backend/deletion \
  calendar_backend/orchestration \
  tests \
  tools \
  scripts/cursor \
  .cursor/commands \
  .cursor/rules \
  docs/plans
```

Create placeholder package files:

```bash
touch \
  calendar_backend/__init__.py \
  calendar_backend/db/__init__.py \
  calendar_backend/models/__init__.py \
  calendar_backend/domain/__init__.py \
  calendar_backend/services/__init__.py \
  calendar_backend/scheduling/__init__.py \
  calendar_backend/deletion/__init__.py \
  calendar_backend/orchestration/__init__.py
```

Repo convention §1: service bootstrap defaults live in the mutating service module (e.g. `services/app_settings.py`), not a separate `calendar_backend/settings/` package. ORM app settings mapping is [`calendar_backend/models/settings.py`](../calendar_backend/models/settings.py).

### 2.4 Recommended `pyproject.toml`

Replace or merge your generated `pyproject.toml` with this baseline:

```toml
[project]
name = "calendar-backend"
version = "0.1.0"
description = "Python service-layer backend for task planning, scheduling, calendar assignment, and free-time allocation."
readme = "README.md"
requires-python = ">=3.13"
dependencies = [
    "alembic>=1.13",
    "sqlalchemy>=2.0",
]

[project.scripts]
calendar-backend-dev = "tools.dev_cli:main"

[dependency-groups]
dev = [
    "pyright>=1.1",
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "ruff>=0.6",
]

[tool.uv]
package = true

[tool.ruff]
line-length = 100
target-version = "py313"
src = ["calendar_backend", "tools", "tests"]

[tool.ruff.lint]
select = [
    "E",
    "F",
    "I",
    "B",
    "UP",
    "SIM",
    "PL",
    "RUF",
]
ignore = [
    "PLR0913", # service methods may need explicit parameters
]

[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = [
    "PLR2004", # magic values in tests are often clearer than named constants
]

[tool.pyright]
pythonVersion = "3.13"
typeCheckingMode = "strict"
include = ["calendar_backend", "tools", "tests"]
venvPath = "."
venv = ".venv"
reportMissingTypeStubs = "warning"
reportUnknownMemberType = "warning"
reportUnknownVariableType = "warning"
reportUnknownArgumentType = "warning"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = [
    "--strict-markers",
    "--strict-config",
]
markers = [
    "slow: tests that are too slow for the default local suite",
    "failure_expected: tests that document known unsupported/failing behavior",
    "integration: integration tests that touch multiple services or persistence boundaries",
]
```

Notes:

- `requires-python = ">=3.13"` follows the “latest stable Python” preference. If your WSL Python is behind, use the latest stable version available to you and update `target-version` / `pythonVersion`.
- `ruff` handles both formatting and import sorting, so the VS Code `isort` extension becomes optional. It is fine to keep installed, but do not let it fight `ruff`.
- If pyright is too noisy early in the project, temporarily downgrade `typeCheckingMode` to `"basic"` and make a later slice restore strict mode.

### 2.5 Minimal `.gitignore`

Create `.gitignore`:

```gitignore
# Python
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
.coverage
htmlcov/

# Virtualenv
.venv/

# Databases and local generated files
*.sqlite
*.sqlite3
*.db
local_data/

# Cursor/editor

# OS/editor noise
.DS_Store
.vscode/
```

### 2.6 Minimal `README.md`

Create `README.md`:

```markdown
# calendar_backend

Python service-layer backend for task planning, scheduling, calendar assignment, and free-time allocation.

The implementation source of truth is the updated V1 engineering design document. Finalized implementation plans live in `docs/plans/`.
```

### 2.7 Minimal `tools/dev_cli.py`

Create `tools/__init__.py`:

```python
"""Development tooling package for calendar_backend."""
```

Create `tools/dev_cli.py`:

```python
"""Thin development CLI placeholder.

Real commands should call public services once they exist. Do not put business
logic here that is not also available through the importable backend package.
"""

from __future__ import annotations


def main() -> None:
    """Run the development CLI placeholder."""
    print("calendar_backend development CLI placeholder")
```

## 3. Cursor rules

Keep always-loaded rules short. Long rules are hidden token tax. Use commands for detailed workflows.

### 3.1 `.cursor/rules/00-project-source-of-truth.mdc`

```markdown
---
description: Project source of truth and implementation boundaries
alwaysApply: true
---

The updated calendar_backend V1 engineering design document is the source of truth for architecture and behavior.

Project identity:
- Python service-layer backend named calendar_backend.
- Persistence uses SQLite through SQLAlchemy ORM and Alembic.
- Mutations go through services inside transactions.
- ORM models are persistence records, not behavior-heavy domain objects.
- DTOs/result objects use frozen dataclasses where practical.
- Scheduling components must be isolated from persistence.
- V1 prioritizes correctness, readability, deterministic behavior, and explicit invariants.

Do not implement V1 non-goals unless explicitly asked:
- No production HTTP API or mobile frontend.
- No OS notification scheduler.
- No external calendar sync.
- No recurring availability constraints.
- No undo/audit/soft-delete/history system.
- No DAG plan relationships.
- No orphan active plans.
- No mutable plan type or plan type conversion.
- No sub-minute scheduling.
```

### 3.2 `.cursor/rules/10-layer-boundaries.mdc`

```markdown
---
description: Layer ownership boundaries for calendar_backend
alwaysApply: true
---

Respect package ownership boundaries.

calendar_backend/db:
- Owns SQLAlchemy base, metadata, engine/session setup, and Alembic integration.
- Does not own business rules or scheduling algorithms.

calendar_backend/models:
- Owns SQLAlchemy table mappings and relationships.
- Models are persistence records.
- Do not put public mutation behavior or scheduling logic here.

calendar_backend/domain:
- Owns pure enums, IDs, errors, dataclasses, time helpers, DTOs, and ServiceResult.
- Does not import SQLAlchemy sessions.

calendar_backend/services:
- Owns public service methods, validation, transactions, and persistence-changing behavior.
- Services coordinate models and domain types.
- Do not put heavy optional solver dependencies here.

calendar_backend/scheduling:
- Owns assignment solver interfaces and algorithms.
- Does not import SQLAlchemy sessions or write the database.

calendar_backend/deletion:
- Owns deletion previews and conflict deletion suggestions.
- Does not execute task assignment.

calendar_backend/orchestration:
- Owns composed workflows such as refresh_schedule.
- Does not duplicate low-level service logic.

tools:
- Thin development CLI only.
- No business logic that is not callable from services.
```

### 3.3 `.cursor/rules/15-abstraction-discipline.mdc`

```markdown
---
description: Prevent unnecessary abstractions while preserving useful decomposition
alwaysApply: true
---

Abstraction discipline:
- Prefer the simplest readable implementation that satisfies current known requirements.
- Prefer extraction for readability; avoid abstraction for hypothetical flexibility.
- Do not add classes, factories, registries, strategies, protocols, adapters, or frameworks for possible future needs.
- An abstraction is allowed only when at least one of these is true:
  1. There are already two or more real implementations.
  2. The abstraction removes duplicated logic that exists now.
  3. The boundary represents a real domain concept with independent invariants.
  4. The seam is needed for testing an otherwise hard-to-control side effect.
  5. The approved plan explicitly calls for this abstraction.

Helper functions:
- One-call helper functions are allowed only when they name a meaningful domain step, isolate a side effect, or keep a function from mixing levels of detail.
- Do not create pass-through wrappers.
- Do not create classes that only hold one method and no meaningful state.
- Prefer functions and explicit parameters over classes unless object state or polymorphism is genuinely needed.
- Prefer local, direct code over extensibility hooks until variation actually exists.
- If introducing an abstraction, explain why it is necessary now.
```

### 3.4 `.cursor/rules/20-testing-and-checks.mdc`

```markdown
---
description: Testing and check expectations
alwaysApply: true
---

Testing expectations:
- Add or update tests with each implementation slice when behavior changes.
- Invariant tests should come before complex scheduling implementation.
- Prefer small service/invariant tests over broad end-to-end tests until the service layer is stable.
- Use integration tests for refresh_schedule and cross-service persistence behavior.
- Do not silently skip failing tests.
- If a check fails during a command that is not explicitly a fix command, report the failure and stop.

Default local check sequence:
1. uv run ruff format .
2. uv run ruff check .
3. uv run pyright
4. uv run pytest -m "not slow and not failure_expected"
```

### 3.5 `.cursor/rules/30-planning-slices.mdc`

```markdown
---
description: Planning and slice discipline
alwaysApply: true
---

For implementation plans:
- Draft plans live in ~/.cursor/plans/.
- Finalized plans live in docs/plans/.
- Each plan must be split into small, reviewable slices.
- Stop after each slice and wait for approval before building the next slice.
- Do not stop after each individual file edit.
- Each slice must include:
  - objective
  - files expected to change
  - implementation steps
  - tests/checks
  - acceptance criteria
  - risks/edge cases
- Do not broaden scope during build.
- If implementation reveals a need to change the approved plan, stop and ask.
- If additional dependencies are necessary, install them with uv as part of the relevant slice.
```

## 4. Cursor commands

Commands go in `.cursor/commands/`.

### 4.1 `.cursor/commands/request-questions.md`

Use this in Plan Mode before drafting or revising a plan.

```markdown
You are in clarification mode.

Goal:
Before writing or revising an implementation plan, identify unresolved questions, edge cases, risks, and infeasibilities.

Rules:
- Do not edit files.
- Do not create a plan yet.
- Do not replace an existing plan.
- Ask only questions that could materially change the implementation.
- Keep the question list concise.
- Prefer at most 3 blocking questions.
- Use your best judgment for minor details.
- If I answer with a clarifying question, answer it briefly, then re-ask the still-relevant question.
- If no material ambiguity remains, say: "No blocking questions remain. Ready to draft or revise the plan."

Output format:
1. Blocking questions
2. Non-blocking concerns
3. Safe assumptions
```

### 4.2 `.cursor/commands/draft-plan.md`

Use this after questions are resolved.

```markdown
Draft a Cursor implementation plan.

Inputs:
- The updated calendar_backend V1 engineering design document is the source of truth.
- Use the active conversation instructions and any locked decisions.
- Store draft plans in ~/.cursor/plans/.
- After approval, save the finalized plan to docs/plans/.
- Do not edit source code.

Plan requirements:
- Make the plan more granular than the high-level implementation roadmap.
- Split the plan into small slices.
- Each slice should be buildable and reviewable independently.
- Stop after each slice during implementation.
- Do not introduce speculative abstractions.
- If a dependency is needed for a slice, include the uv command to install it in that slice.
- Defer OR-Tools until the exact-solver slice.

Plan format:
# Plan: <short name>

## Context
Summarize the requested change and the relevant design-doc constraints.

## Non-goals
List what this plan intentionally does not implement.

## Locked assumptions
List assumptions that should not be changed during build without asking.

## Slices
For each slice:

### Slice <number>: <name>
Objective:
Files expected to change:
Implementation steps:
Tests/checks:
Acceptance criteria:
  - For test-creation slices: post a **Test catalog** in chat after build (see §9 Test-creation slice convention).
Risks/edge cases:

## Abstraction check
List any new classes, protocols, factories, registries, strategy objects, adapters, or helper layers the plan introduces.
For each one, justify why it is needed now.
If an abstraction is only for possible future flexibility, remove it from the plan.

## Dependency changes
List uv add / uv add --dev commands, if any.

## Open questions
Only include questions that block implementation.
```

### 4.3 `.cursor/commands/revise-plan.md`

Use this when a plan already exists.

```markdown
Revise the existing implementation plan. Do not create a new plan unless I explicitly say to start over.

Instructions:
- Locate the current active plan in docs/plans/ if finalized, or in ~/.cursor/plans/ if still drafting.
- Preserve the plan's structure unless the structure itself is the problem.
- Apply my requested changes as a patch to the existing plan.
- If the requested change conflicts with an earlier locked decision or the design document, ask before changing it.
- Keep the plan split into small implementation slices.
- Do not edit source code.

At the end of the plan, include:

## Changed in this revision
- Concise bullets describing what changed.
```

### 4.4 `.cursor/commands/build-plan-slice.md`

Use this in Agent mode after a plan and slice are approved.

```markdown
Build exactly one approved plan slice.

Before editing:
- Identify the active finalized plan file in docs/plans/.
- Identify the exact slice to build.
- If the slice is ambiguous, ask one concise blocking question and stop.
- Do not build future slices.
- Do not broaden scope.
- If implementation reveals the approved plan is wrong or incomplete, stop and ask.

Abstraction constraints:
- Do not introduce new abstraction layers beyond those named in the approved plan.
- Do not add new classes/protocols/factories/registries unless the slice explicitly calls for them or implementation reveals an immediate need.
- If implementation reveals a need for a new abstraction not in the plan, stop and ask before adding it.
- Prefer direct functions and explicit data flow.

Dependency constraints:
- Use uv to add dependencies required by this slice.
- Do not add optional future dependencies early.
- Defer OR-Tools until the exact-solver slice.

After editing:
- Run the narrowest relevant checks for this slice.
- If the slice changes shared infrastructure or public behavior, run:
  uv run ruff format .
  uv run ruff check .
  uv run pyright
  uv run pytest -m "not slow and not failure_expected"
- Report:
  1. What changed
  2. Files changed
  3. Tests/checks run
  4. Any skipped checks and why
  5. Whether the slice acceptance criteria are met
  6. For test-creation slices: **Test catalog** — detailed list of every test function added or changed, with one line per test describing behavior under test (see §9 Test-creation slice convention)

Stop after this slice and wait for approval.
```

### 4.5 `.cursor/commands/small-change.md`

Use for small bounded changes that do not need a full plan.

```markdown
Handle this as a small bounded change, not a full plan.

Before editing:
- If there is a blocking ambiguity that could cause wrong behavior, ask at most 3 focused questions and stop.
- If ambiguity is minor, state your assumption and proceed.
- Do not create a long plan.
- Do not touch unrelated files.
- Do not create new abstractions unless necessary for the immediate change.

After editing:
- Run only the narrowest relevant check unless I ask for broader tests.
- Report changed files and any skipped checks.
```

### 4.6 `.cursor/commands/review-abstractions.md`

Use after a slice if the diff feels too agentic.

```markdown
Review the current diff for unnecessary abstraction.

Do not edit files.

Look specifically for:
- one-call helper functions
- pass-through wrappers
- classes with one method and no meaningful state
- factories with only one concrete implementation
- protocols/interfaces with only one implementation
- registries used in only one place
- adapters that only rename fields or forward calls
- generic names like Manager, Handler, Processor, Executor, Orchestrator
- config objects that only mirror function arguments
- layers that make tracing harder without reducing duplication

For each suspicious abstraction, report:
1. File/path
2. Abstraction name
3. Why it may be unnecessary
4. Whether to inline, keep, rename, or simplify
5. What risk simplification would introduce

Also identify abstractions that are justified and should be kept.
```

### 4.6a `.cursor/commands/review-consistency.md`

Runs **after** `/review-validation` in `/build-plan-slice` and `/small-change`. Canonical text lives in `.cursor/commands/review-consistency.md`.

Use to catch symmetric ORM wiring gaps, stale slice deferrals, and pattern drift vs sibling modules (see §0.2). Parameters mirror `/review-validation` (`Changes only`, optional `Edit`, optional `File`).

### 4.7 `.cursor/commands/commit-changes.md`

Use once a slice or logical group of slices is ready to commit.

```markdown
Use the repository's deterministic commit script.

Default:
python scripts/cursor/commit_changes.py

If and only if my current message explicitly says to skip tests:
python scripts/cursor/commit_changes.py --skip-tests

Rules:
- Skipping tests is invocation-local and must not become the default.
- Do not infer that tests should be skipped unless I explicitly say so in the current message.
- Do not fix failing tests during this command.
- If checks fail, report failures and stop.
- Review the entire diff, including files you did not edit.
- Prefer atomic commits using a strict standard of relatedness:
  - behavior changes separate from tests when practical
  - refactors separate from behavior changes
  - config/dependency changes separate from source changes
  - generated or formatting-only changes separate when substantial
  - docs separate from implementation unless the docs only explain the same small change
- Use interactive staging or equivalent patch-level staging where practical.
- Before each commit, show:
  1. files/hunks included
  2. files/hunks excluded
  3. proposed commit message
```

### 4.8 `.cursor/commands/explain-codepath.md`

Use when you want to understand existing code before changing it.

```markdown
Explain the code path relevant to my request.

Do not edit files.

Output:
1. Entry points
2. Main functions/classes involved
3. Data flow
4. Database tables/models involved, if any
5. Service boundaries involved, if any
6. Tests that currently cover this behavior
7. Risks or confusing parts
8. Where a future change should likely be made

Keep the explanation concise and code-grounded.
```

### 4.9 `.cursor/commands/review-branch.md`

Use before committing or after several slices.

```markdown
Review the current branch against main.

Do not edit files.

Focus on logical behavior, not commit history.

Output:
1. Summary of branch intent
2. Changed files grouped by purpose
3. Behavior changes
4. Schema/config/dependency changes
5. Tests added/changed
6. Risk areas
7. Suspicious abstractions
8. Missing tests
9. Recommended next checks
```

## 5. Scripts

Scripts go in `scripts/cursor/`. These reduce token usage by moving deterministic tasks out of the agent.

### 5.1 `scripts/cursor/checks.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -m "not slow and not failure_expected"
```

Make it executable:

```bash
chmod +x scripts/cursor/checks.sh
```

### 5.2 `scripts/cursor/changed_files_summary.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "## Branch"
git branch --show-current

echo
echo "## Status"
git status --short

echo
echo "## Diff stat"
git diff --stat

echo
echo "## Changed files"
git diff --name-status

echo
echo "## Staged files"
git diff --cached --name-status
```

Make it executable:

```bash
chmod +x scripts/cursor/changed_files_summary.sh
```

### 5.3 `scripts/cursor/commit_changes.py`

```python
"""Commit helper for Cursor-driven development.

This script intentionally keeps humans in the loop for staging and commit
boundaries. It runs checks, shows the diff, suggests a strict commit rubric,
and then opens interactive staging.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence


def run(cmd: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    print(f"\n$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, text=True)


def capture(cmd: Sequence[str]) -> str:
    return subprocess.check_output(cmd, text=True).strip()


def has_changes() -> bool:
    result = subprocess.run(["git", "diff", "--quiet"])
    unstaged = result.returncode != 0
    result_cached = subprocess.run(["git", "diff", "--cached", "--quiet"])
    staged = result_cached.returncode != 0
    return unstaged or staged


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip pytest for this invocation only.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show status and suggested process without staging or committing.",
    )
    args = parser.parse_args()

    if not has_changes():
        print("No changes to commit.")
        return 0

    run(["uv", "run", "ruff", "format", "."])
    run(["uv", "run", "ruff", "check", "."])
    run(["uv", "run", "pyright"])

    if args.skip_tests:
        print("\nTests skipped for this invocation only.")
    else:
        run(["uv", "run", "pytest", "-m", "not slow and not failure_expected"])

    print("\n## Current diff summary")
    run(["git", "status", "--short"], check=False)
    run(["git", "diff", "--stat"], check=False)
    run(["git", "diff", "--name-status"], check=False)

    print(
        """
## Commit splitting rubric

Prefer separate commits when changes differ by:
- behavior vs tests
- production code vs refactor
- config/dependency changes vs app code
- generated files vs source files
- docs vs implementation
- schema migration vs consumers
- mechanical rename vs semantic behavior change

Use `git add -p` for patch-level staging.
"""
    )

    if args.dry_run:
        return 0

    while True:
        answer = input("Open interactive staging now? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Stopping before staging.")
            return 0

        run(["git", "add", "-p"], check=False)
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode != 0

        if not staged:
            print("No staged changes.")
        else:
            print("\n## Staged diff")
            run(["git", "diff", "--cached", "--stat"], check=False)
            run(["git", "diff", "--cached", "--name-status"], check=False)

            message = input("Commit message, or blank to skip commit: ").strip()
            if message:
                run(["git", "commit", "-m", message])

        if not has_changes():
            print("All changes committed.")
            return 0

        again = input("Continue staging another commit? [y/N] ").strip().lower()
        if again not in {"y", "yes"}:
            print("Remaining changes left uncommitted.")
            return 0


if __name__ == "__main__":
    sys.exit(main())
```

### 5.4 `scripts/cursor/new_branch.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/cursor/new_branch.sh <branch-name>"
  exit 1
fi

branch="$1"

git status --short

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree is not clean. Commit or stash changes before creating a new branch."
  exit 1
fi

git checkout main
git pull --ff-only
git checkout -b "$branch"
```

Make it executable:

```bash
chmod +x scripts/cursor/new_branch.sh
```

## 6. VS Code / Cursor extensions and settings

You said you primarily use:

- indent-rainbow
- Data Wrangler
- Python
- isort

For this project, recommended extensions:

| Extension | Recommendation | Notes |
|---|---|---|
| Python | Keep | Needed for Python language support. |
| Data Wrangler | Keep | Useful for inspecting dataframes later. |
| indent-rainbow | Keep | Personal readability preference. |
| isort | Optional | Ruff can sort imports; avoid conflicting format-on-save behavior. |
| Ruff | Recommended | Useful for editor lint/format integration. |
| Pyright / Pylance | Recommended | Pyright is the configured type checker. |

If Cursor cannot find an extension, download the VSIX from the VS Code marketplace and install it manually.

Recommended `.vscode/settings.json` if you choose to commit editor settings:

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
  "python.terminal.activateEnvironment": true,
  "editor.formatOnSave": true,
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.codeActionsOnSave": {
      "source.organizeImports": "explicit",
      "source.fixAll.ruff": "explicit"
    }
  },
  "ruff.nativeServer": "on",
  "python.analysis.typeCheckingMode": "strict"
}
```

If the Ruff extension ID differs in Cursor, select Ruff manually as the Python formatter through the command palette.

## 7. MCPs, subagents, and skills

Do not add MCPs, custom subagents, or custom skills at initial setup.

Reasons:

- The project benefits more from precise repo-local rules, commands, and scripts.
- MCPs/subagents/skills can add token/context overhead.
- The design document already gives strong architecture boundaries.
- Most repeated workflows here are deterministic enough to script.

Reconsider only if a concrete bottleneck appears:

| Tooling type | Use when | Avoid when |
|---|---|---|
| Script | A workflow is deterministic shell/Python work | The workflow requires broad design judgment |
| Cursor command | You need repeated agent behavior in this repo | A shell script can do it exactly |
| Rule | The instruction should affect nearly every coding task | The instruction is long or situational |
| Skill | A large reusable workflow needs bundled docs/scripts | It would always load too much context |
| Subagent | A specialized repeated task requires independent codebase research | The main agent can read the needed files |
| MCP | External system access is essential | Local files/scripts are enough |

## 8. Alembic tutorial for this project

### 8.1 What Alembic does

SQLAlchemy ORM models describe your intended database tables in Python. Alembic manages the database schema over time.

Think of the pieces this way:

- SQLAlchemy model classes define table shapes.
- SQLAlchemy `MetaData` is the collection of table definitions.
- Alembic compares metadata against the actual database when using autogenerate.
- Alembic revision files are versioned migration scripts.
- `upgrade()` changes the database forward.
- `downgrade()` reverses that migration when practical.
- The `alembic_version` table records which revision the database is currently on.

Alembic does not magically understand every intended domain rule. Autogenerate is a starting point, not a substitute for review.

### 8.2 SQLAlchemy metadata

You will usually have a base like this in `calendar_backend/db/base.py`:

```python
from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
```

The naming convention matters because Alembic can generate stable constraint names. Stable names make migrations easier to review and downgrade.

### 8.3 Alembic init

Run:

```bash
uv run alembic init calendar_backend/db/migrations
```

This creates:

```text
alembic.ini
calendar_backend/db/migrations/env.py
calendar_backend/db/migrations/script.py.mako
calendar_backend/db/migrations/versions/
```

Because the design doc places migrations under `calendar_backend/db/migrations/`, keep them there.

### 8.4 Configure `alembic.ini`

In `alembic.ini`, set:

```ini
script_location = calendar_backend/db/migrations
```

For local development, the database URL can be:

```ini
sqlalchemy.url = sqlite:///local_data/calendar_backend.sqlite3
```

Later you can make this configurable through your session/settings layer.

### 8.5 Configure `env.py`

In `calendar_backend/db/migrations/env.py`, import your model metadata.

A typical target:

```python
from calendar_backend.db.base import Base

# Import model modules so SQLAlchemy registers their tables.
from calendar_backend.models import calendar, chains, constraints, free_time, plans, repetitions, runs, settings  # noqa: F401

target_metadata = Base.metadata
```

If models are not imported, Alembic may see an empty metadata object and generate empty migrations.

In agent-driven schema work, wire imports and autogenerate via [`/db-revision-preview`](../../.cursor/commands/db-revision-preview.md) (see §8.11) instead of ad hoc shell steps.

### 8.6 Create a migration

After model changes exist in the working tree, run:

```text
/db-revision-preview
```

Provide a short `-m` message when prompted (for example `create remaining core orm tables`).

The command:

1. Ensures `env.py` imports every ORM module that should register on `Base.metadata`.
2. Runs ruff format, ruff check, and pyright.
3. Runs `uv run alembic revision --autogenerate -m "<message>"`.
4. Produces a structured migration review (report only — **does not edit** the generated revision).

Generated files land under:

```text
calendar_backend/db/migrations/versions/
```

**Manual review checklist** (also used by the preview report):

- Are all expected tables present?
- Are unexpected tables absent?
- Are constraints named and meaningful?
- Are nullable settings correct?
- Are enum/string values represented as intended?
- Are FK constraints correct?
- Are indexes necessary now, or speculative?
- Does downgrade reverse the migration?

Edit the migration file manually based on the preview report, then run [`/db-revision-continue`](../../.cursor/commands/db-revision-continue.md).

### 8.7 Apply migrations

After preview approval and manual migration edits, run:

```text
/db-revision-continue
```

This applies `uv run alembic upgrade head`, runs pytest, then follows the [`/commit-changes`](../../.cursor/commands/commit-changes.md) workflow (with checks skipped because preview/continue already ran them).

For manual inspection only (outside the continue command):

```bash
uv run alembic upgrade head
```

This applies all pending migrations to the local database.

### 8.8 Check current revision and history

```bash
uv run alembic current
uv run alembic history
```

### 8.9 Downgrade

Downgrade one revision:

```bash
uv run alembic downgrade -1
```

Downgrade to base:

```bash
uv run alembic downgrade base
```

In early local development, downgrade helps validate that migrations are reversible. In production, downgrades can be more complicated, especially when data migrations are involved.

### 8.10 SQLite limitations

SQLite is good for V1, but migrations have constraints:

- Some ALTER TABLE operations are limited.
- Alembic may need batch mode for certain table changes.
- SQLite has weaker type enforcement than Postgres.
- Enum behavior is usually represented as strings/check constraints rather than native enum types.
- Foreign key enforcement must be enabled for SQLite connections.

For SQLite batch migrations, Alembic can use:

```python
with op.batch_alter_table("some_table") as batch_op:
    batch_op.add_column(...)
```

Do not over-optimize for Postgres in V1, but avoid SQLite-only assumptions when easy.

### 8.11 Recommended migration workflow

For each schema slice (after ORM model changes are in the working tree):

```text
# 1. Generate draft migration + structured review (does not apply or edit migration).
/db-revision-preview

# 2. Edit calendar_backend/db/migrations/versions/<revision>.py manually per preview report.

# 3. Apply migration, run pytest, and commit (see db-revision-continue for full steps).
/db-revision-continue
```

See [`.cursor/commands/db-revision-preview.md`](../../.cursor/commands/db-revision-preview.md) and [`.cursor/commands/db-revision-continue.md`](../../.cursor/commands/db-revision-continue.md).

During `/build-plan-slice`, stop after preview and wait for migration approval before running continue — same as [`build-plan-slice`](../../.cursor/commands/build-plan-slice.md) slice boundaries.

### 8.12 Common Alembic mistakes

| Mistake | Symptom | Fix |
|---|---|---|
| Forgot to import models in `env.py` | Empty autogenerate migration | `/db-revision-preview` wires imports; verify preview report and `env.py` section. |
| Multiple Base objects | Alembic sees only some tables | Ensure every model inherits from the same `Base`. |
| Did not review autogenerate | Bad nullable/FK/index choices | Use `/db-revision-preview`; treat output as a draft; edit before `/db-revision-continue`. |
| Renamed a column | Alembic generates drop/add | Manually edit migration to rename when preserving data matters. |
| SQLite foreign keys disabled | Relationship tests pass incorrectly or fail inconsistently | Enable `PRAGMA foreign_keys=ON` on connection. |
| Migration depends on app services | Migration breaks outside app runtime | Keep migrations schema/data focused and self-contained. |

## 9. Implementation plan prompts

Use these prompts sequentially. Each prompt should produce a finalized plan in `docs/plans/`, and each plan should contain smaller build slices.

These prompts intentionally split the design into more granular plans than the high-level roadmap.

### Test-creation slice convention

Any slice whose primary deliverable is **new or materially updated tests** must, when built via `/build-plan-slice`, end the chat report with a **Test catalog**:

- List **every test function** added or materially changed (`tests/<path>::test_<name>` or equivalent).
- One line per test stating the **behavior under test** (not implementation detail).
- Group by file or category if helpful; include markers (`integration`, `failure_expected`) when used.

Apply to slices named or scoped for tests (schema tests, service tests, invariant tests, smoke tests, etc.). Non-test slices do not require a Test catalog.

When tests cover a recently implemented chunk, they must exercise **all behavior, schema, and constraints introduced in that chunk** — plan slice test bullets are minimum examples, not the full scope (see `.cursor/rules/20-testing-and-checks.mdc`).

Finalized plans should repeat this requirement on each test-creation slice (acceptance criteria or implementation steps).

### Prompt 1: Repository skeleton and tooling

```text
Use /request-questions first.

Create a Cursor implementation plan for initial repository setup for calendar_backend.

Context:
- Brand-new empty repo.
- Use uv, ruff, pyright, pytest, SQLAlchemy, and Alembic.
- Create the package skeleton from the updated V1 engineering design document.
- Create repo-local Cursor commands/rules/scripts from docs/cursor_implementation_guide.md.
- Create a minimal tools/dev_cli.py placeholder only.
- Do not implement real domain behavior yet.

The plan should be split into small slices:
1. pyproject/dependency/tooling baseline
2. package directory skeleton
3. Cursor rules/commands/scripts
4. initial smoke tests/checks (post Test catalog in chat)

Store the finalized plan in docs/plans/.
```

### Prompt 2: Database base, session, and Alembic baseline

```text
Use /request-questions first.

Create a Cursor implementation plan for database infrastructure.

Context:
- Follow the updated V1 engineering design document.
- Implement calendar_backend/db/base.py with SQLAlchemy Declarative Base and naming conventions.
- Implement calendar_backend/db/session.py with engine/session factory and transaction helper.
- Configure Alembic under calendar_backend/db/migrations.
- Ensure SQLite foreign keys are enabled.
- Add minimal tests for engine/session behavior.
- Do not implement application ORM models yet beyond what is necessary for Alembic wiring.

Split into slices:
1. SQLAlchemy Base and metadata conventions
2. session/engine helpers and SQLite FK behavior
3. Alembic initialization/configuration
4. database infrastructure tests (post Test catalog in chat)

Store the finalized plan in docs/plans/.
```

### Prompt 3: Domain primitives, IDs, enums, time helpers, results

```text
Use /request-questions first.

Create a Cursor implementation plan for core domain primitives.

Context:
- Implement domain NewType UUID IDs, enums, errors, time dataclasses/helpers, clock protocol/service, and ServiceResult.
- Use frozen dataclasses where practical.
- Time rules: timezone-aware UTC datetimes for persisted timestamps; integer minutes for durations and granularity; no sub-minute scheduling.
- Keep this pure domain layer free of SQLAlchemy sessions.

Split into slices:
1. ID NewTypes and UUID helpers
2. enums and error/message codes
3. time window dataclasses, UTC/minute-alignment helpers, clock abstraction
4. ServiceResult and common result helpers
5. tests for domain validation and time helpers (post Test catalog in chat)

Store the finalized plan in docs/plans/.
```

### Prompt 4: Core ORM models part 1 — plans and child chains

```text
Use /request-questions first.

Create a Cursor implementation plan for core plan ORM models.

Context:
- Follow the updated V1 data model.
- Implement Plan base table and one-to-one subtype detail tables for GoalPlan, TaskPlan, and RepetitionPlan.
- Implement GoalChildChain and GoalChildChainItem.
- Models are persistence records only; do not add public mutation behavior.
- Use plan_id as PK/FK for subtype detail rows.
- Only GoalPlan may be master.
- Each active non-master child plan should be representable as appearing in exactly one chain item, with enforcement where practical.

Split into slices:
1. Plan and subtype tables
2. goal child chain tables
3. relationships and basic constraints
4. model/schema tests (post Test catalog in chat)

Store the finalized plan in docs/plans/.
```

### Prompt 5: Core ORM models part 2 — constraints, repetitions, calendar, settings, runs, free time

```text
Use /request-questions first.

Create a Cursor implementation plan for the remaining core ORM models.

Context:
- Follow the updated V1 data model (see §0.1 for PDF deviations).
- Implement TimeConstraintGroup and TimeWindow with constraint_kind (no group_order on groups).
- Implement RepetitionInstance with is_critical, sort_order, and instance_index (see §0.1).
- Implement CalendarEntry with TASK/FREE_TIME entry types and denormalized display_label.
- Implement FreeTimeActivity and FreeTimeActivityPrerequisite.
- Implement CalendarRun, ActiveCalendarState, and AppSettings.
- Models are persistence records only.
- Add schema tests where the plan specifies them.

Split into slices:
1. constraints tables
2. repetition instance table
3. calendar entry table
4. free-time tables
5. settings and run metadata tables
6. schema tests (post Test catalog in chat)

Store the finalized plan in docs/plans/.
```

### Prompt 6: Master plan, app settings, and master horizon services

```text
Use /request-questions first.

Create a Cursor implementation plan for foundational services.

Context:
- Follow the updated V1 service layer.
- Implement MasterPlanService.ensure_master_exists().
- Implement AppSettingsService.
- Implement MasterHorizonService.refresh_master_horizon(run_started_at).
- Service methods return ServiceResult[T].
- Mutations happen inside transactions.
- SYSTEM_MASTER_HORIZON constraints are system-owned and not directly editable.
- Add invariant and service tests.

Split into slices:
1. service test fixtures and transaction test helpers (post Test catalog in chat for new helpers/fixtures used by tests)
2. MasterPlanService
3. AppSettingsService
4. MasterHorizonService
5. invariant/service tests (post Test catalog in chat)

Store the finalized plan in docs/plans/.
```

### Prompt 7: Time constraint service and invariant validation

```text
Use /request-questions first.

Create a Cursor implementation plan for time constraint editing and invariant validation.

Context:
- Implement TimeConstraintService for USER constraints only.
- Reject direct mutation of SYSTEM_REPETITION_WINDOW and SYSTEM_MASTER_HORIZON constraints.
- Implement PlanTreeInvariantService or invariant_validation module for diagnostics.
- Constraint semantics: AND-of-OR groups, empty outer list means no local restriction, empty inner group invalid, windows are half-open and minute-aligned.
- Normalize/merge OR windows within each group where appropriate.
- Do not implement task resolution yet.

Split into slices:
1. user constraint add/update/remove APIs
2. constraint validation and normalization helpers
3. system-owned constraint edit rejection
4. invariant diagnostics
5. tests (post Test catalog in chat)

Store the finalized plan in docs/plans/.
```

### Prompt 8: Plan tree service

```text
Use /request-questions first.

Create a Cursor implementation plan for PlanTreeService.

Context:
- Implement create_goal, create_task, create_repetition, move_plan, rename_plan, preview_delete, and delete_plan as appropriate for V1.
- Maintain rooted tree under master.
- No orphan active plans.
- Deletion cascades to descendants.
- Deleting a plan inside a goal child chain deletes the whole chain as specified by the design.
- Master cannot be deleted.
- Keep subtype behavior service-owned, not model-owned.
- Do not implement repetition refresh internals yet beyond placeholders needed for plan creation.

Split into slices:
1. create operations
2. move/rename operations
3. deletion preview foundations
4. real deletion and cascade parity
5. tests for tree invariants and deletion behavior (post Test catalog in chat)

Store the finalized plan in docs/plans/.
```

### Prompt 9: Task service

```text
Use /request-questions first.

Create a Cursor implementation plan for TaskService.

Context:
- Implement mark_complete, reopen, and update_scheduling_fields.
- Manual task completion only.
- Reopen toggles user_completed and completed_at.
- Scheduling fields must obey minute/duration/divisibility/minimum chunk rules.
- Linked repetition clones detach as required when edited/completed.
- Do not implement full repetition refresh yet unless needed for detachment primitives.

Split into slices:
1. task scheduling field validation/update
2. mark_complete/reopen
3. clone detachment hooks/primitives
4. service tests (post Test catalog in chat)

Store the finalized plan in docs/plans/.
```

### Prompt 10: Repetition service

```text
Use /request-questions first.

Create a Cursor implementation plan for RepetitionService.

Context:
- Implement repetition creation, generation, refresh, clone propagation, descendant-only detachment rules, and materialized SYSTEM_REPETITION_WINDOW constraints.
- RepetitionInstance rows use is_critical and sort_order (critical-first, then sort_order within bucket) per §0.1; set is_critical from default_instance_critical at generation; assign sort_order in RepetitionService.
- Template subtree is unscheduled.
- Instance 0 is a scheduled clone shifted by 0 * repeat_interval.
- After generation, mode/start_time/repeat_interval are locked.
- manual_count may increase after generation but may not decrease.
- date_range with unset end_time resolves to master horizon end and expands as horizon rolls.
- Detached clones are not overwritten by template refresh, but ordinary deletion still cascades through descendants.

Split into slices:
1. repetition settings validation and lock rules
2. initial instance generation
3. shifted constraint materialization
4. refresh existing instances without overwriting detached clones
5. detachment behavior tests (post Test catalog in chat)
6. horizon expansion tests (post Test catalog in chat)

Store the finalized plan in docs/plans/.
```

### Prompt 11: Task resolution service

```text
Use /request-questions first.

Create a Cursor implementation plan for TaskResolutionService.

Context:
- resolve_tasks(run_started_at) refreshes master horizon and repetitions, then reads the current master tree.
- Traverse repetitions in critical-first instance order (is_critical, then sort_order within bucket), analogous to goal child chains.
- Output task buckets: valid/invalid and complete/incomplete as specified by the design.
- Include inherited effective constraints and constraint sources.
- Completed predecessors should be ignored.
- Invalid incomplete tasks block assignment.
- Do not write active calendar entries.

Split into slices:
1. resolution DTOs
2. tree traversal and task bucket classification
3. effective constraint intersection
4. precedence constraint extraction
5. invalid incomplete task blocking metadata
6. tests (post Test catalog in chat)

Store the finalized plan in docs/plans/.
```

### Prompt 12: Deletion previews and conflict deletion suggestions

```text
Use /request-questions first.

Create a Cursor implementation plan for deletion preview and conflict deletion suggestion support.

Context:
- DeletionPreviewService computes exactly what would be deleted for candidate operations.
- Real deletion and preview must match.
- ConflictDeletionSuggestionService ranks candidate deletion previews for assignment conflicts.
- Ordinary deletion cascades to descendants.
- Chain deletion cascade and critical chain deletion behavior must be represented.
- This plan should not implement task assignment solving.

Split into slices:
1. pure deletion preview data structures
2. service-facing preview_delete
3. parity tests between preview and real deletion (post Test catalog in chat)
4. conflict deletion candidate generation
5. ranking tests (post Test catalog in chat)

Store the finalized plan in docs/plans/.
```

### Prompt 13: Scheduling interfaces and heuristic solver

```text
Use /request-questions first.

Create a Cursor implementation plan for scheduling interfaces and deterministic heuristic assignment.

Context:
- Implement AssignmentSolver protocol/interface and solver result types.
- Implement deterministic heuristic fallback that never violates hard constraints.
- No OR-Tools yet.
- Scheduling granularity is 1 minute.
- Hard feasibility includes full duration assignment, valid windows, non-overlap, minimum chunk size, and precedence constraints.
- Previous entries may be used as soft stability hints where supported.
- Keep scheduling package free of SQLAlchemy sessions.

Split into slices:
1. solver interface/result types
2. assignment input DTOs for scheduling package
3. hard feasibility validator
4. deterministic heuristic algorithm
5. heuristic tests (post Test catalog in chat)

Store the finalized plan in docs/plans/.
```

### Prompt 14: Task assignment service and conflict analysis

```text
Use /request-questions first.

Create a Cursor implementation plan for TaskAssignmentService and conflict analysis.

Context:
- TaskAssignmentService.assign_tasks(resolved, run_started_at) coordinates solvers and persists successful TASK entries.
- It requires resolved.run_started_at to match assignment run_started_at.
- It refuses to run if invalid_incomplete_tasks is non-empty.
- On success, atomically replace future TASK entries.
- On failure, leave active TASK entries unchanged and persist failed run summary.
- Add ConflictAnalysisService for deterministic conflict analysis after solver/heuristic failure.
- Do not implement OR-Tools yet.

Split into slices:
1. assignment service guards and run metadata
2. integration with heuristic solver
3. atomic future TASK entry replacement
4. failure behavior and conflict analysis
5. tests for success/failure/no-calendar-replacement (post Test catalog in chat)

Store the finalized plan in docs/plans/.
```

### Prompt 15: Free-time activity and assignment services

```text
Use /request-questions first.

Create a Cursor implementation plan for free-time activity management and free-time assignment.

Context:
- Implement FreeTimeActivityService for enabled activities, fractions, minimum block size, and prerequisites.
- Enabled positive fractions must sum to 1.
- Implement FreeTimeAssignmentService.assign_free_time(run_started_at).
- Task assignment ignores FREE_TIME entries.
- Free-time assignment uses current future TASK entries as blockers.
- It atomically removes/replaces future FREE_TIME entries.
- Blocked activities renormalize fractions.
- Tiny gaps remain unassigned when smaller than minimum block size.
- Failure should not roll back successful task assignment.

Split into slices:
1. free-time activity CRUD/validation
2. prerequisite completion evaluation
3. real_fraction calculation
4. free-time gap discovery around TASK blockers
5. deterministic free-time assignment
6. tests (post Test catalog in chat)

Store the finalized plan in docs/plans/.
```

### Prompt 16: Orchestration refresh workflow

```text
Use /request-questions first.

Create a Cursor implementation plan for OrchestrationService.refresh_schedule.

Context:
- refresh_schedule composes task resolution, task assignment, and free-time assignment.
- It is manually invoked in V1.
- No automatic full orchestration after every edit/completion.
- Successful full orchestration clears failure state.
- Failed assignment persists summary metadata and leaves active task calendar unchanged.
- If task assignment succeeds and free-time assignment fails, preserve task assignment semantics as specified by the design.

Split into slices:
1. orchestration DTOs/result types
2. refresh_schedule happy path
3. assignment failure path
4. free-time failure/partial behavior
5. CalendarRun and ActiveCalendarState tests (post Test catalog in chat)
6. end-to-end integration tests (post Test catalog in chat)

Store the finalized plan in docs/plans/.
```

### Prompt 17: Exact solver with OR-Tools CP-SAT

```text
Use /request-questions first.

Create a Cursor implementation plan for the optional exact OR-Tools CP-SAT solver.

Context:
- Defer dependency installation until this plan.
- Use uv to add OR-Tools.
- Implement ExactAssignmentSolver in calendar_backend/scheduling/exact_cp_sat.py.
- Respect exact_solver_time_limit_seconds and exact_solver_model_size_limit.
- If the exact solver cannot produce a usable result, TaskAssignmentService may fall back to the deterministic heuristic if enabled.
- Hard constraints must never be violated.
- Solver warnings/status should distinguish optimal, feasible not proven optimal, limit reached, and failure.

Split into slices:
1. add OR-Tools dependency and import isolation
2. CP-SAT model input decomposition/model-size guard
3. hard constraints
4. lexicographic objective support
5. solver status/warning mapping
6. integration with TaskAssignmentService fallback
7. tests with small deterministic cases (post Test catalog in chat)

Store the finalized plan in docs/plans/.
```

### Prompt 18: Development CLI

```text
Use /request-questions first.

Create a Cursor implementation plan for the thin development CLI.

Context:
- tools/dev_cli.py exists as a placeholder.
- The CLI is for local manual development/testing only.
- It should call public services, not contain business logic.
- Keep commands minimal and useful for smoke testing.
- Do not build a production user interface.

Split into slices:
1. CLI argument structure
2. database initialization/status command
3. simple master/settings inspection commands
4. optional refresh_schedule command once services support it
5. smoke tests (post Test catalog in chat)

Store the finalized plan in docs/plans/.
```

### Prompt 19: Invariant and integration test hardening

```text
Use /request-questions first.

Create a Cursor implementation plan for test hardening after core services exist.

Context:
- Review the updated V1 design document's testing strategy.
- Add missing invariant, domain validation, repetition, resolution, assignment, free-time, deletion, and integration tests.
- Do not change production behavior unless a test exposes a real bug.
- Keep tests readable and deterministic.
- Each slice build posts a Test catalog in chat (see §9 Test-creation slice convention).

Split into slices by test category:
1. invariant tests (post Test catalog in chat)
2. domain validation tests (post Test catalog in chat)
3. repetition tests (post Test catalog in chat)
4. resolution tests (post Test catalog in chat)
5. assignment tests (post Test catalog in chat)
6. free-time tests (post Test catalog in chat)
7. deletion tests (post Test catalog in chat)
8. integration tests (post Test catalog in chat)

Store the finalized plan in docs/plans/.
```

### Prompt 20: Final V1 design conformance audit

```text
Use /request-questions first.

Create a Cursor implementation plan for a final V1 design conformance audit.

Context:
- Compare the implemented code against the updated V1 engineering design document.
- Do not add new features.
- Identify missing behavior, accidental non-goals, layer violations, unnecessary abstractions, weak tests, and schema drift issues.
- Produce a plan split into audit/fix slices.

Split into slices:
1. package/layer boundary audit
2. data model and schema audit
3. service behavior audit
4. algorithm behavior audit
5. test coverage audit
6. abstraction discipline audit
7. docs/dev CLI audit

Store the finalized plan in docs/plans/.
```

## 10. Branch workflow for solo sequential work

No merge commands are needed because you are working sequentially.

Use a simple branch rhythm:

```bash
bash scripts/cursor/new_branch.sh setup-repo
# build slices
# commit
git checkout main
git merge --ff-only setup-repo
git branch -d setup-repo
```

If `--ff-only` fails, stop and inspect manually. In a solo sequential workflow, that usually means local branch state diverged unexpectedly.

## 11. Checklists

### 11.1 Before drafting a plan

- Is the request tied to a specific design-doc section?
- Have I run `/request-questions`?
- Are there fewer than 3 blocking questions?
- Are assumptions written down?
- Is OR-Tools deferred unless this is the exact-solver plan?

### 11.2 Before building a slice

- Is the active finalized plan in `docs/plans/`?
- Is the slice number/name explicit?
- Are acceptance criteria clear?
- Are expected files listed?
- Does the slice avoid future work?
- Are tests identified?

### 11.3 Before committing

- `uv run ruff format .`
- `uv run ruff check .`
- `uv run pyright`
- `uv run pytest -m "not slow and not failure_expected"`
- Review full diff.
- Use patch-level staging where useful.
- Keep commits stricter than “all related to same feature.”

### 11.4 When Cursor over-abstracts

Run `/review-abstractions`, then simplify if the report finds:

- one-use classes
- one-implementation protocols
- pass-through wrappers
- unnecessary factories
- manager/handler/processor objects with vague responsibilities
- layers that make tracing harder

Keep extraction when it names a meaningful domain step. Remove abstraction when it only preserves hypothetical flexibility.

## 12. What not to automate yet

Do not automate these at the start:

- MCP setup
- custom subagents
- custom skills
- production deployment
- GitHub PR automation
- merge conflict workflows
- frontend scaffolding
- notification scheduling
- external calendar sync

Add them only when the project has a concrete, repeated bottleneck.