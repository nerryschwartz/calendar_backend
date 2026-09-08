"""Map service-layer failures to HTTP exceptions."""

from __future__ import annotations

from typing import Any

from calendar_backend.api.serialize import dto_to_json
from calendar_backend.domain.results import ServiceResult
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder


def unwrap_result[T](result: ServiceResult[T]) -> T:
    if result.success:
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
        body["value"] = jsonable_encoder(dto_to_json(result.value))
    return HTTPException(status_code=422, detail=body)
