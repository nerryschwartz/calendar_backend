Review validation code in an explicit area and decide whether it should stay.

Parameter hygiene:
- Ignore trailing words attached to the slash command.
- Use only labeled fields in the current user message.
- Required field:
  - Changes only: true|false
- Optional fields:
  - Edit: true|false
  - File: <path>
- If `Changes only` is missing, ask for it.
- If `Edit` is missing, assume `Edit: false`.
- Do not infer parameters from previous invocations.

Area to examine:
- If `Changes only: true` and `File` is absent, inspect the current git diff.
- If `Changes only: true` and `File` is set, inspect `git diff -- <file>`.
- If `Changes only: false` and `File` is set, inspect the whole file.
- If `Changes only: false` and `File` is absent, inspect the whole codebase.
  - Prefer targeted searches for validation patterns.
  - Summarize if the result is too large.

What counts as validation:
Flag code primarily dedicated to checking, guarding, or rephrasing invalid state, including:
- `if ...: raise ...`
- explicit type checks
- explicit range checks
- explicit shape checks
- explicit null checks
- defensive existence checks
- conversion checks whose only purpose is raising a custom error
- repeated validation already guaranteed by a caller, config schema, parser, constructor, or earlier branch

Do not flag:
- ordinary branch logic
- algorithmic conditions
- security or permission checks
- user-input boundary checks
- file/network input validation
- data safety checks
- persistence/destructive-operation checks
- checks that select valid behavior rather than merely reject invalid state

Review questions for each validation area:
1. Is the invalid state feasible?
   - Recursively trace the value from user entry points, config/schema parsing, constructors, callers, and prior checks.
   - If the invalid state is impossible or already validated earlier, the validation is redundant.
2. Is the custom validation helpful?
   - Keep it if the alternative failure would be obscure, destructive, delayed, or much harder for a user to diagnose.
   - Recommend removing it if the natural downstream error would be similarly clear and local.

Edit mode:
- When `Edit: false`:
  - Do not edit files.
  - Report findings only.
- When `Edit: true`:
  - Remove only validation that is clearly redundant or clearly not helpful.
  - Do not remove boundary validation for external user input, file/network inputs, config loading, data safety, security, persistence, or destructive operations unless the guarantee is explicit and local.
  - Keep validation when tracing is uncertain.
  - Preserve behavior other than removing the redundant validation path.
  - Run the narrowest relevant checks after edits.

Output:
Always report:
1. Area examined
2. Validation reviewed
3. Validation removed, or recommended for removal if `Edit: false`
4. Validation kept and why
5. Trace evidence for each decision
6. Checks run, or why none were run

Use precise file/function references.
Keep the report concise.