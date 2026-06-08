#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/cursor/new_branch.sh <branch-name>"
  exit 1
fi

branch="$1"

git status --short

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree is not clean. Commit or stash changes before creating a new branch."
  exit 1
fi

git checkout main
git pull --ff-only
git checkout -b "$branch"