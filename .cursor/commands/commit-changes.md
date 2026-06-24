Use the repository's deterministic commit script.

## 0. Commit-readiness audit (before anything else)

Run [`/audit-commit-readiness`](audit-commit-readiness.md) on the full working tree diff.

- If **blocking** findings: report them and **stop** (do not run `/review-abstractions` or the commit script).
- If **none**: state that briefly and continue.

The user may override and proceed; do not infer override without an explicit say-so.

## 1. Abstraction review

Run `/review-abstractions` on the current diff.

- Do not edit files during the abstraction review unless the user explicitly asks to fix findings in the same turn.

## 2. Commit script

Default:
```bash
python scripts/cursor/commit_changes.py
```

If and only if my current message explicitly says to skip tests:
```bash
python scripts/cursor/commit_changes.py --skip-tests
```

If and only if my current message explicitly says that checks were already run
(for example after `/db-revision-continue` pre-commit verification):
```bash
python scripts/cursor/commit_changes.py --skip-checks
```

## Rules

- Skipping tests or checks is invocation-local and must not become the default.
- Do not infer that tests or checks should be skipped unless I explicitly say so in the current message or the invoking command specifies it.
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
