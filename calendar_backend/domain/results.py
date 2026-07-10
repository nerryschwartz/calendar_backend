from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from calendar_backend.domain.errors import ServiceMessage


@dataclass(frozen=True)
class ServiceResult[T]:
    success: bool
    value: T | None = None
    errors: tuple[ServiceMessage, ...] = ()
    warnings: tuple[ServiceMessage, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]


def ok[T](
    value: T,
    *,
    warnings: tuple[ServiceMessage, ...] = (),
    metadata: Mapping[str, Any] | None = None,
) -> ServiceResult[T]:
    return ServiceResult(
        success=True,
        value=value,
        warnings=warnings,
        metadata={} if metadata is None else dict(metadata),
    )


def fail[T](
    *errors: ServiceMessage,
    metadata: Mapping[str, Any] | None = None,
    _value: T | None = None,
) -> ServiceResult[T]:
    return ServiceResult(
        success=False,
        value=_value,
        errors=errors,
        metadata={} if metadata is None else dict(metadata),
    )
