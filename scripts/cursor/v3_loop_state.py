"""Validate and advance `.cursor/v3_implementation_loop.json` for /run-v3-implementation."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

STATE_PATH = Path(".cursor/v3_implementation_loop.json")
STATE_VERSION = 1
PLAN_PATH = "docs/plans/v3_frontend_integration.md"
MACRO_BLOCK_ID = "v3_agent_block"
FINAL_CHECKS = "v3_final_checks"

SLICE_HEADING_RE = re.compile(r"^###\s+Slice\s+([^:\n]+)", re.MULTILINE)
MIGRATION_SLICE_MARKERS = (
    "db-revision-preview",
    "/db-revision-preview",
    "db-revision-continue",
    "/db-revision-continue",
)
SLICE_SUBSTEP_RE = re.compile(
    r"slice_(.+)_(pre_alembic|alembic_preview|migration_manual_edit|alembic_continue|post_alembic|build)$"
)

PHASE0_REQUIRED_PATHS = (
    "docs/v3_engineering_design.md",
    "docs/v3_cursor_implementation_guide.md",
    ".cursor/rules/00-project-source-of-truth.mdc",
)


def git_branch() -> str:
    return subprocess.check_output(["git", "branch", "--show-current"], text=True).strip()


def repo_root() -> Path:
    return Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())


def load_state(path: Path | None = None) -> dict[str, Any]:
    state_path = path if path is not None else STATE_PATH
    with state_path.open(encoding="utf-8") as handle:
        return json.load(handle)


def save_state(state: dict[str, Any], path: Path | None = None) -> None:
    state_path = path if path is not None else STATE_PATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")


def phase0_complete(root: Path) -> bool:
    return all((root / relative).is_file() for relative in PHASE0_REQUIRED_PATHS)


def required_mode(_step_id: str) -> str:
    return "agent"


def parse_slice_ids(plan_path: Path) -> list[str]:
    if not plan_path.is_file():
        return []
    text = plan_path.read_text(encoding="utf-8")
    slices_section = text.split("## Slices", 1)
    body = slices_section[1] if len(slices_section) > 1 else text
    ids: list[str] = []
    for match in SLICE_HEADING_RE.finditer(body):
        slug = match.group(1).strip().lower()
        slug = re.sub(r"\s+", "-", slug)
        ids.append(slug)
    return ids


def slice_section_text(plan_path: Path, slice_id: str) -> str:
    if not plan_path.is_file():
        return ""
    text = plan_path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^###\s+Slice\s+{re.escape(slice_id)}\b.*?(?=^###\s+Slice\s|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(text)
    return match.group(0) if match else ""


def slice_substep_suffixes(plan_path: Path, slice_id: str) -> list[str]:
    section = slice_section_text(plan_path, slice_id)
    if any(marker in section for marker in MIGRATION_SLICE_MARKERS):
        return [
            "pre_alembic",
            "alembic_preview",
            "migration_manual_edit",
            "alembic_continue",
            "post_alembic",
        ]
    return ["build"]


def slice_step_id(slice_id: str, substep: str) -> str:
    return f"slice_{slice_id}_{substep}"


def agent_block_substeps(root: Path) -> list[str]:
    steps: list[str] = []
    if not phase0_complete(root):
        steps.append("phase_0_verify")
    plan_file = root / PLAN_PATH
    for slice_id in parse_slice_ids(plan_file):
        for substep in slice_substep_suffixes(plan_file, slice_id):
            steps.append(slice_step_id(slice_id, substep))
    steps.append(FINAL_CHECKS)
    return steps


def block_substeps_remaining(state: dict[str, Any], root: Path) -> list[str]:
    completed = set(state.get("completed_steps", []))
    return [step for step in agent_block_substeps(root) if step not in completed]


def current_substep(state: dict[str, Any], root: Path | None = None) -> str | None:
    root = root or repo_root()
    remaining = block_substeps_remaining(state, root)
    return remaining[0] if remaining else None


def normalize_state(state: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    next_state = dict(state)
    if "pause_auto_resume" not in next_state:
        next_state["pause_auto_resume"] = False
    if "active_batch_mode" not in next_state:
        next_state["active_batch_mode"] = None
    step = next_state.get("next_step")
    if step not in {"done", MACRO_BLOCK_ID}:
        next_state["next_step"] = MACRO_BLOCK_ID
    next_state["next_required_mode"] = "agent"
    next_state["current_plan_path"] = PLAN_PATH
    return next_state


def initial_state() -> dict[str, Any]:
    root = repo_root()
    completed: list[str] = []
    if phase0_complete(root):
        completed.append("phase_0_verify")
    return {
        "version": STATE_VERSION,
        "branch": git_branch(),
        "next_required_mode": "agent",
        "next_step": MACRO_BLOCK_ID,
        "current_plan_path": PLAN_PATH,
        "current_slice_id": None,
        "skip_tests_on_commit": True,
        "completed_steps": completed,
        "alembic_group": None,
        "pause_auto_resume": False,
        "active_batch_mode": None,
    }


def validate_state(state: dict[str, Any], root: Path | None = None) -> list[str]:
    root = root or repo_root()
    errors: list[str] = []
    if state.get("version") != STATE_VERSION:
        errors.append(f"version must be {STATE_VERSION}")
    for field in (
        "branch",
        "next_required_mode",
        "next_step",
        "skip_tests_on_commit",
        "completed_steps",
        "pause_auto_resume",
    ):
        if field not in state:
            errors.append(f"missing field: {field}")
    if state.get("next_required_mode") != "agent":
        errors.append("next_required_mode must be 'agent'")
    if state.get("next_step") not in {MACRO_BLOCK_ID, "done"}:
        errors.append(f"next_step must be {MACRO_BLOCK_ID!r} or 'done'")
    if not state.get("skip_tests_on_commit"):
        errors.append("skip_tests_on_commit must be true during loop")
    if state.get("current_plan_path") != PLAN_PATH:
        errors.append(f"current_plan_path must be {PLAN_PATH!r}")
    return errors


def skip_if_done(state: dict[str, Any], step_id: str, root: Path | None = None) -> bool:
    root = root or repo_root()
    completed = set(state.get("completed_steps", []))
    if step_id in completed:
        return True
    if step_id == "phase_0_verify":
        return phase0_complete(root)
    if step_id == FINAL_CHECKS:
        return False
    return False


def substep_complete(
    state: dict[str, Any], step_id: str, root: Path | None = None
) -> tuple[dict[str, Any], list[str]]:
    root = root or repo_root()
    errors: list[str] = []
    if step_id not in agent_block_substeps(root):
        errors.append(f"unknown substep: {step_id}")
        return state, errors
    completed = list(state.get("completed_steps", []))
    if step_id not in completed:
        completed.append(step_id)
    state = dict(state)
    state["completed_steps"] = completed
    match = SLICE_SUBSTEP_RE.match(step_id)
    if match:
        state["current_slice_id"] = match.group(1)
    remaining = block_substeps_remaining(state, root)
    if not remaining:
        state["next_step"] = "done"
        state["current_slice_id"] = None
    else:
        state["next_step"] = MACRO_BLOCK_ID
    state["next_required_mode"] = "agent"
    return state, errors


def batch_exit_check(
    state: dict[str, Any], batch_mode: str, root: Path | None = None
) -> dict[str, Any]:
    root = root or repo_root()
    state = normalize_state(state, root)
    step = state.get("next_step")
    substeps_remaining = block_substeps_remaining(state, root) if step == MACRO_BLOCK_ID else []
    if step == "done":
        return {
            "may_exit": True,
            "exit_kind": "done",
            "batch_mode": batch_mode,
            "next_step": step,
            "next_required_mode": "agent",
            "substeps_remaining": [],
            "substeps_remaining_count": 0,
        }
    return {
        "may_exit": False,
        "exit_kind": "batch_incomplete",
        "batch_mode": batch_mode,
        "next_step": step,
        "next_required_mode": "agent",
        "substeps_remaining": substeps_remaining,
        "substeps_remaining_count": len(substeps_remaining),
    }


def cmd_init(args: argparse.Namespace) -> int:
    if STATE_PATH.is_file() and not args.reset:
        print(f"State file already exists: {STATE_PATH}")
        return 1
    save_state(initial_state())
    print(json.dumps(load_state(), indent=2))
    return 0


def cmd_validate(_: argparse.Namespace) -> int:
    if not STATE_PATH.is_file():
        print(f"Missing state file: {STATE_PATH}")
        return 1
    root = repo_root()
    state = normalize_state(load_state(), root)
    errors = validate_state(state, root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    save_state(state)
    print("OK")
    return 0


def cmd_substep_complete(args: argparse.Namespace) -> int:
    root = repo_root()
    state = normalize_state(load_state(), root)
    state, errors = substep_complete(state, args.substep_id, root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    save_state(state)
    print(json.dumps(state, indent=2))
    return 0


def cmd_current_substep(_: argparse.Namespace) -> int:
    root = repo_root()
    state = normalize_state(load_state(), root)
    sub = current_substep(state, root)
    print(json.dumps({"current_substep": sub, "next_step": state.get("next_step")}, indent=2))
    return 0


def cmd_batch_steps(_: argparse.Namespace) -> int:
    root = repo_root()
    state = normalize_state(load_state(), root)
    substeps = block_substeps_remaining(state, root)
    print(
        json.dumps(
            {
                "batch_mode": "agent",
                "next_step": state.get("next_step"),
                "steps_in_batch": [MACRO_BLOCK_ID],
                "substeps_remaining": substeps,
                "substeps_remaining_count": len(substeps),
            },
            indent=2,
        )
    )
    return 0


def cmd_batch_exit_check(args: argparse.Namespace) -> int:
    root = repo_root()
    state = normalize_state(load_state(), root)
    print(json.dumps(batch_exit_check(state, args.batch_mode, root), indent=2))
    return 0


def cmd_set_pause_auto_resume(args: argparse.Namespace) -> int:
    state = load_state()
    state["pause_auto_resume"] = args.value.lower() == "true"
    save_state(state)
    return 0


def cmd_set_active_batch_mode(args: argparse.Namespace) -> int:
    state = load_state()
    mode = args.mode
    state["active_batch_mode"] = None if mode in {"null", "none"} else mode
    save_state(state)
    return 0


def cmd_clear_active_batch_mode(_: argparse.Namespace) -> int:
    state = load_state()
    state["active_batch_mode"] = None
    save_state(state)
    return 0


def cmd_fast_forward(_: argparse.Namespace) -> int:
    root = repo_root()
    state = normalize_state(load_state(), root)
    completed_ids: list[str] = []
    for _ in range(128):
        sub = current_substep(state, root)
        if sub is None or not skip_if_done(state, sub, root):
            break
        state, errors = substep_complete(state, sub, root)
        if errors:
            return 1
        completed_ids.append(sub)
    save_state(state)
    print(json.dumps({"fast_forwarded": completed_ids}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    init_p = sub.add_parser("init")
    init_p.add_argument("--reset", action="store_true")
    sub.add_parser("validate")
    sc = sub.add_parser("substep-complete")
    sc.add_argument("substep_id")
    sub.add_parser("current-substep")
    sub.add_parser("batch-steps")
    bec = sub.add_parser("batch-exit-check")
    bec.add_argument("--batch-mode", required=True)
    sp = sub.add_parser("set-pause-auto-resume")
    sp.add_argument("value")
    sab = sub.add_parser("set-active-batch-mode")
    sab.add_argument("mode")
    sub.add_parser("clear-active-batch-mode")
    sub.add_parser("fast-forward")
    args = parser.parse_args()
    handlers = {
        "init": cmd_init,
        "validate": cmd_validate,
        "substep-complete": cmd_substep_complete,
        "current-substep": cmd_current_substep,
        "batch-steps": cmd_batch_steps,
        "batch-exit-check": cmd_batch_exit_check,
        "set-pause-auto-resume": cmd_set_pause_auto_resume,
        "set-active-batch-mode": cmd_set_active_batch_mode,
        "clear-active-batch-mode": cmd_clear_active_batch_mode,
        "fast-forward": cmd_fast_forward,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
