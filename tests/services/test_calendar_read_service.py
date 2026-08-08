from __future__ import annotations

from calendar_backend.services.calendar_read import CalendarReadService


def test_calendar_read_schedule_state(service_db_session, fake_clock) -> None:
    result = CalendarReadService(service_db_session, fake_clock).get_schedule_state()
    assert result.success
    assert result.value is not None
    assert result.value.active_calendar_run_id is None


def test_calendar_read_empty_calendars(service_db_session, fake_clock) -> None:
    read = CalendarReadService(service_db_session, fake_clock)
    tasks = read.get_task_calendar()
    blocks = read.get_block_calendar()
    assert tasks.success and tasks.value is not None
    assert blocks.success and blocks.value is not None
    assert tasks.value.entries == ()
    assert blocks.value.entries == ()
