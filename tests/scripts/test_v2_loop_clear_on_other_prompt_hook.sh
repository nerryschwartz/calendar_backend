#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CLEAR_HOOK="$ROOT/.cursor/hooks/v2-loop-clear-on-other-prompt.sh"
STOP_HOOK="$ROOT/.cursor/hooks/v2-loop-auto-resume.sh"
STATE_DIR="$ROOT/.cursor"
STATE_FILE="$STATE_DIR/v2_implementation_loop.json"
BACKUP=""

cleanup() {
  if [[ -n "$BACKUP" && -f "$BACKUP" ]]; then
    mv "$BACKUP" "$STATE_FILE"
  elif [[ -n "$BACKUP" && ! -f "$BACKUP" ]]; then
    rm -f "$STATE_FILE"
  fi
}
trap cleanup EXIT

run_clear_hook() {
  local prompt="$1"
  printf '%s' "$(python3 -c 'import json,sys; print(json.dumps({"prompt": sys.argv[1]}))' "$prompt")" | bash "$CLEAR_HOOK"
}

run_stop_hook() {
  echo '{"status":"completed"}' | bash "$STOP_HOOK"
}

assert_continue() {
  local output="$1"
  echo "$output" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d.get("continue") is True, d
'
}

assert_no_followup() {
  local output="$1"
  echo "$output" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if not d else 1)'
}

assert_followup() {
  local output="$1"
  echo "$output" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d.get("followup_message") == "/run-v2-implementation", d
'
}

if [[ -f "$STATE_FILE" ]]; then
  BACKUP="$(mktemp)"
  cp "$STATE_FILE" "$BACKUP"
fi

mkdir -p "$STATE_DIR"

cat >"$STATE_FILE" <<'EOF'
{
  "version": 1,
  "branch": "main",
  "next_required_mode": "agent",
  "next_step": "phase_3_agent_block",
  "current_phase": 3,
  "current_plan_path": "docs/plans/v2_block_orm.md",
  "current_slice_id": null,
  "skip_tests_on_commit": true,
  "completed_steps": ["phase_3_plan_bootstrap", "phase_3_request_questions", "phase_3_draft_plan"],
  "alembic_group": null,
  "plan_mode_escape_hatches": [],
  "pause_auto_resume": false,
  "active_batch_mode": "agent"
}
EOF

assert_continue "$(run_clear_hook "/run-v2-implementation")"
active="$(python3 -c 'import json; print(json.load(open("'"$STATE_FILE"'")).get("active_batch_mode"))')"
if [[ "$active" != "agent" ]]; then
  echo "Expected active_batch_mode to remain agent for loop prompt, got $active" >&2
  exit 1
fi

assert_continue "$(run_clear_hook "/commit-changes")"
active="$(python3 -c 'import json; print(json.load(open("'"$STATE_FILE"'")).get("active_batch_mode"))')"
if [[ "$active" != "None" ]]; then
  echo "Expected active_batch_mode cleared after /commit-changes, got $active" >&2
  exit 1
fi

assert_no_followup "$(run_stop_hook)"

cat >"$STATE_FILE" <<'EOF'
{
  "version": 1,
  "branch": "main",
  "next_required_mode": "agent",
  "next_step": "phase_3_agent_block",
  "current_phase": 3,
  "current_plan_path": "docs/plans/v2_block_orm.md",
  "current_slice_id": null,
  "skip_tests_on_commit": true,
  "completed_steps": ["phase_3_plan_bootstrap", "phase_3_request_questions", "phase_3_draft_plan"],
  "alembic_group": null,
  "plan_mode_escape_hatches": [],
  "pause_auto_resume": false,
  "active_batch_mode": "agent"
}
EOF

assert_followup "$(run_stop_hook)"

echo "test_v2_loop_clear_on_other_prompt_hook.sh: ok"
