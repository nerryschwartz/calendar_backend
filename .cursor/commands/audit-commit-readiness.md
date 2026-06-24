Conservative pre-commit audit for **major or clearly incomplete** work in the working tree.

Use at the start of [`/commit-changes`](commit-changes.md) before `/review-abstractions` or the commit script.

Parameter hygiene:
- Ignore trailing words attached to the slash command.
- Use only labeled fields in the current user message.
- Optional fields:
  - Changes only: true|false (default: true — full working diff vs `git diff` + untracked non-ignored files)
  - File: <path> (optional narrow scope)

Rules:
- **Read-only** — do not edit files during this audit unless the user explicitly asks to fix findings in the same turn.
- Be **conservative**: flag only issues that are **major, obvious, and likely unintentional**. Minor style, subjective design, or “could be better” items are out of scope.
- The user owns final judgment; this is a safeguard, not a gate for nitpicks.
- Inspect the **entire** working diff (staged + unstaged + relevant untracked), not only files from the current chat.

## What to inspect

Start with `git status --short`, `git diff`, `git diff --cached`, and untracked files that are not gitignored.

Skim changed production code, tests, migrations, and docs in the diff.

## Flag as **blocking** only when obvious

Report a **blocking** finding when the diff clearly shows:

1. **Half-finished implementation** — stub bodies (`pass`, `...`, `raise NotImplementedError`, `TODO: implement`) on new or materially changed **production** paths that the diff implies should be complete now.
2. **Broken pairing** — ORM `CheckConstraint` / column / relationship changes with **no** matching migration when the same diff adds enforcement that only works after `alembic upgrade`; or a migration revision that is still obviously autogenerate-only (e.g. empty upgrade, or `### commands auto generated` with known-missing CHECKs the ORM already defines).
3. **Deferred work left active** — `# TODO(<scope>):` on code the **same diff** was supposed to finish; or `failure_expected` on tests the **same diff** should have unmarked (e.g. after adding the migration those tests target).
4. **Accidental debris** — debug prints/logging left in production paths, committed secrets or local env paths, or large commented-out blocks replacing real logic in the changed hunks.
5. **Clear inconsistency in the same diff** — old symbol/name still used alongside rename; imports referencing deleted modules; migration `down_revision` that does not match current head when this revision is presented as the next migration.

## Do **not** flag (non-blocking)

- Subjective abstraction, naming, or structure (use `/review-abstractions` later).
- Missing tests for edge cases when behavior otherwise looks complete.
- Docs/plan drift unless the diff **introduces** an obvious contradiction (e.g. convention says X, same diff implements clearly-not-X with no note).
- Pre-existing issues outside the working diff.
- `failure_expected` tests that are **intentionally** still pending per an explicit deferral comment or repo convention **§13** when the migration is **not** in the same diff.

## Output

Always report:

1. **Area examined** (scope, file count)
2. **Blocking findings** — file + one-line issue + why it is major/obvious (or **None**)
3. **Non-blocking notes** (optional, at most a few — only if useful context, not a full review)

## Stop rule for `/commit-changes`

- If **any blocking** finding: **stop** the commit workflow. List findings; do not run `/review-abstractions` or `commit_changes.py` unless the user explicitly says to proceed anyway.
- If **none**: state “No blocking commit-readiness issues found.” and continue the commit workflow.
