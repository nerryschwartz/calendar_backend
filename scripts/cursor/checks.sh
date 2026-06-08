#!/usr/bin/env bash
set -euo pipefail

uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -m "not slow and not failure_expected"