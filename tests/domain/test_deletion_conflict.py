"""Pure tests for conflict deletion generation and ranking helpers."""

from __future__ import annotations

import uuid

from calendar_backend.domain.deletion import (
    AssignmentConflict,
    DeletionCandidate,
    DeletionOperation,
    DeletionPreview,
    build_deletion_candidate,
    compute_ranking_keys,
    generate_deletion_operations,
    rank_deletion_candidates,
)
from calendar_backend.domain.ids import PlanID


def _fabricated_candidate(
    root_plan_id: PlanID,
    *,
    ranking_keys: tuple[int, ...],
) -> DeletionCandidate:
    operation = DeletionOperation(root_plan_id=root_plan_id)
    preview = DeletionPreview(
        root_plan_id=root_plan_id,
        legal_operation=operation,
        affected_plan_ids=(root_plan_id,),
        affected_task_ids=(),
        affected_calendar_entry_ids=(),
        affected_depth_counts_from_master=(),
    )
    return DeletionCandidate(
        legal_operation=operation,
        deletion_preview=preview,
        ranking_keys=ranking_keys,
        explanation="test",
    )


def _fabricated_preview(
    root_plan_id: PlanID,
    *,
    affected_plan_ids: tuple[PlanID, ...],
    depth_counts: tuple[int, ...],
) -> DeletionPreview:
    operation = DeletionOperation(root_plan_id=root_plan_id)
    return DeletionPreview(
        root_plan_id=root_plan_id,
        legal_operation=operation,
        affected_plan_ids=affected_plan_ids,
        affected_task_ids=(),
        affected_calendar_entry_ids=(),
        affected_depth_counts_from_master=depth_counts,
    )


def test_generate_deletion_operations_dedupes_and_sorts() -> None:
    first_id = PlanID(uuid.UUID("00000000-0000-4000-8000-000000000002"))
    second_id = PlanID(uuid.UUID("00000000-0000-4000-8000-000000000001"))
    conflict = AssignmentConflict(
        conflicting_plan_ids=(second_id, first_id, second_id),
    )

    operations = generate_deletion_operations(conflict)

    assert operations == (
        DeletionOperation(root_plan_id=second_id),
        DeletionOperation(root_plan_id=first_id),
    )


def test_generate_deletion_operations_skips_master() -> None:
    master_id = PlanID(uuid.uuid4())
    task_id = PlanID(uuid.uuid4())
    conflict = AssignmentConflict(conflicting_plan_ids=(master_id, task_id))

    operations = generate_deletion_operations(conflict, master_plan_id=master_id)

    assert operations == (DeletionOperation(root_plan_id=task_id),)


def test_compute_ranking_keys_sums_priorities_for_affected_only() -> None:
    root_id = PlanID(uuid.uuid4())
    other_id = PlanID(uuid.uuid4())
    preview = _fabricated_preview(
        root_id,
        affected_plan_ids=(root_id, other_id),
        depth_counts=(0, 1),
    )
    conflict = AssignmentConflict(
        conflicting_plan_ids=(root_id,),
        affected_priority_by_plan_id=((root_id, 4), (other_id, 9)),
    )

    keys = compute_ranking_keys(preview, conflict)

    assert keys == (0, 1, 13, 2)


def test_rank_deletion_candidates_prefers_shallow_depth_impact() -> None:
    shallow_impact = _fabricated_candidate(
        PlanID(uuid.uuid4()),
        ranking_keys=(0, 0, 1, 0, 1),
    )
    deep_impact = _fabricated_candidate(
        PlanID(uuid.uuid4()),
        ranking_keys=(0, 1, 1, 0, 2),
    )

    ranked = rank_deletion_candidates((deep_impact, shallow_impact))

    assert ranked[0] is shallow_impact
    assert ranked[1] is deep_impact


def test_rank_deletion_candidates_priority_tie_break() -> None:
    lower_priority_sum = _fabricated_candidate(
        PlanID(uuid.uuid4()),
        ranking_keys=(0, 0, 1, 2, 1),
    )
    higher_priority_sum = _fabricated_candidate(
        PlanID(uuid.uuid4()),
        ranking_keys=(0, 0, 1, 9, 1),
    )

    ranked = rank_deletion_candidates((higher_priority_sum, lower_priority_sum))

    assert ranked[0] is lower_priority_sum
    assert ranked[1] is higher_priority_sum


def test_rank_deletion_candidates_root_plan_id_tie_break() -> None:
    later_id = PlanID(uuid.UUID("00000000-0000-4000-8000-000000000002"))
    earlier_id = PlanID(uuid.UUID("00000000-0000-4000-8000-000000000001"))
    shared_keys = (0, 0, 1, 0, 1)
    later = _fabricated_candidate(later_id, ranking_keys=shared_keys)
    earlier = _fabricated_candidate(earlier_id, ranking_keys=shared_keys)

    ranked = rank_deletion_candidates((later, earlier))

    assert ranked[0].legal_operation.root_plan_id == earlier_id
    assert ranked[1].legal_operation.root_plan_id == later_id


def test_build_deletion_candidate_sets_keys_and_explanation() -> None:
    root_id = PlanID(uuid.uuid4())
    operation = DeletionOperation(root_plan_id=root_id)
    preview = _fabricated_preview(
        root_id,
        affected_plan_ids=(root_id,),
        depth_counts=(0, 0, 1),
    )
    conflict = AssignmentConflict(
        conflicting_plan_ids=(root_id,),
        affected_priority_by_plan_id=((root_id, 7),),
    )

    candidate = build_deletion_candidate(operation, preview, conflict)

    assert candidate.ranking_keys == (0, 0, 1, 7, 1)
    assert str(root_id) in candidate.explanation
    assert "depth counts (0, 0, 1)" in candidate.explanation
