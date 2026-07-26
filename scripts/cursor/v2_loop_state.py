"""Validate and advance `.cursor/v2_implementation_loop.json` for /run-v2-implementation."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

STATE_PATH = Path(".cursor/v2_implementation_loop.json")
STATE_VERSION = 1
MAX_V2_PHASE = 7
FINAL_PHASE_CHECKS = "phase_7_phase_checks"

PHASE_PLAN_PATHS: dict[int, str | None] = {
    0: None,
    1: "docs/plans/v2_flat_goal_children.md",
    2: "docs/plans/v2_prerequisites.md",
    3: "docs/plans/v2_block_orm.md",
    4: "docs/plans/v2_block_assignment.md",
    5: "docs/plans/v2_task_families.md",
    6: "docs/plans/v2_free_time_families.md",
    7: "docs/plans/v2_orchestration_integration.md",
}

PHASE0_REQUIRED_PATHS = (
    "docs/v2_engineering_design.md",
    "docs/v2_cursor_implementation_guide.md",
    ".cursor/rules/00-project-source-of-truth.mdc",
)

SLICE_HEADING_RE = re.compile(r"^###\s+Slice\s+([^:\n]+)", re.MULTILINE)
MIGRATION_SLICE_MARKERS = (
    "db-revision-preview",
    "/db-revision-preview",
    "db-revision-continue",
    "/db-revision-continue",
)


def git_branch() -> str:
    return subprocess.check_output(["git", "branch", "--show-current"], text=True).strip()


def repo_root() -> Path:
    return Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def save_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")


def phase0_complete(root: Path) -> bool:
    for relative in PHASE0_REQUIRED_PATHS:
        if not (root / relative).is_file():
            return False
    conventions = root / ".cursor/repo_conventions.md"
    if not conventions.is_file():
        return False
    text = conventions.read_text(encoding="utf-8")
    return "## 20. V2 design supersessions" in text


def required_mode(step_id: str) -> str:
    if step_id == "done":
        return "agent"
    if re.match(r"phase_\d+_(plan_bootstrap|request_questions)$", step_id):
        return "plan"
    return "agent"


def phase_from_step(step_id: str) -> int | None:
    match = re.match(r"phase_(\d+)_", step_id)
    if match:
        return int(match.group(1))
    if step_id == "done":
        return None
    return None


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


def slice_step_id(phase: int, slice_id: str, substep: str) -> str:
    return f"phase_{phase}_slice_{slice_id}_{substep}"


def first_incomplete_slice_step(state: dict[str, Any], root: Path) -> str | None:
    phase = state["current_phase"]
    plan_path = state.get("current_plan_path")
    if not plan_path:
        return None
    plan_file = root / plan_path
    completed = set(state.get("completed_steps", []))
    for slice_id in parse_slice_ids(plan_file):
        for substep in slice_substep_suffixes(plan_file, slice_id):
            step_id = slice_step_id(phase, slice_id, substep)
            if step_id not in completed:
                return step_id
    return None


def bootstrap_step(phase: int) -> str:
    if phase == 0:
        return "phase_0_verify"
    return f"phase_{phase}_plan_bootstrap"


def initial_state(*, phase: int | None = None) -> dict[str, Any]:
    root = repo_root()
    branch = git_branch()
    completed: list[str] = []
    if phase0_complete(root):
        completed.append("phase_0_verify")

    if phase is not None:
        current_phase = phase
    elif phase0_complete(root):
        current_phase = 1
    else:
        current_phase = 0

    next_step = bootstrap_step(current_phase)
    plan_path = PHASE_PLAN_PATHS.get(current_phase)

    return {
        "version": STATE_VERSION,
        "branch": branch,
        "next_required_mode": required_mode(next_step),
        "next_step": next_step,
        "current_phase": current_phase,
        "current_plan_path": plan_path,
        "current_slice_id": None,
        "skip_tests_on_commit": True,
        "completed_steps": completed,
        "alembic_group": None,
        "plan_mode_escape_hatches": [],
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
        "current_phase",
        "skip_tests_on_commit",
        "completed_steps",
    ):
        if field not in state:
            errors.append(f"missing field: {field}")

    mode = state.get("next_required_mode")
    step = state.get("next_step")
    if isinstance(step, str) and isinstance(mode, str):
        expected = required_mode(step)
        if mode != expected:
            errors.append(f"next_required_mode {mode!r} != expected {expected!r} for {step!r}")

    if state.get("plan_mode_escape_hatches") != []:
        errors.append("plan_mode_escape_hatches must be []")

    if not state.get("skip_tests_on_commit"):
        errors.append("skip_tests_on_commit must be true during loop")

    phase = state.get("current_phase")
    plan_path = state.get("current_plan_path")
    if isinstance(phase, int) and phase >= 1:
        expected_path = PHASE_PLAN_PATHS.get(phase)
        if plan_path != expected_path:
            errors.append(
                f"current_plan_path {plan_path!r} != expected {expected_path!r} for phase {phase}"
            )

    return errors


def _bootstrap_skip_done(root: Path, phase: int, substep: str, completed: set[str]) -> bool | None:
    plan_path = PHASE_PLAN_PATHS.get(phase)
    if substep == "plan_bootstrap":
        finalize_id = f"phase_{phase}_finalize_plan"
        return plan_path is not None and (root / plan_path).is_file() and finalize_id in completed
    if substep == "request_questions":
        return f"phase_{phase}_draft_plan" in completed
    if substep == "draft_plan":
        return plan_path is not None and (root / plan_path).is_file()
    if substep == "finalize_plan" and plan_path:
        plan_file = root / plan_path
        return plan_file.is_file() and bool(parse_slice_ids(plan_file))
    return None


def skip_if_done(state: dict[str, Any], step_id: str, root: Path | None = None) -> bool:  # noqa: PLR0911
    root = root or repo_root()
    completed = set(state.get("completed_steps", []))
    if step_id in completed:
        return True
    if step_id == "phase_0_verify":
        return phase0_complete(root)

    match = re.match(
        r"phase_(\d+)_(plan_bootstrap|request_questions|draft_plan|finalize_plan)$", step_id
    )
    if match:
        result = _bootstrap_skip_done(root, int(match.group(1)), match.group(2), completed)
        return bool(result)

    if step_id.endswith("_phase_checks"):
        if phase_from_step(step_id) is None or not state.get("current_plan_path"):
            return False
        return first_incomplete_slice_step(state, root) is None and step_id in completed

    if step_id == "done":
        return state.get("current_phase") == MAX_V2_PHASE and FINAL_PHASE_CHECKS in completed

    return False


def _set_next_step(state: dict[str, Any], next_step: str) -> dict[str, Any]:
    state["next_step"] = next_step
    state["next_required_mode"] = required_mode(next_step)
    return state


def _advance_from_phase0(state: dict[str, Any]) -> dict[str, Any]:
    state["current_phase"] = 1
    state["current_plan_path"] = PHASE_PLAN_PATHS[1]
    return _set_next_step(state, "phase_1_plan_bootstrap")


def _advance_from_bootstrap(
    state: dict[str, Any], phase: int, substep: str, root: Path
) -> dict[str, Any]:
    if substep == "plan_bootstrap":
        return _set_next_step(state, f"phase_{phase}_request_questions")
    if substep == "request_questions":
        return _set_next_step(state, f"phase_{phase}_draft_plan")
    if substep == "draft_plan":
        return _set_next_step(state, f"phase_{phase}_finalize_plan")

    first_slice = first_incomplete_slice_step(state, root)
    if first_slice:
        state["current_slice_id"] = first_slice.split("_slice_")[1].rsplit("_", 1)[0]
        return _set_next_step(state, first_slice)
    return _set_next_step(state, f"phase_{phase}_phase_checks")


def _slice_id_from_step(step_id: str) -> str:
    return step_id.split("_slice_")[1].rsplit("_", 1)[0]


def _advance_within_slice(
    state: dict[str, Any],
    *,
    phase: int,
    slice_id: str,
    substep: str,
    plan_file: Path | None,
    root: Path,
) -> dict[str, Any]:
    suffixes = slice_substep_suffixes(plan_file, slice_id) if plan_file else ["build"]
    try:
        index = suffixes.index(substep)
    except ValueError:
        index = len(suffixes) - 1

    if index + 1 < len(suffixes):
        next_substep = suffixes[index + 1]
        state["current_slice_id"] = slice_id
        state["alembic_group"] = {
            "slice_id": slice_id,
            "phase": phase,
            "substep": next_substep,
        }
        return _set_next_step(state, slice_step_id(phase, slice_id, next_substep))

    next_slice = first_incomplete_slice_step(state, root)
    if next_slice:
        state["current_slice_id"] = _slice_id_from_step(next_slice)
        state["alembic_group"] = None
        return _set_next_step(state, next_slice)

    state["current_slice_id"] = None
    state["alembic_group"] = None
    return _set_next_step(state, f"phase_{phase}_phase_checks")


def _advance_from_phase_checks(state: dict[str, Any], phase: int) -> dict[str, Any]:
    if phase >= MAX_V2_PHASE:
        return _set_next_step(state, "done")

    next_phase = phase + 1
    state["current_phase"] = next_phase
    state["current_plan_path"] = PHASE_PLAN_PATHS[next_phase]
    state["current_slice_id"] = None
    return _set_next_step(state, bootstrap_step(next_phase))


def advance_after_step(
    state: dict[str, Any], completed_step: str, root: Path | None = None
) -> dict[str, Any]:
    root = root or repo_root()
    completed = list(state.get("completed_steps", []))
    if completed_step not in completed:
        completed.append(completed_step)

    next_state = dict(state)
    next_state["completed_steps"] = completed

    if completed_step == "phase_0_verify":
        return _advance_from_phase0(next_state)

    bootstrap_match = re.match(
        r"phase_(\d+)_(plan_bootstrap|request_questions|draft_plan|finalize_plan)$",
        completed_step,
    )
    if bootstrap_match:
        return _advance_from_bootstrap(
            next_state, int(bootstrap_match.group(1)), bootstrap_match.group(2), root
        )

    slice_match = re.match(
        r"phase_(\d+)_slice_(.+)_(pre_alembic|alembic_preview|migration_manual_edit|alembic_continue|post_alembic|build)$",
        completed_step,
    )
    if slice_match:
        plan_path = next_state.get("current_plan_path")
        plan_file = root / plan_path if plan_path else None
        return _advance_within_slice(
            next_state,
            phase=int(slice_match.group(1)),
            slice_id=slice_match.group(2),
            substep=slice_match.group(3),
            plan_file=plan_file,
            root=root,
        )

    phase_checks_match = re.match(r"phase_(\d+)_phase_checks$", completed_step)
    if phase_checks_match:
        return _advance_from_phase_checks(next_state, int(phase_checks_match.group(1)))

    return next_state


def cmd_validate(_: argparse.Namespace) -> int:
    if not STATE_PATH.is_file():
        print(f"Missing state file: {STATE_PATH}")
        return 1
    state = load_state()
    errors = validate_state(state)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("State file is valid.")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    state = initial_state(phase=args.phase)
    save_state(state)
    print(json.dumps(state, indent=2))
    return 0


def complete_step(state: dict[str, Any], step_id: str) -> tuple[dict[str, Any], list[str]]:
    state = advance_after_step(state, step_id)
    return state, validate_state(state)


def cmd_complete(args: argparse.Namespace) -> int:
    if not STATE_PATH.is_file():
        print(f"Missing state file: {STATE_PATH}")
        return 1
    state = load_state()
    if state.get("next_step") != args.step_id:
        print(f"WARNING: completing {args.step_id!r} but next_step is {state.get('next_step')!r}")
    state, errors = complete_step(state, args.step_id)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    save_state(state)
    print(json.dumps(state, indent=2))
    return 0


def cmd_fast_forward(_args: argparse.Namespace) -> int:
    if not STATE_PATH.is_file():
        print(f"Missing state file: {STATE_PATH}")
        return 1
    state = load_state()
    completed_ids: list[str] = []
    for _ in range(64):
        step = state.get("next_step")
        if not isinstance(step, str) or step == "done":
            break
        is_bootstrap = re.match(r"phase_\d+_plan_bootstrap$", step) is not None
        if not is_bootstrap and not skip_if_done(state, step):
            break
        state, errors = complete_step(state, step)
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            return 1
        completed_ids.append(step)
    save_state(state)
    print(json.dumps({"fast_forwarded": completed_ids, "state": state}, indent=2))
    return 0


def cmd_show(_: argparse.Namespace) -> int:
    if not STATE_PATH.is_file():
        print(f"Missing state file: {STATE_PATH}")
        return 1
    state = load_state()
    print(json.dumps(state, indent=2))
    step = state.get("next_step")
    if isinstance(step, str):
        print(f"\nrequired_mode={required_mode(step)}")
        if skip_if_done(state, step):
            print(f"skip_if_done: {step} predicate already satisfied")
    return 0


def batch_step_ids(state: dict[str, Any]) -> tuple[str, list[str]]:
    step = state.get("next_step")
    if not isinstance(step, str):
        return "agent", []
    batch_mode = required_mode(step)
    steps: list[str] = []
    sim = dict(state)
    for _ in range(256):
        current = sim.get("next_step")
        if not isinstance(current, str):
            break
        if required_mode(current) != batch_mode:
            break
        steps.append(current)
        if current == "done":
            break
        sim, errors = complete_step(sim, current)
        if errors:
            break
    return batch_mode, steps


def batch_exit_check(state: dict[str, Any], batch_mode: str) -> dict[str, Any]:
    step = state.get("next_step")
    if not isinstance(step, str):
        return {
            "may_exit": True,
            "exit_kind": "unknown",
            "remaining_steps": [],
            "remaining_count": 0,
        }

    next_mode = required_mode(step)
    _, remaining = batch_step_ids(state)

    if step == "done":
        return {
            "may_exit": True,
            "exit_kind": "done",
            "batch_mode": batch_mode,
            "next_step": step,
            "next_required_mode": next_mode,
            "remaining_steps": [],
            "remaining_count": 0,
        }

    if next_mode == batch_mode:
        return {
            "may_exit": False,
            "exit_kind": "batch_incomplete",
            "batch_mode": batch_mode,
            "next_step": step,
            "next_required_mode": next_mode,
            "remaining_steps": remaining,
            "remaining_count": len(remaining),
        }

    return {
        "may_exit": True,
        "exit_kind": "mode_change",
        "batch_mode": batch_mode,
        "next_step": step,
        "next_required_mode": next_mode,
        "remaining_steps": [],
        "remaining_count": 0,
    }


def cmd_batch_steps(_: argparse.Namespace) -> int:
    if not STATE_PATH.is_file():
        print(f"Missing state file: {STATE_PATH}")
        return 1
    state = load_state()
    batch_mode, steps = batch_step_ids(state)
    print(
        json.dumps(
            {
                "batch_mode": batch_mode,
                "next_step": state.get("next_step"),
                "steps_in_batch": steps,
                "remaining_count": len(steps),
            },
            indent=2,
        )
    )
    return 0


def cmd_batch_exit_check(args: argparse.Namespace) -> int:
    if not STATE_PATH.is_file():
        print(f"Missing state file: {STATE_PATH}")
        return 1
    state = load_state()
    step = state.get("next_step")
    batch_mode = args.batch_mode
    if batch_mode is None:
        batch_mode = required_mode(step) if isinstance(step, str) else "agent"
    print(json.dumps(batch_exit_check(state, batch_mode), indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("validate", help="Validate state file schema and consistency")

    init_parser = subparsers.add_parser("init", help="Write initial state file")
    init_parser.add_argument("--reset", action="store_true", help="Reinitialize from scratch")
    init_parser.add_argument(
        "--phase",
        type=int,
        choices=range(8),
        help="Optional debug jump to phase 0-7 bootstrap",
    )

    complete_parser = subparsers.add_parser("complete", help="Mark a step complete and advance")
    complete_parser.add_argument("step_id", help="Step ID that finished in this invocation")

    subparsers.add_parser("show", help="Print current state and next step metadata")

    subparsers.add_parser(
        "fast-forward",
        help="Auto-complete skip-if-done steps until substantive work (agent-only)",
    )

    subparsers.add_parser(
        "batch-steps",
        help="List step IDs in the current mode batch (simulated advance)",
    )

    batch_exit_parser = subparsers.add_parser(
        "batch-exit-check",
        help="Return whether the agent may post a batch exit message",
    )
    batch_exit_parser.add_argument(
        "--batch-mode",
        choices=("plan", "agent"),
        help="batch_mode recorded at invocation start (default: required_mode of next_step)",
    )

    args = parser.parse_args()
    handlers = {
        "validate": cmd_validate,
        "init": cmd_init,
        "complete": cmd_complete,
        "show": cmd_show,
        "fast-forward": cmd_fast_forward,
        "batch-steps": cmd_batch_steps,
        "batch-exit-check": cmd_batch_exit_check,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
