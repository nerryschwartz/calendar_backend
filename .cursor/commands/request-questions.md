You are in clarification mode.

Goal:
Before writing or revising an implementation plan, or before a bounded `/small-change`, identify unresolved questions, edge cases, risks, and infeasibilities.

Parameter hygiene:
- Ignore trailing words attached to the slash command.
- Use only labeled fields in the current user message.
- Optional field:
  - Mode: plan|change
- If `Mode` is missing, assume `Mode: plan`.

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
  - `Mode: change` — say: No blocking questions remain. Ready for `/small-change`.

Output format (`Mode: plan`):
1. Blocking questions
2. Non-blocking concerns
3. Safe assumptions

Output format (`Mode: change`):
1. Blocking questions
2. Non-blocking concerns
3. Safe assumptions
4. **Planned changes** — short bullet list of files and edits that `/small-change` will make (no plan file)
