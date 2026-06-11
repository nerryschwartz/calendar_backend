Analyze private helpers changed by the git diff for one specified Python file.

Parameter hygiene:
- Ignore trailing words attached to the slash command.
- Use only labeled fields in the current user message.
- Required field:
  - File: <path>
- If File is missing, ask for it.
- Require exactly one file path.
- Do not infer the file from previous invocations.

Rules:
- Read-only.
- Do not edit files.
- Do not run formatters.
- Do not stage or commit.
- The file must be a Python file ending in .py.
- Use `git diff -- <file>` as the source of diff truth.
- Inspect the current file for line numbers and call context.
- A private helper is a Python function or method whose name starts with exactly one underscore, such as `_helper`, not `__dunder` and not public functions.
- Include helpers that are newly defined in the diff.
- Include helpers that existed before but have changed lines in the diff.
- Treat every function/method not newly defined in the diff as an external call site.
- External call sites include:
  - existing public functions
  - existing private helpers
  - newly defined public functions

Workflow:
1. Read the `File:` field from the current message.
2. Confirm the file path ends in `.py`.
3. Run `git diff -- <file>`.
4. If there is no diff for the file, report that and stop.
5. Find newly defined helpers from added `def _name` or `async def _name` lines.
6. Find changed existing helpers by mapping changed diff lines back to enclosing function or method definitions in the current file.
7. Parse or inspect the current file to determine helper-to-helper calls.
   - Prefer Python AST when practical.
   - Use text search only as a fallback.
8. Find external call sites: functions or methods not newly defined in this diff that call any newly defined or changed private helper.
9. For every changed private helper, summarize its role in prose.
   - Include important helper calls inside the role sentence.
   - Do not produce separate generic helper/non-helper call lists.
10. For every helper called from an external call site, render a compact helper-only call tree.

Output format:

## Private Helpers Defined Or Changed In This Diff

For each helper:
- Use a short heading with the helper name.
- Provide a clickable definition link with a line number.
- Summarize what the helper does and how it uses any helper calls.
- Keep summaries concise.

## Call Sites From Functions Not Newly Defined In This Diff

Group by caller.

For each call site:
- Link to the call line.
- Explain when or why the caller invokes the helper.
- Include existing private callers here too if they are not newly defined in the diff.

## Helper Call Trees For Helpers Called From Non-New Functions

For each helper that is called from an external call site:
- Render one compact helper-only tree.
- Prefer Mermaid if the current renderer supports it.
- If Mermaid does not render cleanly, use the plain-text fallback format below.
- Use only private helpers from this report.
- Exclude ordinary calls such as `get_cfg`, `pd.to_datetime`, constructors, library calls, or public functions.
- Keep trees small:
  - no repeated subtrees
  - no non-helper calls
  - no prose inside nodes
- For recursion, show a back-edge or annotate the recursive helper once.

Preferred Mermaid shape when supported:

MERMAID_START
flowchart LR
    h0["_helper_a"]
    h1["_helper_b"]
    h0 --> h1
MERMAID_END

When producing actual Mermaid output, replace MERMAID_START and MERMAID_END with a normal fenced mermaid code block.

Plain-text fallback shape:

_helper_a
└── _helper_b

Recursive plain-text fallback shape:

_walk
└── _children
    └── _walk (recursive)