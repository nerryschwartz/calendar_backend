from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "v2_loop_state",
    ROOT / "scripts/cursor/v2_loop_state.py",
)
assert _SPEC and _SPEC.loader
loop = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(loop)


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_normalize_legacy_granular_next_step(repo_root: Path) -> None:
    state = {
        "version": 1,
        "branch": "main",
        "next_required_mode": "agent",
        "next_step": "phase_2_slice_3_build",
        "current_phase": 2,
        "current_plan_path": "docs/plans/v2_prerequisites.md",
        "current_slice_id": "3",
        "skip_tests_on_commit": True,
        "completed_steps": ["phase_2_slice_2_build"],
        "alembic_group": None,
        "plan_mode_escape_hatches": [],
        "pause_auto_resume": False,
    }
    normalized = loop.normalize_state(state, repo_root)
    assert normalized["next_step"] == "phase_2_agent_block"
    assert normalized["next_required_mode"] == "agent"


def test_current_substep_within_agent_block(repo_root: Path) -> None:
    state = loop.normalize_state(
        {
            "version": 1,
            "branch": "main",
            "next_required_mode": "agent",
            "next_step": "phase_2_agent_block",
            "current_phase": 2,
            "current_plan_path": "docs/plans/v2_prerequisites.md",
            "current_slice_id": "2",
            "skip_tests_on_commit": True,
            "completed_steps": [
                "phase_2_draft_plan",
                "phase_2_finalize_plan",
                "phase_2_slice_1_pre_alembic",
                "phase_2_slice_1_alembic_preview",
                "phase_2_slice_1_migration_manual_edit",
                "phase_2_slice_1_alembic_continue",
                "phase_2_slice_1_post_alembic",
                "phase_2_slice_2_build",
            ],
            "alembic_group": None,
            "plan_mode_escape_hatches": [],
            "pause_auto_resume": False,
        },
        repo_root,
    )
    assert loop.current_substep(state, repo_root) == "phase_2_slice_3_build"


def test_batch_steps_returns_single_macro_block(repo_root: Path) -> None:
    state = loop.normalize_state(
        {
            "version": 1,
            "branch": "main",
            "next_required_mode": "agent",
            "next_step": "phase_2_agent_block",
            "current_phase": 2,
            "current_plan_path": "docs/plans/v2_prerequisites.md",
            "current_slice_id": "2",
            "skip_tests_on_commit": True,
            "completed_steps": [
                "phase_2_draft_plan",
                "phase_2_finalize_plan",
                "phase_2_slice_1_pre_alembic",
                "phase_2_slice_1_alembic_preview",
                "phase_2_slice_1_migration_manual_edit",
                "phase_2_slice_1_alembic_continue",
                "phase_2_slice_1_post_alembic",
                "phase_2_slice_2_build",
            ],
            "alembic_group": None,
            "plan_mode_escape_hatches": [],
            "pause_auto_resume": False,
        },
        repo_root,
    )
    batch_mode, steps = loop.batch_step_ids(state, repo_root)
    assert batch_mode == "agent"
    assert steps == ["phase_2_agent_block"]


def test_batch_exit_check_false_mid_agent_block(repo_root: Path) -> None:
    state = loop.normalize_state(
        {
            "version": 1,
            "branch": "main",
            "next_required_mode": "agent",
            "next_step": "phase_2_agent_block",
            "current_phase": 2,
            "current_plan_path": "docs/plans/v2_prerequisites.md",
            "current_slice_id": "2",
            "skip_tests_on_commit": True,
            "completed_steps": [
                "phase_2_draft_plan",
                "phase_2_finalize_plan",
                "phase_2_slice_1_pre_alembic",
                "phase_2_slice_1_alembic_preview",
                "phase_2_slice_1_migration_manual_edit",
                "phase_2_slice_1_alembic_continue",
                "phase_2_slice_1_post_alembic",
                "phase_2_slice_2_build",
            ],
            "alembic_group": None,
            "plan_mode_escape_hatches": [],
            "pause_auto_resume": False,
        },
        repo_root,
    )
    result = loop.batch_exit_check(state, "agent", repo_root)
    assert result["may_exit"] is False
    assert result["exit_kind"] == "batch_incomplete"
    assert result["remaining_count"] == 1
    assert result["substeps_remaining_count"] > 0


def test_substep_complete_keeps_macro_block(repo_root: Path) -> None:
    state = loop.normalize_state(
        {
            "version": 1,
            "branch": "main",
            "next_required_mode": "agent",
            "next_step": "phase_2_agent_block",
            "current_phase": 2,
            "current_plan_path": "docs/plans/v2_prerequisites.md",
            "current_slice_id": "3",
            "skip_tests_on_commit": True,
            "completed_steps": [
                "phase_2_draft_plan",
                "phase_2_finalize_plan",
                "phase_2_slice_1_pre_alembic",
                "phase_2_slice_1_alembic_preview",
                "phase_2_slice_1_migration_manual_edit",
                "phase_2_slice_1_alembic_continue",
                "phase_2_slice_1_post_alembic",
                "phase_2_slice_2_build",
            ],
            "alembic_group": None,
            "plan_mode_escape_hatches": [],
            "pause_auto_resume": False,
        },
        repo_root,
    )
    updated, errors = loop.substep_complete(state, "phase_2_slice_3_build", repo_root)
    assert errors == []
    assert updated["next_step"] == "phase_2_agent_block"
    assert "phase_2_slice_3_build" in updated["completed_steps"]
    assert loop.current_substep(updated, repo_root) == "phase_2_slice_4_build"


def test_plan_block_substeps_and_mode(repo_root: Path) -> None:
    state = {
        "version": 1,
        "branch": "main",
        "next_required_mode": "plan",
        "next_step": "phase_3_plan_block",
        "current_phase": 3,
        "current_plan_path": "docs/plans/v2_block_orm.md",
        "current_slice_id": None,
        "skip_tests_on_commit": True,
        "completed_steps": ["phase_3_plan_bootstrap"],
        "alembic_group": None,
        "plan_mode_escape_hatches": [],
        "pause_auto_resume": False,
    }
    assert loop.current_substep(state, repo_root) == "phase_3_request_questions"
    batch_mode, steps = loop.batch_step_ids(state, repo_root)
    assert batch_mode == "plan"
    assert steps == ["phase_3_plan_block"]


def test_substep_complete_finishes_plan_block(repo_root: Path) -> None:
    state = {
        "version": 1,
        "branch": "main",
        "next_required_mode": "plan",
        "next_step": "phase_3_plan_block",
        "current_phase": 3,
        "current_plan_path": "docs/plans/v2_block_orm.md",
        "current_slice_id": None,
        "skip_tests_on_commit": True,
        "completed_steps": ["phase_3_plan_bootstrap"],
        "alembic_group": None,
        "plan_mode_escape_hatches": [],
        "pause_auto_resume": False,
    }
    updated, errors = loop.substep_complete(state, "phase_3_request_questions", repo_root)
    assert errors == []
    assert updated["next_step"] == "phase_3_agent_block"
    assert updated["next_required_mode"] == "agent"


def test_batch_exit_check_plan_handoff_after_plan_block(repo_root: Path) -> None:
    state = {
        "version": 1,
        "branch": "main",
        "next_required_mode": "agent",
        "next_step": "phase_3_agent_block",
        "current_phase": 3,
        "current_plan_path": "docs/plans/v2_block_orm.md",
        "current_slice_id": None,
        "skip_tests_on_commit": True,
        "completed_steps": [
            "phase_3_plan_bootstrap",
            "phase_3_request_questions",
        ],
        "alembic_group": None,
        "plan_mode_escape_hatches": [],
        "pause_auto_resume": False,
        "active_batch_mode": "plan",
    }
    plan_result = loop.batch_exit_check(state, "plan", repo_root)
    assert plan_result["may_exit"] is True
    assert plan_result["exit_kind"] == "mode_change"

    agent_result = loop.batch_exit_check(state, "agent", repo_root)
    assert agent_result["may_exit"] is False
    assert agent_result["exit_kind"] == "batch_incomplete"


def test_set_and_clear_active_batch_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_path = tmp_path / ".cursor" / "v2_implementation_loop.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "branch": "main",
                "next_required_mode": "agent",
                "next_step": "phase_3_agent_block",
                "current_phase": 3,
                "current_plan_path": "docs/plans/v2_block_orm.md",
                "current_slice_id": None,
                "skip_tests_on_commit": True,
                "completed_steps": [],
                "alembic_group": None,
                "plan_mode_escape_hatches": [],
                "pause_auto_resume": False,
                "active_batch_mode": None,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(loop, "STATE_PATH", state_path)
    monkeypatch.setattr(loop, "repo_root", lambda: tmp_path)

    assert loop.cmd_set_active_batch_mode(argparse.Namespace(mode="agent")) == 0
    loaded = json.loads(state_path.read_text(encoding="utf-8"))
    assert loaded["active_batch_mode"] == "agent"

    assert loop.cmd_clear_active_batch_mode(argparse.Namespace()) == 0
    loaded = json.loads(state_path.read_text(encoding="utf-8"))
    assert loaded["active_batch_mode"] is None
