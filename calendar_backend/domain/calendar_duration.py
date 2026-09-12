"""Validated calendar-relative duration used by the master horizon."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta
from dateutil.tz import resolve_imaginary


@dataclass(frozen=True)
class CalendarDuration:
    years: int = 0
    months: int = 0
    days: int = 0
    hours: int = 0
    minutes: int = 0

    def __post_init__(self) -> None:
        values = asdict(self).values()
        if any(type(value) is not int or value < 0 for value in values) or not any(values):
            raise ValueError("Calendar duration needs nonnegative integers and a positive part")

    def end_at(self, start: datetime, timezone: str) -> datetime:
        local_end = start.astimezone(ZoneInfo(timezone)) + relativedelta(**asdict(self))
        end = resolve_imaginary(local_end.replace(fold=0)).astimezone(UTC)
        if end <= start:
            end = resolve_imaginary(local_end.replace(fold=1)).astimezone(UTC)
        if end <= start:
            raise ValueError("Calendar duration must end after its start")
        return end
