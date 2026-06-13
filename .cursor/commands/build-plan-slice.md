Build exactly one approved plan slice.

Before editing:
- Identify the active finalized plan file in docs/plans/.
- Identify the exact slice to build.
- Run **slice preflight** and report 3–5 bullets before editing:
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
