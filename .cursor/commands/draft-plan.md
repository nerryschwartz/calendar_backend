Draft a Cursor implementation plan.

Inputs:
- The updated calendar_backend V1 engineering design document is the source of truth.
- Use the active conversation instructions and any locked decisions.
- Store draft plans in ~/.cursor/plans/.
- After approval, save the finalized plan to docs/plans/.
- Do not edit source code.

Plan requirements:
- Make the plan more granular than the high-level implementation roadmap.
- Split the plan into small slices.
- Each slice should be buildable and reviewable independently.
- Stop after each slice during implementation.
- Do not introduce speculative abstractions.
- If a dependency is needed for a slice, include the uv command to install it in that slice.
- Defer OR-Tools until the exact-solver slice.

Plan format:
# Plan: <short name>

## Context
Summarize the requested change and the relevant design-doc constraints.

## Non-goals
List what this plan intentionally does not implement.

## Locked assumptions
List assumptions that should not be changed during build without asking.

## Slices
For each slice:

### Slice <number>: <name>
Objective:
Files expected to change: (minimum touch points — not a maximum)
May also change: (optional — prior modules when completing symmetric wiring, env imports, stale plan fixes)
Implementation steps: (illustrative — match slice objective and sibling repo patterns, not only these bullets)
Tests/checks:
Acceptance criteria:
Risks/edge cases:

## Abstraction check
List any new classes, protocols, factories, registries, strategy objects, adapters, or helper layers the plan introduces.
For each one, justify why it is needed now.
If an abstraction is only for possible future flexibility, remove it from the plan.

## Dependency changes
List uv add / uv add --dev commands, if any.

## Open questions
Only include questions that block implementation.
