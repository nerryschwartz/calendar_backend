#!/usr/bin/env bash
set -euo pipefail

echo "## Branch"
git branch --show-current

echo
echo "## Status"
git status --short

echo
echo "## Diff stat"
git diff --stat

echo
echo "## Changed files"
git diff --name-status

echo
echo "## Staged files"
git diff --cached --name-status