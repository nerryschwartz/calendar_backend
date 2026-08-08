"""JSON serialization helpers for domain DTOs."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, cast
from uuid import UUID


def dto_to_json(value: object) -> Any:  # noqa: PLR0911
    if value is None:
        return None
    if is_dataclass(value):
        return {key: dto_to_json(item) for key, item in asdict(cast(Any, value)).items()}
    if isinstance(value, tuple | list):
        return [dto_to_json(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value
