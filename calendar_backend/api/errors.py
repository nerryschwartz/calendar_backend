"""Map service-layer failures to HTTP exceptions."""

from __future__ import annotations

from typing import Any

from calendar_backend.domain.results import ServiceResult
from fastapi import HTTPException


def unwrap_result[T](result: ServiceResult[T]) -> T:
    if result.success and result.value is not None:
        return result.value
    raise service_result_http_error(result)


def service_result_http_error(result: ServiceResult[Any]) -> HTTPException:
    errors = [
        {
            "code": message.code.value,
            "message": message.message,
            "details": message.details,
        }
        for message in result.errors
    ]
    body: dict[str, Any] = {"errors": errors}
    if result.value is not None:
        body["value"] = result.value
    return HTTPException(status_code=422, detail=body)
