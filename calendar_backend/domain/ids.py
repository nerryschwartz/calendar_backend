from __future__ import annotations

from collections.abc import Callable
from typing import NewType
from uuid import UUID, uuid4

PlanID = NewType("PlanID", UUID)
GoalChildChainID = NewType("GoalChildChainID", UUID)
GoalChildChainItemID = NewType("GoalChildChainItemID", UUID)
TimeConstraintGroupID = NewType("TimeConstraintGroupID", UUID)
TimeWindowID = NewType("TimeWindowID", UUID)
RepetitionInstanceID = NewType("RepetitionInstanceID", UUID)
CalendarEntryID = NewType("CalendarEntryID", UUID)
FreeTimeActivityID = NewType("FreeTimeActivityID", UUID)
FreeTimeActivityPrerequisiteID = NewType("FreeTimeActivityPrerequisiteID", UUID)
CalendarRunID = NewType("CalendarRunID", UUID)


def new_id[IdT](id_type: Callable[[UUID], IdT]) -> IdT:
    return id_type(uuid4())
