Review the working diff and nearby changed modules for pattern and wiring consistency.

Parameter hygiene:
- Ignore trailing words attached to the slash command.
- Use only labeled fields in the current user message.
- Optional fields:
  - Changes only: true|false
  - Edit: true|false
  - File: <path>
- If `Changes only` is missing, assume `Changes only: true`.
- If `Edit` is missing, assume `Edit: false`.
- Do not infer parameters from previous invocations.

Working diff definition:
- Start with tracked changes from `git diff` and `git diff --cached`.
- Also include untracked files that are not excluded by `.gitignore`.
- For an untracked non-ignored file in scope, treat the entire current file as newly added content.

Area to examine:
- If `Changes only: true` and `File` is absent, inspect the full working diff plus modules it touches for symmetric patterns.
- If `Changes only: true` and `File` is set, inspect the working diff for `<file>` and its direct ORM/service neighbors only.
- If `Changes only: false`, inspect the whole codebase (or the given `File`) for consistency issues.

Authority (when judging fixes):
1. V1 design + `docs/cursor_implementation_guide.md` (including §0.1–§0.2)
2. Existing sibling modules in the repo
3. Plan slice **objective and acceptance criteria**
4. Plan file lists and step bullets — hints, not a cap on symmetric wiring

Consistency principles (see guide §0.2):
- FK column present → add matching `relationship()` when the target mapped class exists, unless a one-way leaf pointer is intentional and documented in the slice report.
- Symmetric nullable source pairs (e.g. `source_plan_id` / `source_free_time_activity_id`) should both be navigable when both target models exist.
- Defer only when the **target model does not exist yet** — not because a different slice number owns the file.
- Match CHECK / import / env.py registration patterns used by sibling tables in the same chunk.
- Do not add `back_populates` inverses on `Plan` unless the diff already introduces that navigation need; flag optional inverses in the report instead of expanding scope silently.

What to flag:
- FK without `relationship()` where a sibling FK in the same module has one
- Obvious slice-stale deferrals (target class now exists in repo)
- Missing Alembic `env.py` import for a new models module in the diff
- ORM CHECKs added in models but not yet reflected in migration (note for slice 6 — do not autogenerate migrations here)
- Plan text in `docs/plans/` contradicted by implemented code (report only unless `Edit: true` and fix is markdown in diff scope)

Edit mode:
- When `Edit: false`: report findings only.
- When `Edit: true`:
  - Fix only **clear** consistency gaps **within the working diff scope** (same modules the slice already touched).
  - Do not implement future slices, new tables, or migrations.
  - Do not add optional `Plan` back-populates unless the diff already edits `plans.py` for related work.
  - Run the narrowest relevant checks after edits.

Output:
1. Area examined
2. Consistency issues found (file + one-line fix suggestion each)
3. Fixes applied, or recommended if `Edit: false`
4. Intentional deferrals left unchanged (with reason)
5. Checks run, or why none

Keep the report concise.
