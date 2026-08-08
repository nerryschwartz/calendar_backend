from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import calendar_backend.models.notifications  # noqa: F401  # pyright: ignore[reportUnusedImport]
from calendar_backend.db.session import transaction
from calendar_backend.domain.enums import CloneStatus, NotificationSourceKind, PlanKind
from calendar_backend.domain.ids import PlanID
from calendar_backend.models.plans import GoalPlan, Plan
from calendar_backend.services.notification_queue import NotificationQueueService
from calendar_backend.services.timer import TimerService


def test_timer_service_empty_active(service_db_session, fake_clock) -> None:
    result = TimerService(service_db_session, fake_clock).get_active_timers()
    assert result.success
    assert result.value == ()


def test_notification_enqueue_idempotent(service_db_session, fake_clock) -> None:
    plan_id = uuid4()
    now = fake_clock.now_utc()
    with transaction(service_db_session) as txn:
        txn.add(
            Plan(
                plan_id=plan_id,
                plan_kind=PlanKind.GOAL,
                name="goal",
                parent_id=None,
                is_master=False,
                cloned_from_id=None,
                clone_status=CloneStatus.NOT_CLONED,
                created_at=now,
                updated_at=now,
            )
        )
        txn.add(GoalPlan(plan_id=plan_id))
        txn.flush()

    service = NotificationQueueService(service_db_session, fake_clock)
    end = now + timedelta(minutes=30)
    first = service.enqueue_timer_completion(
        source_kind=NotificationSourceKind.TASK,
        plan_id=PlanID(plan_id),
        timer_key="task:test",
        window_end_at=end,
        display_label="Task",
    )
    second = service.enqueue_timer_completion(
        source_kind=NotificationSourceKind.TASK,
        plan_id=PlanID(plan_id),
        timer_key="task:test",
        window_end_at=end,
        display_label="Task",
    )
    assert first.success and second.success
    pending = service.list_pending()
    assert pending.success
    assert pending.value is not None
    assert len(pending.value) == 1
