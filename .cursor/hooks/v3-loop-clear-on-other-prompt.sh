#!/usr/bin/env bash
set -euo pipefail

input="$(cat)"
prompt="$(echo "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("prompt",""))')"

if [[ "$prompt" == "/run-v3-implementation"* ]]; then
  echo '{}'
  exit 0
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
state_file="$repo_root/.cursor/v3_implementation_loop.json"

if [[ ! -f "$state_file" ]]; then
  echo '{}'
  exit 0
fi

cd "$repo_root"
uv run python scripts/cursor/v3_loop_state.py clear-active-batch-mode >/dev/null
echo '{}'
