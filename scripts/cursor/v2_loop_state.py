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

MACRO_BLOCK_RE = re.compile(r"phase_(\d+)_(plan_block|agent_block)$")
PLAN_SUBSTEP_RE = re.compile(r"phase_(\d+)_(plan_bootstrap|request_questions)$")
BOOTSTRAP_SUBSTEP_RE = re.compile(
    r"phase_(\d+)_(plan_bootstrap|request_questions|draft_plan|finalize_plan)$"
)
SLICE_SUBSTEP_RE = re.compile(
    r"phase_(\d+)_slice_(.+)_(pre_alembic|alembic_preview|migration_manual_edit|alembic_continue|post_alembic|build)$"
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


def plan_block_id(phase: int) -> str:
    return f"phase_{phase}_plan_block"


def agent_block_id(phase: int) -> str:
    return f"phase_{phase}_agent_block"


def is_macro_block(step_id: str) -> bool:
    return MACRO_BLOCK_RE.match(step_id) is not None


def is_granular_step(step_id: str) -> bool:
    if step_id in {"phase_0_verify", "done"}:
        return True
    if is_macro_block(step_id):
        return False
    return (
        PLAN_SUBSTEP_RE.match(step_id) is not None
        or BOOTSTRAP_SUBSTEP_RE.match(step_id) is not None
        or SLICE_SUBSTEP_RE.match(step_id) is not None
        or re.match(r"phase_\d+_phase_checks$", step_id) is not None
    )


def block_id_for_granular(step_id: str) -> str | None:
    if step_id in {"phase_0_verify", "done"}:
        return None
    plan_match = PLAN_SUBSTEP_RE.match(step_id)
    if plan_match:
        return plan_block_id(int(plan_match.group(1)))
    phase = phase_from_step(step_id)
    if phase is None:
        return None
    if BOOTSTRAP_SUBSTEP_RE.match(step_id) or SLICE_SUBSTEP_RE.match(step_id):
        return agent_block_id(phase)
    if re.match(rf"phase_{phase}_phase_checks$", step_id):
        return agent_block_id(phase)
    return None


def macro_step_for_next_step(step_id: str) -> str:
    block = block_id_for_granular(step_id)
    return block if block is not None else step_id


def required_mode(step_id: str) -> str:
    if step_id == "done":
        return "agent"
    if step_id == "phase_0_verify":
        return "agent"
    if re.match(r"phase_\d+_plan_block$", step_id):
        return "plan"
    if PLAN_SUBSTEP_RE.match(step_id):
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


def plan_block_substeps(phase: int) -> list[str]:
    return [f"phase_{phase}_plan_bootstrap", f"phase_{phase}_request_questions"]


def agent_block_substeps(state: dict[str, Any], root: Path, phase: int) -> list[str]:
    steps = [f"phase_{phase}_draft_plan", f"phase_{phase}_finalize_plan"]
    plan_path = state.get("current_plan_path")
    if plan_path:
        plan_file = root / plan_path
        for slice_id in parse_slice_ids(plan_file):
            for substep in slice_substep_suffixes(plan_file, slice_id):
                steps.append(slice_step_id(phase, slice_id, substep))
    steps.append(f"phase_{phase}_phase_checks")
    return steps


def substeps_for_block(state: dict[str, Any], block_id: str, root: Path) -> list[str]:
    match = MACRO_BLOCK_RE.match(block_id)
    if not match:
        return []
    phase = int(match.group(1))
    if match.group(2) == "plan_block":
        return plan_block_substeps(phase)
    return agent_block_substeps(state, root, phase)


def block_substeps_remaining(state: dict[str, Any], block_id: str, root: Path) -> list[str]:
    completed = set(state.get("completed_steps", []))
    return [step for step in substeps_for_block(state, block_id, root) if step not in completed]


def normalize_state(state: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    next_state = dict(state)
    if "pause_auto_resume" not in next_state:
        next_state["pause_auto_resume"] = False
    step = next_state.get("next_step")
    if not isinstance(step, str):
        return next_state
    if step in {"done", "phase_0_verify"} or is_macro_block(step):
        next_state["next_required_mode"] = required_mode(step)
        return next_state
    macro = macro_step_for_next_step(step)
    next_state["next_step"] = macro
    next_state["next_required_mode"] = required_mode(macro)
    return next_state


def bootstrap_step(phase: int) -> str:
    if phase == 0:
        return "phase_0_verify"
    return plan_block_id(phase)


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
        "pause_auto_resume": False,
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
        "pause_auto_resume",
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

    if not isinstance(state.get("pause_auto_resume"), bool):
        errors.append("pause_auto_resume must be a boolean")

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

    next_step = state.get("next_step")
    if (
        isinstance(next_step, str)
        and is_granular_step(next_step)
        and next_step
        not in {
            "phase_0_verify",
            "done",
        }
    ):
        errors.append(
            f"next_step {next_step!r} must be a macro block "
            "(run validate to normalize legacy state)"
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
    return _set_next_step(state, plan_block_id(1))


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

    slice_match = SLICE_SUBSTEP_RE.match(completed_step)
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


def current_substep(state: dict[str, Any], root: Path | None = None) -> str | None:
    root = root or repo_root()
    state = normalize_state(state, root)
    step = state.get("next_step")
    if not isinstance(step, str):
        return None
    if step == "done":
        return None
    if step == "phase_0_verify":
        completed = set(state.get("completed_steps", []))
        return None if "phase_0_verify" in completed else "phase_0_verify"
    if is_macro_block(step):
        remaining = block_substeps_remaining(state, step, root)
        return remaining[0] if remaining else None
    return step


def _finalize_substep_complete(
    state: dict[str, Any], *, prior_block: str | None, root: Path
) -> dict[str, Any]:
    if prior_block == "phase_0_verify" or state.get("next_step") == "done":
        return normalize_state(state, root)

    granular_next = state.get("next_step")
    if not isinstance(granular_next, str):
        return normalize_state(state, root)

    if prior_block and is_macro_block(prior_block):
        if block_substeps_remaining(state, prior_block, root):
            return _set_next_step(state, prior_block)
        next_macro = macro_step_for_next_step(granular_next)
        return _set_next_step(state, next_macro)

    return normalize_state(state, root)


def substep_complete(
    state: dict[str, Any], substep_id: str, root: Path | None = None
) -> tuple[dict[str, Any], list[str]]:
    root = root or repo_root()
    normalized = normalize_state(state, root)
    prior_block = normalized.get("next_step")
    if not isinstance(prior_block, str):
        prior_block = None

    expected = current_substep(normalized, root)
    if expected is not None and substep_id != expected:
        print(
            f"WARNING: substep-complete {substep_id!r} but current substep is {expected!r}",
            file=sys.stderr,
        )

    advanced = advance_after_step(normalized, substep_id, root)
    finalized = _finalize_substep_complete(advanced, prior_block=prior_block, root=root)
    return finalized, validate_state(finalized, root)


def cmd_validate(_: argparse.Namespace) -> int:
    if not STATE_PATH.is_file():
        print(f"Missing state file: {STATE_PATH}")
        return 1
    root = repo_root()
    state = load_state()
    normalized = normalize_state(state, root)
    if normalized != state:
        save_state(normalized)
        print("Normalized legacy next_step to macro block.")
        state = normalized
    errors = validate_state(state, root)
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
    return substep_complete(state, step_id)


def cmd_complete(args: argparse.Namespace) -> int:
    if not STATE_PATH.is_file():
        print(f"Missing state file: {STATE_PATH}")
        return 1
    state = load_state()
    expected = current_substep(state)
    if expected is not None and args.step_id != expected:
        print(
            f"WARNING: completing {args.step_id!r} but current substep is {expected!r}",
            file=sys.stderr,
        )
    state, errors = substep_complete(state, args.step_id)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    save_state(state)
    print(json.dumps(state, indent=2))
    return 0


def cmd_substep_complete(args: argparse.Namespace) -> int:
    return cmd_complete(args)


def cmd_current_substep(_: argparse.Namespace) -> int:
    if not STATE_PATH.is_file():
        print(f"Missing state file: {STATE_PATH}")
        return 1
    root = repo_root()
    state = normalize_state(load_state(), root)
    substep = current_substep(state, root)
    block = state.get("next_step")
    remaining: list[str] = []
    if isinstance(block, str) and is_macro_block(block):
        remaining = block_substeps_remaining(state, block, root)
    print(
        json.dumps(
            {
                "next_step": block,
                "current_substep": substep,
                "substeps_remaining": remaining,
                "remaining_count": len(remaining),
            },
            indent=2,
        )
    )
    return 0


def cmd_set_pause_auto_resume(args: argparse.Namespace) -> int:
    if not STATE_PATH.is_file():
        print(f"Missing state file: {STATE_PATH}")
        return 1
    state = load_state()
    state["pause_auto_resume"] = args.paused == "true"
    save_state(state)
    print(json.dumps({"pause_auto_resume": state["pause_auto_resume"]}, indent=2))
    return 0


def cmd_fast_forward(_args: argparse.Namespace) -> int:
    if not STATE_PATH.is_file():
        print(f"Missing state file: {STATE_PATH}")
        return 1
    root = repo_root()
    state = normalize_state(load_state(), root)
    completed_ids: list[str] = []
    for _ in range(64):
        sub = current_substep(state, root)
        if sub is None or not skip_if_done(state, sub, root):
            break
        state, errors = substep_complete(state, sub, root)
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            return 1
        completed_ids.append(sub)
    save_state(state)
    print(json.dumps({"fast_forwarded": completed_ids, "state": state}, indent=2))
    return 0


def cmd_show(_: argparse.Namespace) -> int:
    if not STATE_PATH.is_file():
        print(f"Missing state file: {STATE_PATH}")
        return 1
    root = repo_root()
    state = normalize_state(load_state(), root)
    print(json.dumps(state, indent=2))
    step = state.get("next_step")
    if isinstance(step, str):
        print(f"\nrequired_mode={required_mode(step)}")
        sub = current_substep(state, root)
        if sub:
            print(f"current_substep={sub}")
        if skip_if_done(state, sub or step, root):
            print(f"skip_if_done: {(sub or step)!r} predicate already satisfied")
    return 0


def batch_step_ids(state: dict[str, Any], root: Path | None = None) -> tuple[str, list[str]]:
    root = root or repo_root()
    state = normalize_state(state, root)
    step = state.get("next_step")
    if not isinstance(step, str):
        return "agent", []
    batch_mode = required_mode(step)
    if required_mode(step) != batch_mode:
        return batch_mode, []
    return batch_mode, [step]


def batch_exit_check(
    state: dict[str, Any], batch_mode: str, root: Path | None = None
) -> dict[str, Any]:
    root = root or repo_root()
    state = normalize_state(state, root)
    step = state.get("next_step")
    if not isinstance(step, str):
        return {
            "may_exit": True,
            "exit_kind": "unknown",
            "remaining_steps": [],
            "substeps_remaining": [],
            "remaining_count": 0,
            "substeps_remaining_count": 0,
        }

    next_mode = required_mode(step)
    _, remaining = batch_step_ids(state, root)
    substeps_remaining: list[str] = []
    if is_macro_block(step):
        substeps_remaining = block_substeps_remaining(state, step, root)
    elif step == "phase_0_verify":
        substeps_remaining = (
            [] if "phase_0_verify" in set(state.get("completed_steps", [])) else ["phase_0_verify"]
        )

    if step == "done":
        return {
            "may_exit": True,
            "exit_kind": "done",
            "batch_mode": batch_mode,
            "next_step": step,
            "next_required_mode": next_mode,
            "remaining_steps": [],
            "substeps_remaining": [],
            "remaining_count": 0,
            "substeps_remaining_count": 0,
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
            "substeps_remaining": substeps_remaining,
            "substeps_remaining_count": len(substeps_remaining),
        }

    return {
        "may_exit": True,
        "exit_kind": "mode_change",
        "batch_mode": batch_mode,
        "next_step": step,
        "next_required_mode": next_mode,
        "remaining_steps": [],
        "substeps_remaining": [],
        "remaining_count": 0,
        "substeps_remaining_count": 0,
    }


def cmd_batch_steps(_: argparse.Namespace) -> int:
    if not STATE_PATH.is_file():
        print(f"Missing state file: {STATE_PATH}")
        return 1
    root = repo_root()
    state = normalize_state(load_state(), root)
    batch_mode, steps = batch_step_ids(state, root)
    substeps_remaining: list[str] = []
    step = state.get("next_step")
    if isinstance(step, str) and is_macro_block(step):
        substeps_remaining = block_substeps_remaining(state, step, root)
    elif step == "phase_0_verify":
        substeps_remaining = (
            [] if "phase_0_verify" in set(state.get("completed_steps", [])) else ["phase_0_verify"]
        )
    print(
        json.dumps(
            {
                "batch_mode": batch_mode,
                "next_step": step,
                "steps_in_batch": steps,
                "remaining_count": len(steps),
                "substeps_remaining": substeps_remaining,
                "substeps_remaining_count": len(substeps_remaining),
            },
            indent=2,
        )
    )
    return 0


def cmd_batch_exit_check(args: argparse.Namespace) -> int:
    if not STATE_PATH.is_file():
        print(f"Missing state file: {STATE_PATH}")
        return 1
    root = repo_root()
    state = normalize_state(load_state(), root)
    step = state.get("next_step")
    batch_mode = args.batch_mode
    if batch_mode is None:
        batch_mode = required_mode(step) if isinstance(step, str) else "agent"
    print(json.dumps(batch_exit_check(state, batch_mode, root), indent=2))
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

    complete_parser = subparsers.add_parser(
        "complete", help="Mark a substep complete and advance macro block state"
    )
    complete_parser.add_argument("step_id", help="Granular substep ID that finished")

    substep_parser = subparsers.add_parser(
        "substep-complete", help="Alias for complete (granular substep within macro block)"
    )
    substep_parser.add_argument("step_id", help="Granular substep ID that finished")

    subparsers.add_parser(
        "current-substep", help="Return the granular substep to execute for the current macro block"
    )

    pause_parser = subparsers.add_parser(
        "set-pause-auto-resume",
        help="Pause or resume stop-hook auto continuation (AskQuestion / hard failure)",
    )
    pause_parser.add_argument(
        "paused",
        choices=("true", "false"),
        help="Whether the stop hook should skip auto-resume",
    )

    subparsers.add_parser("show", help="Print current state and next step metadata")

    subparsers.add_parser(
        "fast-forward",
        help="Auto-complete skip-if-done steps until substantive work (agent-only)",
    )

    subparsers.add_parser(
        "batch-steps",
        help="List macro block IDs in the current mode batch (one per mode stretch)",
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
        "substep-complete": cmd_substep_complete,
        "current-substep": cmd_current_substep,
        "set-pause-auto-resume": cmd_set_pause_auto_resume,
        "show": cmd_show,
        "fast-forward": cmd_fast_forward,
        "batch-steps": cmd_batch_steps,
        "batch-exit-check": cmd_batch_exit_check,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
