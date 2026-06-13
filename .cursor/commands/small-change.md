Handle this as a small bounded change, not a full plan.

Before editing:
- If there is a blocking ambiguity that could cause wrong behavior, ask at most 3 focused questions and stop.
- If ambiguity is minor, state your assumption and proceed.
- Do not create a long plan.
- Do not touch unrelated files.
- Do not create new abstractions unless necessary for the immediate change.
- When the change touches ORM or schema, apply `docs/cursor_implementation_guide.md` §0.2 (symmetric wiring where target models exist).

After editing:
- Run only the narrowest relevant check unless I ask for broader tests.
- Report changed files and any skipped checks.

Then run `/review-validation` using `.cursor/commands/review-validation.md` with:
- Changes only: true
- Edit: true

Validation pass rules:
- Inspect the current git diff only; do not examine or edit files or lines outside the diff.
- Remove only validation that is clearly redundant or clearly not helpful per that command.
- Do not write a findings-only report when redundant validation can be removed within the diff.
- If no validation changes are warranted, say so briefly and continue.
- After validation edits, run the narrowest relevant checks again when code changed.

Then run `/review-consistency` using `.cursor/commands/review-consistency.md` with:
- Changes only: true
- Edit: true

Consistency pass rules:
- Inspect the working diff for symmetric ORM/service patterns per guide §0.2.
- Fix only clear gaps within the diff scope.
- If no consistency edits are warranted, say so briefly and continue.
- After consistency edits, run the narrowest relevant checks again when code changed.
