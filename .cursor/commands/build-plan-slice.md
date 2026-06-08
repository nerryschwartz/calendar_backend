Build exactly one approved plan slice.

Before editing:
- Identify the active plan file in .cursor/plans/.
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

Stop after this slice and wait for approval.
