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
  uv run pytest -m not slow and not failure_expected
- Report:
  1. What changed
  2. Files changed
  3. Tests/checks run
  4. Any skipped checks and why
  5. Whether the slice acceptance criteria are met
  6. For test-creation slices: **Test catalog** — detailed list of every test function added or changed, with one line per test describing behavior under test (see docs/cursor_implementation_guide.md §9 Test-creation slice convention)

Then run `/review-validation` using `.cursor/commands/review-validation.md` with:
- Changes only: true
- Edit: true

Validation pass rules:
- Inspect the current git diff only; do not examine or edit files or lines outside the diff.
- Remove only validation that is clearly redundant or clearly not helpful per that command.
- Do not write a findings-only report when redundant validation can be removed within the diff.
- If no validation changes are warranted, say so briefly in the slice report.
- After validation edits, run the narrowest relevant checks again when code changed.

Stop after this slice and wait for approval.
