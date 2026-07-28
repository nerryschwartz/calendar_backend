You are in clarification mode.

## When called from `/run-v2-implementation` (loop context)

Maps to `phase_N_request_questions` (**Plan** mode batch).

- Use **`AskQuestion`** for blocking questions; after the user answers, finish this substep and **`substep-complete`** it — do **not** stop with “Ready to draft or revise the plan.”
- Do **not** tell the user to run `/draft-plan`; the loop runs all Plan-block substeps, then the user switches to **Agent** mode once for `phase_N_agent_block`.
- If the turn ends on `AskQuestion`, set `pause_auto_resume true` first; after the user answers, finish the substep, `substep-complete` it, and continue the batch.

Goal:
Before writing or revising an implementation plan, before a bounded `/small-change`, or before `/build-plan-slice` when the prompt targets an approved plan slice, identify unresolved questions, edge cases, risks, and infeasibilities.

Parameter hygiene:
- Ignore trailing words attached to the slash command.
- Use only labeled fields in the current user message.
- Optional field:
  - Mode: plan|change
- If `Mode` is missing, assume `Mode: plan`.

Follow-up commands (`Mode: change` only):
- The same clarification output feeds the next implementation step.
- Use `/small-change` for bounded ad-hoc edits not tied to an approved plan slice.
- Use `/build-plan-slice` when the invoking prompt names a finalized plan in `docs/plans/` and a slice (for example `Slice: 1A`, or an explicit `/build-plan-slice` follow-up).
- Which follow-up command applies must be clear from the invoking prompt — do not guess.

Rules:
- Do not edit files.
- Do not create a plan yet (unless the user explicitly asks you to draft one after clarification).
- Do not replace an existing plan.
- Ask only questions that could materially change the implementation.
- Keep the question list concise.
- Prefer at most 3 blocking questions.
- Use your best judgment for minor details.
- If I answer with a clarifying question, answer it briefly, then re-ask the still-relevant question.
- If no material ambiguity remains:
  - `Mode: plan` — say: No blocking questions remain. Ready to draft or revise the plan.
  - `Mode: change` — say: No blocking questions remain. Ready for `/small-change` or `/build-plan-slice` (per your prompt).

Output format (`Mode: plan`):
1. Blocking questions
2. Non-blocking concerns
3. Safe assumptions

Output format (`Mode: change`):
1. Blocking questions
2. Non-blocking concerns
3. Safe assumptions
4. **Planned changes** — short bullet list of files and edits for the follow-up command (`/small-change` or `/build-plan-slice`); no new plan file
