#!/usr/bin/env bash
set -euo pipefail

input="$(cat)"
status="$(echo "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))')"

if [[ "$status" != "completed" ]]; then
  echo '{}'
  exit 0
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
state_file="$repo_root/.cursor/v3_implementation_loop.json"

if [[ ! -f "$state_file" ]]; then
  echo '{}'
  exit 0
fi

pause_auto_resume="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("pause_auto_resume", False))' "$state_file")"
if [[ "$pause_auto_resume" == "True" ]]; then
  echo '{}'
  exit 0
fi

active_batch_mode="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("active_batch_mode"))' "$state_file")"
if [[ "$active_batch_mode" == "None" || -z "$active_batch_mode" ]]; then
  echo '{}'
  exit 0
fi

cd "$repo_root"
exit_json="$(uv run python scripts/cursor/v3_loop_state.py batch-exit-check --batch-mode "$active_batch_mode")"
may_exit="$(echo "$exit_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("may_exit", True))')"

if [[ "$may_exit" == "True" ]]; then
  exit_kind="$(echo "$exit_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("exit_kind",""))')"
  uv run python scripts/cursor/v3_loop_state.py clear-active-batch-mode >/dev/null
  if [[ "$exit_kind" == "done" ]]; then
    uv run python scripts/cursor/v3_loop_state.py set-pause-auto-resume true >/dev/null
  fi
  echo '{}'
  exit 0
fi

python3 -c 'import json; print(json.dumps({"followup_message": "/run-v3-implementation"}))'
