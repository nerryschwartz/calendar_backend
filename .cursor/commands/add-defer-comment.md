Insert a **scheduled deferral** comment at a specific location in source.

Use this when work is **blocked until a named future prompt, plan slice, or V1 milestone** — not for lazy “deal with later” notes. Plain `# TODO:` without a resolve scope in parentheses is discouraged for deferrals.

Parameter hygiene:
- Ignore trailing words attached to the slash command.
- Use only labeled fields in the current user message.
- Required field:
  - `Task:` — what to do when the deferral is resolved (imperative, specific).
- Optional fields:
  - `When:` — when to resolve (default: `v1-complete`).
  - `Resolve:` — explicit resolve label for the comment parenthetical (overrides inference from `When:`).
  - `File:` — path to edit (required if not unambiguous from editor context).
  - `Anchor:` — symbol name, line number, or short nearby code snippet to place the comment above.

Default resolve mapping:
- `When:` omitted or `v1-complete` → comment scope **`Prompt 20`** (Final V1 design conformance audit in [`docs/cursor_implementation_guide.md`](../docs/cursor_implementation_guide.md)).
- `When: Prompt N` → scope **`Prompt N`** (use the guide’s prompt title when helpful, e.g. `Prompt 9 / TaskService`).
- `When: <plan> slice M` or `plan_tree_service slice 5` → scope **`<plan-file> slice M`** (e.g. `plan_tree_service slice 5`); guide target is the **owning prompt** for that plan (see below).
- Other `When:` values → infer the closest guide prompt or finalized plan slice; state the assumption in chat if inference was needed.

Guide registry (when resolve is **not** `v1-complete` / **`Prompt 20`**):
- Also append an entry under **`### Prompt N:`** in [`docs/cursor_implementation_guide.md`](../docs/cursor_implementation_guide.md) so agents see carry-over work when executing that prompt — without scanning all source for `TODO(...)`.
- **Do not** add guide entries for `v1-complete` / **`Prompt 20`** deferrals (Prompt 20 is the audit pass; code comments are sufficient).
- Locate the target prompt:
  - `Resolve:` or `When: Prompt N` → **`### Prompt N:`** section.
  - Plan slice or plan file → owning prompt (e.g. `plan_tree_service` → Prompt 8, `time_constraint_invariant_validation` → Prompt 7, `master_plan_app_settings_master_horizon_services` → Prompt 6); infer from finalized plan path or guide cross-refs if unclear.
- Under that heading, ensure a **`Deferred carry-over:`** subsection exists **immediately above** the prompt’s fenced ` ```text ` block (outside the fence — agent-facing registry, not part of the copy-paste prompt template).
- Append one bullet (dedupe if the same `File:` + `Task:` already listed):
  - `- [`<file>`](<file>): <Task text> — `# TODO(<resolve-scope>)` in source`
- Keep bullets sorted by file path within each prompt section when practical.

Comment format (Python-standard `TODO(scope):`):
- Python: `# TODO(<resolve-scope>): <Task text>.`
- Other languages: same pattern with that language’s line comment prefix (`//`, `#`, etc.).
- `<resolve-scope>` is the explicit prompt, plan slice, or default **`Prompt 20`** — never leave scope empty.
- Keep the line ≤100 characters when practical; shorten `Task` text, not the resolve scope.

Rules:
- Do not insert a deferral comment without a clear `Task:` and resolve scope.
- Do not use this command for work that belongs in the current slice — implement or reject instead.
- Place the comment **immediately above** the line or block the deferral applies to (or at file top for file-wide follow-ups only when `Anchor` is absent and scope is file-level).
- One comment per invocation unless the user explicitly lists multiple `Task:` items.
- When resolve is **not** `v1-complete` / **`Prompt 20`**, update the guide **`Deferred carry-over:`** registry for the target prompt in the same change.
- After editing, reply with: exact comment text, file path, resolve scope, guide section updated (or “none — Prompt 20”), and when it should be addressed.

Examples:

```text
/add-defer-comment
Task: Audit MessageCode enum and remove values with no references
File: calendar_backend/domain/errors.py
When: v1-complete
```

→ `# TODO(Prompt 20): Audit MessageCode enum and remove values with no references.`

(No guide edit — resolves at Prompt 20.)

```text
/add-defer-comment
Task: Reuse task scheduling validation in update_scheduling_fields
File: calendar_backend/services/plan_tree.py
Resolve: Prompt 9 / TaskService
```

→ `# TODO(Prompt 9 / TaskService): Reuse task scheduling validation in update_scheduling_fields.`

Also under **`### Prompt 9: Task service`** in the guide:

```markdown
**Deferred carry-over:**
- [`calendar_backend/services/plan_tree.py`](../../calendar_backend/services/plan_tree.py): Reuse task scheduling validation in update_scheduling_fields — `# TODO(Prompt 9 / TaskService)` in source
```

```text
/add-defer-comment
Task: Remove failure_expected markers after task_plan minimum_chunk migration
File: tests/models/test_plans_schema.py
When: plan_tree_service slice 5
Anchor: test_relationships_navigate_goal_to_chain_item
```

→ `# TODO(plan_tree_service slice 5): Remove failure_expected markers after task_plan minimum_chunk migration.`

Also under **`### Prompt 8: Plan tree service`** in the guide (owning prompt for `plan_tree_service`):

```markdown
**Deferred carry-over:**
- [`tests/models/test_plans_schema.py`](../../tests/models/test_plans_schema.py): Remove failure_expected markers after task_plan minimum_chunk migration — `# TODO(plan_tree_service slice 5)` in source
```
