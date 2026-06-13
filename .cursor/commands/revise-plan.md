Revise the existing implementation plan. Do not create a new plan unless I explicitly say to start over.

Instructions:
- Locate the current active plan in docs/plans/ if finalized, or in ~/.cursor/plans/ if still drafting.
- Preserve the plan's structure unless the structure itself is the problem.
- Apply my requested changes as a patch to the existing plan.
- If the requested change conflicts with an earlier locked decision or the design document, ask before changing it.
- Keep the plan split into small implementation slices.
- When editing slice sections, use the same slice fields as `/draft-plan` (see below). Add missing fields to older plans when touching a slice.
- Align stale slice text with the repo and `docs/cursor_implementation_guide.md` §0.1–§0.2 when revision scope includes ORM/schema (e.g. remove “deferred until slice N” when the target model already exists).
- Do not edit source code.

Slice fields (each slice in the plan should include):
- Objective:
- Files expected to change: (minimum touch points — not a maximum)
- May also change: (optional — prior modules when completing symmetric wiring, env imports, stale plan fixes)
- Implementation steps: (illustrative — match slice objective and sibling repo patterns, not only these bullets)
- Tests/checks:
- Acceptance criteria:
- Risks/edge cases:

At the end of the plan, include:

## Changed in this revision
- Concise bullets describing what changed.
