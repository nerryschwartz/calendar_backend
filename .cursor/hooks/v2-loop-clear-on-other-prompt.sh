#!/usr/bin/env bash
set -euo pipefail

input="$(cat)"
prompt="$(echo "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("prompt",""))')"

if python3 -c 'import re,sys; sys.exit(0 if re.match(r"^/run-v2-implementation(\s|$)", sys.argv[1]) else 1)' "$prompt"; then
  python3 -c 'import json; print(json.dumps({"continue": True}))'
  exit 0
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
state_file="$repo_root/.cursor/v2_implementation_loop.json"

if [[ ! -f "$state_file" ]]; then
  python3 -c 'import json; print(json.dumps({"continue": True}))'
  exit 0
fi

active_batch_mode="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("active_batch_mode"))' "$state_file")"
if [[ "$active_batch_mode" == "None" || -z "$active_batch_mode" ]]; then
  python3 -c 'import json; print(json.dumps({"continue": True}))'
  exit 0
fi

cd "$repo_root"
uv run python scripts/cursor/v2_loop_state.py clear-active-batch-mode >/dev/null
python3 -c 'import json; print(json.dumps({"continue": True}))'
