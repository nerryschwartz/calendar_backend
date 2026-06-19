"""USER time constraint group CRUD."""

from __future__ import annotations

from datetime import UTC

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from calendar_backend.db.session import transaction
from calendar_backend.domain.constraints import merge_or_windows, validate_user_group_windows
from calendar_backend.domain.dtos import (
    TimeConstraintGroupDTO,
    time_constraint_group_dto_from_rows,
)
from calendar_backend.domain.enums import ConstraintKind
from calendar_backend.domain.errors import MessageCode, ServiceMessage
from calendar_backend.domain.ids import PlanID, TimeConstraintGroupID, TimeWindowID, new_id
from calendar_backend.domain.results import ServiceResult, fail, ok
from calendar_backend.domain.time import Clock, SystemClock, TimeWindow
from calendar_backend.models.constraints import TimeConstraintGroup
from calendar_backend.models.constraints import TimeWindow as TimeWindowRow
from calendar_backend.models.plans import Plan


class TimeConstraintService:
    def __init__(self, session: Session, clock: Clock | None = None) -> None:
        self._session = session
        self._clock = clock or SystemClock()

    def add_user_group(
        self,
        plan_id: PlanID,
        windows: tuple[TimeWindow, ...],
    ) -> ServiceResult[TimeConstraintGroupDTO]:
        validation_error = validate_user_group_windows(windows)
        if validation_error is not None:
            return fail(validation_error)

        merged_windows = merge_or_windows(windows)

        with transaction(self._session) as txn:
            if txn.get(Plan, plan_id) is None:
                return fail(
                    ServiceMessage(
                        code=MessageCode.PLAN_NOT_FOUND,
                        message="Plan not found",
                        details={"plan_id": str(plan_id)},
                    )
                )

            group_id = new_id(TimeConstraintGroupID)
            group = TimeConstraintGroup(
                time_constraint_group_id=group_id,
                plan_id=plan_id,
                constraint_kind=ConstraintKind.USER,
            )
            txn.add(group)
            window_rows = _insert_windows(txn, group_id=group_id, windows=merged_windows)
            txn.flush()
            return ok(time_constraint_group_dto_from_rows(group, window_rows))

    def update_user_group(
        self,
        group_id: TimeConstraintGroupID,
        windows: tuple[TimeWindow, ...],
    ) -> ServiceResult[TimeConstraintGroupDTO]:
        validation_error = validate_user_group_windows(windows)
        if validation_error is not None:
            return fail(validation_error)

        merged_windows = merge_or_windows(windows)

        with transaction(self._session) as txn:
            loaded = _load_user_group(txn, group_id)
            if isinstance(loaded, ServiceMessage):
                return fail(loaded)
            group = loaded

            window_rows = _replace_group_windows(txn, group, merged_windows)
            txn.flush()
            return ok(time_constraint_group_dto_from_rows(group, window_rows))

    def remove_user_group(self, group_id: TimeConstraintGroupID) -> ServiceResult[None]:
        with transaction(self._session) as txn:
            loaded = _load_user_group(txn, group_id)
            if isinstance(loaded, ServiceMessage):
                return fail(loaded)
            group = loaded

            txn.execute(
                delete(TimeWindowRow).where(
                    TimeWindowRow.group_id == group.time_constraint_group_id
                )
            )
            txn.delete(group)
            txn.flush()
            return ok(None)

    def add_user_window(
        self,
        group_id: TimeConstraintGroupID,
        window: TimeWindow,
    ) -> ServiceResult[TimeConstraintGroupDTO]:
        validation_error = validate_user_group_windows((window,))
        if validation_error is not None:
            return fail(validation_error)

        with transaction(self._session) as txn:
            loaded = _load_user_group(txn, group_id)
            if isinstance(loaded, ServiceMessage):
                return fail(loaded)
            group = loaded

            existing_windows = _load_group_windows(
                txn, TimeConstraintGroupID(group.time_constraint_group_id)
            )
            merged_windows = merge_or_windows((*existing_windows, window))
            window_rows = _replace_group_windows(txn, group, merged_windows)
            txn.flush()
            return ok(time_constraint_group_dto_from_rows(group, window_rows))

    def remove_user_window(
        self,
        group_id: TimeConstraintGroupID,
        time_window_id: TimeWindowID,
    ) -> ServiceResult[TimeConstraintGroupDTO | None]:
        with transaction(self._session) as txn:
            loaded = _load_user_group(txn, group_id)
            if isinstance(loaded, ServiceMessage):
                return fail(loaded)
            group = loaded

            window_row = txn.get(TimeWindowRow, time_window_id)
            if window_row is None or window_row.group_id != group.time_constraint_group_id:
                return fail(
                    ServiceMessage(
                        code=MessageCode.TIME_WINDOW_NOT_FOUND,
                        message="Time window not found in constraint group",
                        details={
                            "constraint_group_id": str(group_id),
                            "time_window_id": str(time_window_id),
                        },
                    )
                )

            txn.delete(window_row)
            txn.flush()

            remaining_rows = _fetch_group_window_rows(
                txn, TimeConstraintGroupID(group.time_constraint_group_id)
            )
            if not remaining_rows:
                txn.delete(group)
                txn.flush()
                return ok(None)

            return ok(time_constraint_group_dto_from_rows(group, remaining_rows))


def _load_user_group(
    txn: Session,
    group_id: TimeConstraintGroupID,
) -> TimeConstraintGroup | ServiceMessage:
    group = txn.get(TimeConstraintGroup, group_id)
    if group is None:
        return ServiceMessage(
            code=MessageCode.CONSTRAINT_GROUP_NOT_FOUND,
            message="Time constraint group not found",
            details={"constraint_group_id": str(group_id)},
        )

    if group.constraint_kind != ConstraintKind.USER:
        return ServiceMessage(
            code=MessageCode.SYSTEM_CONSTRAINT_DIRECT_EDIT_FORBIDDEN,
            message="System-owned constraint groups cannot be edited via TimeConstraintService",
            details={
                "constraint_group_id": str(group.time_constraint_group_id),
                "constraint_kind": group.constraint_kind.value,
            },
        )

    return group


def _load_group_windows(
    txn: Session,
    group_id: TimeConstraintGroupID,
) -> tuple[TimeWindow, ...]:
    rows = _fetch_group_window_rows(txn, group_id)
    windows: list[TimeWindow] = []
    for row in rows:
        start_time = row.start_time
        end_time = row.end_time
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=UTC)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=UTC)
        windows.append(TimeWindow(start_time=start_time, end_time=end_time))
    return tuple(windows)


def _fetch_group_window_rows(
    txn: Session,
    group_id: TimeConstraintGroupID,
) -> tuple[TimeWindowRow, ...]:
    return tuple(
        txn.scalars(
            select(TimeWindowRow)
            .where(TimeWindowRow.group_id == group_id)
            .order_by(TimeWindowRow.start_time)
        ).all()
    )


def _replace_group_windows(
    txn: Session,
    group: TimeConstraintGroup,
    windows: tuple[TimeWindow, ...],
) -> tuple[TimeWindowRow, ...]:
    txn.execute(
        delete(TimeWindowRow).where(TimeWindowRow.group_id == group.time_constraint_group_id)
    )
    return _insert_windows(
        txn,
        group_id=TimeConstraintGroupID(group.time_constraint_group_id),
        windows=windows,
    )


def _insert_windows(
    session: Session,
    *,
    group_id: TimeConstraintGroupID,
    windows: tuple[TimeWindow, ...],
) -> tuple[TimeWindowRow, ...]:
    rows: list[TimeWindowRow] = []
    for window in windows:
        row = TimeWindowRow(
            time_window_id=new_id(TimeWindowID),
            group_id=group_id,
            start_time=window.start_time,
            end_time=window.end_time,
        )
        session.add(row)
        rows.append(row)
    return tuple(rows)
