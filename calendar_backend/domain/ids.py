from __future__ import annotations

from collections.abc import Callable
from typing import NewType
from uuid import UUID, uuid4

PlanID = NewType("PlanID", UUID)
TimeConstraintGroupID = NewType("TimeConstraintGroupID", UUID)
TimeWindowID = NewType("TimeWindowID", UUID)
RepetitionInstanceID = NewType("RepetitionInstanceID", UUID)
CalendarEntryID = NewType("CalendarEntryID", UUID)
BlockCalendarEntryID = NewType("BlockCalendarEntryID", UUID)
FreeTimeActivityID = NewType("FreeTimeActivityID", UUID)
FreeTimeActivityPrerequisiteID = NewType("FreeTimeActivityPrerequisiteID", UUID)
CalendarRunID = NewType("CalendarRunID", UUID)
NotificationQueueItemID = NewType("NotificationQueueItemID", UUID)


def new_id[IdT](id_type: Callable[[UUID], IdT]) -> IdT:
    return id_type(uuid4())
