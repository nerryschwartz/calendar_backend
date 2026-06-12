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

Validation principles (use when judging keep vs remove):
- Validate at trust boundaries, not on every internal hop.
  - External or new input: API/CLI/queue messages, caller-supplied params, imports, DB reads after migration or manual edits.
  - Trusted internal path: data validated earlier in the same request/transaction/pipeline and not re-exposed.
- Do not trust client/UI convenience checks as authority; backend/core rules must hold for every entry path.
- Prioritize validation where bad data would silently change behavior (wrong semantics, mislabeled units/timezones, inverted bounds) rather than fail loudly nearby.
- Reject ambiguous or mislabeled input; do not silently normalize user/source data to "fix" it. Normalization belongs on derived/computed values (for example clock-derived anchors), not on accepting questionable source input.
- Match rule depth to field meaning: strict invariants for business-critical fields; lighter checks for audit/metadata fields where sub-minute or similar drift does not change outcomes.
- Validate once per boundary crossing. Repeating the same rule on trusted internal DTOs is usually redundant.

Working diff definition:
- Start with tracked changes from `git diff` and `git diff --cached`.
- Also include untracked files that are not excluded by `.gitignore` (new commitable files).
- For an untracked non-ignored file in scope, treat the entire current file as newly added content.
- Exclude ignored and hidden generated files even if present on disk.

Area to examine:
- If `Changes only: true` and `File` is absent, inspect the full working diff.
- If `Changes only: true` and `File` is set, inspect the working diff for `<file>` only.
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
1. Is this at a trust boundary?
   - Trace the value from entry points (callers, parsers, persistence writes/reads) through the diff.
   - Keep validation on mutate/persist paths for data that can enter from outside or corrupt stored state.
   - If the value is built internally from already-validated data on the same path, the check may be redundant.
2. Would invalid data fail loudly anyway, or change behavior silently?
   - Keep checks that prevent wrong but plausible outcomes (timezone/semantic mistakes, silent mis-scheduling, wrong persisted meaning).
   - Removing may be reasonable if failure would be equally clear and local downstream (same function, immediate crash/obvious error).
3. Is the invalid state feasible here?
   - If impossible on this path or already guaranteed by an upstream boundary check, the validation is redundant.
4. Is this validation or normalization?
   - Do not remove boundary rejection of bad source input.
   - Question code that repairs input then proceeds; prefer explicit rejection at boundaries unless normalization is intentionally applied to derived values only.
5. Is the custom validation helpful?
   - Keep it if the alternative failure would be obscure, destructive, delayed, or much harder to diagnose.
   - Recommend removing it if the natural downstream error would be similarly clear and local.

Quick checklist before recommending removal:
- External/new input at a mutate or persist boundary? → keep.
- Silent wrong outcome if bad? → keep.
- Same rule already enforced upstream on this path? → candidate for removal.
- Normalizing user/source input instead of rejecting? → flag for redesign, not silent keep.

Edit mode:
- When `Edit: false`:
  - Do not edit files.
  - Report findings only.
- When `Edit: true`:
  - Remove only validation that is clearly redundant or clearly not helpful.
  - Do not remove boundary validation for external input, file/network inputs, config loading, data safety, security, persistence, destructive operations, or rules that prevent silent semantic mistakes unless the guarantee is explicit and local on the same path.
  - Do not remove shared domain invariant helpers merely because a future UI might prevent bad input; services and non-UI callers still need those rules.
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