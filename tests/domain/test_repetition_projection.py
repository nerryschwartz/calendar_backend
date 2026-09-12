"""Pure generation includes stable edit targets without persistence."""

from datetime import UTC, datetime, timedelta

import pytest
from calendar_backend.domain.repetition_projection import (
    FrozenHorizon,
    PreviewInput,
    project_instances,
)
from pydantic import ValidationError


def preview_input(**settings):
    return PreviewInput.model_validate(
        {
            "repetition_ref": "rep",
            "settings": {
                "repeat_mode": "MANUAL_COUNT",
                "start_time": "2026-09-12T05:00:00Z",
                "repeat_interval_minutes": 1440,
                "manual_count": 14,
                **settings,
            },
            "template": {
                "root_ref": "task",
                "nodes": [
                    {
                        "ref": "task",
                        "name": "Lunch",
                        "kind": "TASK",
                        "duration_minutes": 30,
                        "constraint_groups": [
                            {
                                "ref": "group",
                                "windows": [
                                    {
                                        "ref": "window",
                                        "start_time": "2026-09-12T16:00:00Z",
                                        "end_time": "2026-09-12T20:00:00Z",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        }
    )


def test_fourteen_shifted_tasks_and_stable_keys():
    value = preview_input()
    horizon = FrozenHorizon(run_started_at=datetime(2026, 9, 12, tzinfo=UTC))
    result = project_instances(value, horizon, "key")
    assert len(result.instances) == 14
    assert project_instances(value, horizon, "key") == result
    assert value.template.nodes[0].constraint_groups[0].windows[0].start_time.hour == 16
    refs = set()
    for index, instance in enumerate(result.instances):
        node = instance.nodes[0]
        assert node.ref == instance.root_ref
        assert node.ref not in refs
        refs.add(node.ref)
        assert node.parent_ref == "rep"
        window = node.constraint_groups[0].windows[0]
        assert window.start_time == datetime(2026, 9, 12, 16, tzinfo=UTC) + timedelta(days=index)
        assert window.end_time - window.start_time == timedelta(hours=4)
        assert node.constraint_groups[-1].constraint_kind == "SYSTEM_REPETITION_WINDOW"


def test_date_range_uses_exclusive_frozen_horizon():
    value = preview_input(repeat_mode="DATE_RANGE", manual_count=None)
    horizon = FrozenHorizon(
        run_started_at=datetime(2026, 9, 12, tzinfo=UTC),
        master_horizon_end=datetime(2026, 9, 14, 5, tzinfo=UTC),
    )
    assert len(project_instances(value, horizon).instances) == 2


def test_invalid_template_and_minute_alignment():
    with pytest.raises(ValidationError, match="minute-aligned"):
        preview_input(start_time="2026-09-12T05:00:01Z")
    raw = preview_input().model_dump()
    raw["template"]["nodes"].append(raw["template"]["nodes"][0])
    with pytest.raises(ValidationError, match="unique"):
        PreviewInput.model_validate(raw)
