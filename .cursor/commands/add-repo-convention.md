Add a repository code convention and align docs and code.

Parameter hygiene:
- Ignore trailing words attached to the slash command.
- Use only labeled fields in the current user message.
- Required field:
  - Rule: <plain-language convention text>
- Optional fields:
  - Title: <short name for the convention section heading>
  - Scope: <when the rule applies>
- If `Rule` is missing, ask for it and stop.

Authority:
- New conventions are appended to [`.cursor/repo_conventions.md`](../repo_conventions.md).
- Conventions take precedence over the PDF, guide, plans, and existing code (see [`.cursor/rules/01-repo-conventions.mdc`](../rules/01-repo-conventions.mdc)).

## 1. Contradiction check (stop if conflict)

Compare the proposed `Rule` against:
1. Every numbered convention already in `.cursor/repo_conventions.md`
2. Every other `.cursor/rules/*.mdc` file (except `01-repo-conventions.mdc`, which only defines precedence)

Do **not** treat guide, plans, PDF, or code as blocking contradictions — those are updated in step 3 when the rule is accepted.

If the proposed rule clearly contradicts an existing convention or cursor rule:
- Report both texts and why they conflict.
- **Stop.** Do not edit files.

If no contradiction, continue.

## 2. Append the convention

Add the next numbered section to `.cursor/repo_conventions.md`:
- Use `Title` if provided; otherwise derive a short heading from `Rule`.
- Include **Scope**, **Rule** (imperative), **Examples** when helpful, and **Supersedes** when the rule replaces PDF/guide/plan guidance.
- Keep wording agent-readable and concrete.

## 3. Align downstream artifacts

Update to match the new convention:

1. **`docs/cursor_implementation_guide.md`**
   - Add or extend **§0.3** supersession notes when the rule overrides PDF or guide text.
   - Fix any guide sections that now conflict (service patterns, file layout, etc.).

2. **`docs/plans/*.md`**
   - Fix plan text that contradicts the convention (locked assumptions, slice steps, file lists).

3. **Do not edit** `docs/calendar_backend_v1_engineering_design_updated.pdf`. Record PDF supersession in guide §0.3 instead; note that the user updates the PDF manually.

4. **Code**
   - Fix violations in scope (typically `calendar_backend/services/`).
   - Prefer minimal diffs; match sibling services that already follow the convention.

5. **Other commands/rules**
   - Update `.cursor/commands/draft-plan.md` (or similar) only when they still claim PDF-only supremacy over repo conventions.

Do not run autogenerate migrations unless the convention requires schema changes.

## 4. Verify

Run the narrowest relevant checks for touched code:
```bash
uv run ruff format <changed files>
uv run ruff check <changed files>
uv run pyright <changed files>
```

Skip pytest unless code behavior changed materially or the user asked for full tests.

## Output

Report:
1. Contradiction check result
2. Convention number and title added
3. Files updated (conventions, guide, plans, code)
4. PDF supersession notes added (guide §0.3), if any
5. Checks run

## Do not

- Add a convention when step 1 found a contradiction (unless the user sends a follow-up explicitly resolving the conflict).
- Edit the PDF.
- Broaden scope beyond what the convention requires.
