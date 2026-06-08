Handle this as a small bounded change, not a full plan.

Before editing:
- If there is a blocking ambiguity that could cause wrong behavior, ask at most 3 focused questions and stop.
- If ambiguity is minor, state your assumption and proceed.
- Do not create a long plan.
- Do not touch unrelated files.
- Do not create new abstractions unless necessary for the immediate change.

After editing:
- Run only the narrowest relevant check unless I ask for broader tests.
- Report changed files and any skipped checks.
