#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOOK="$ROOT/.cursor/hooks/v2-loop-auto-resume.sh"
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

run_hook() {
  echo '{"status":"completed"}' | bash "$HOOK"
}

assert_no_followup() {
  local output="$1"
  if echo "$output" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if not d else 1)'; then
    return 0
  fi
  echo "Expected empty hook output, got: $output" >&2
  return 1
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

# Idle: no active batch -> no followup
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
  "completed_steps": ["phase_3_plan_bootstrap", "phase_3_request_questions"],
  "alembic_group": null,
  "plan_mode_escape_hatches": [],
  "pause_auto_resume": false,
  "active_batch_mode": null
}
EOF
assert_no_followup "$(run_hook)"

# Plan handoff: finished Plan stretch -> no followup; clears active_batch_mode and pauses
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
  "completed_steps": ["phase_3_plan_bootstrap", "phase_3_request_questions"],
  "alembic_group": null,
  "plan_mode_escape_hatches": [],
  "pause_auto_resume": false,
  "active_batch_mode": "plan"
}
EOF
assert_no_followup "$(run_hook)"
paused="$(python3 -c 'import json; print(json.load(open("'"$STATE_FILE"'")).get("pause_auto_resume"))')"
active="$(python3 -c 'import json; print(json.load(open("'"$STATE_FILE"'")).get("active_batch_mode"))')"
if [[ "$paused" != "True" ]]; then
  echo "Expected pause_auto_resume true after Plan handoff, got $paused" >&2
  exit 1
fi
if [[ "$active" != "None" ]]; then
  echo "Expected active_batch_mode cleared after Plan handoff, got $active" >&2
  exit 1
fi

# Mid-batch Agent stretch -> followup
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
assert_followup "$(run_hook)"

echo "test_v2_loop_auto_resume_hook.sh: ok"
