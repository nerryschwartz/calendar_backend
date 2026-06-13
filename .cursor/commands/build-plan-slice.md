Build exactly one approved plan slice.

Before editing:
- Identify the active finalized plan file in docs/plans/.
- Identify the exact slice to build.
- **Migration-slice gate:** If the slice text references [`.cursor/commands/db-revision-preview.md`](db-revision-preview.md) and/or [`.cursor/commands/db-revision-continue.md`](db-revision-continue.md) (including `/db-revision-preview` or `/db-revision-continue`), **do not edit any files**. Output the [Migration slice manual workflow](#migration-slice-manual-workflow) below (tailored to that slice), then stop. Do not run review-validation, review-consistency, or implementation checks — there is no diff.
- Run **slice preflight** and report 3–5 bullets before editing (or before the manual-workflow message when the migration-slice gate applies):
  1. Restate the slice objective in one sentence (spirit, not file list).
  2. Read sibling/completed modules for the same pattern (ORM, tests, services).
  3. Note stale plan assumptions vs current repo (renamed columns, guide §0.1, already-merged slices).
  4. List consistency gaps this slice should close (symmetric relationships, CHECKs, env imports).
  5. If preflight shows plan text is wrong: stop and ask, or fix within slice scope and report under **Consistency & divergence**.
- If the slice is ambiguous, ask one concise blocking question and stop.
- Do not build future slices.
- Do not broaden scope into **future plan slices** or **unrelated features**.
- **Do** complete **consistency and deferred wiring** required by the slice objective when target modules already exist.
- If implementation reveals the approved plan is wrong or incomplete, stop and ask (or fix + report as above).

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
  uv run pytest -m not slow and not failure_expected
- Report:
  1. What changed
  2. Files changed
  3. Tests/checks run
  4. Any skipped checks and why
  5. Whether the slice acceptance criteria are met
  6. For test-creation slices: **Test catalog** — detailed list of every test function added or changed, with one line per test describing behavior under test (see docs/cursor_implementation_guide.md §9 Test-creation slice convention)
  7. **Consistency & divergence** — patterns completed, stale plan text, intentional deferrals left for later slices (with reason)

Then run `/review-validation` using `.cursor/commands/review-validation.md` with:
- Changes only: true
- Edit: true

Validation pass rules:
- Inspect the current git diff only; do not examine or edit files or lines outside the diff.
- Remove only validation that is clearly redundant or clearly not helpful per that command.
- Do not write a findings-only report when redundant validation can be removed within the diff.
- If no validation changes are warranted, say so briefly in the slice report.
- After validation edits, run the narrowest relevant checks again when code changed.

Then run `/review-consistency` using `.cursor/commands/review-consistency.md` with:
- Changes only: true
- Edit: true

Consistency pass rules:
- Inspect the working diff and modules it touches for symmetric ORM/service patterns per guide §0.2.
- Fix only clear gaps within the diff scope; do not implement future slices.
- If no consistency edits are warranted, say so briefly in the slice report.
- After consistency edits, run the narrowest relevant checks again when code changed.

Stop after this slice and wait for approval.

## Migration slice manual workflow

Use when the **migration-slice gate** applies. **Do not implement this slice via `/build-plan-slice`.** The db-revision commands own autogenerate, manual migration edit, apply, and commit.

After slice preflight, post a message in this shape (fill in slice-specific names, messages, and step numbers from the plan):

---

### This slice uses Alembic db-revision commands

**Slice:** \<N\> — \<objective one-liner\>  
**Plan:** `docs/plans/\<plan-file\>.md`

`/build-plan-slice` does not run migration autogenerate or `upgrade head` for this slice. Follow the sequence below.

#### 1. Migration preview — run the command

```
/db-revision-preview
```

Include the autogenerate message from the plan (e.g. `Message: create remaining core orm tables` if the plan specifies one).

The agent will wire `env.py` if needed, autogenerate the revision, and post a **review-only** report. **Do not** let `/build-plan-slice` substitute for this step.

#### 2. Manual migration edit — you

Edit the generated file under `calendar_backend/db/migrations/versions/` per the preview report (CHECKs, FK order, enum columns, partial indexes, ruff on the migration file, SQLite `batch_alter_table` for ALTER constraints, etc.).

Stop and approve the migration before continue.

#### 3. Other slice steps — use `/small-change`

Steps that are **not** preview/continue (tests, extra migrations, env fixes, ORM tweaks) should be done in separate `/small-change` turns. Example prompts:

- `/small-change` — "Add `tests/models/test_core_orm_part2_schema.py` per slice \<N\> of `docs/plans/\<plan\>.md`: metadata, CHECK/FK integration, relationship navigation, Alembic smoke test. Post Test catalog."
- `/small-change` — "Fix ruff/format issues in `calendar_backend/db/migrations/versions/\<revision\>_*.py`."
- `/small-change` — "Add a follow-up migration for \<constraints/tables\> using SQLite batch mode; add matching tests in `test_plans_schema.py`."

Map each remaining **implementation step** from the plan to a concrete `/small-change` prompt (list them in chat).

#### 4. Apply, verify, commit — run the command

After the migration file is approved and schema tests exist (if the plan requires them before a green continue):

```
/db-revision-continue
```

This runs `alembic upgrade head`, pytest, and the commit workflow per [db-revision-continue.md](db-revision-continue.md).

---

Do not proceed with `/build-plan-slice` implementation for db-revision steps. Return to `/build-plan-slice` only for slices that do **not** reference the db-revision commands.
