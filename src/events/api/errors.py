from __future__ import annotations

import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from pymongo.errors import PyMongoError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from events.api.schemas import (
    APIError,
    APIErrorResponse,
    APIValidationIssue,
)

logger = logging.getLogger("events.api")

API_ERROR_RESPONSES = {
    status_code: {
        "model": APIErrorResponse,
        "description": description,
    }
    for status_code, description in (
        (400, "Invalid request."),
        (404, "Resource not found."),
        (409, "Request conflicts with current state."),
        (422, "Request validation failed."),
        (429, "User run limit reached."),
        (500, "Unexpected server error."),
        (503, "Required service is unavailable."),
    )
}


def _error_code(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "validation_error",
        429: "rate_limited",
        503: "service_unavailable",
    }.get(
        status_code,
        "internal_error" if status_code >= 500 else "request_error",
    )


def _response(
    status_code: int,
    code: str,
    message: str,
    *,
    details: list[APIValidationIssue] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    content = APIErrorResponse(
        error=APIError(code=code, message=message, details=details)
    ).model_dump(mode="json", exclude_none=True)
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers=headers,
    )


async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    if exc.status_code >= 500 and exc.status_code != 503:
        message = "An unexpected error occurred."
    elif isinstance(exc.detail, str):
        message = exc.detail
    else:
        message = "Request could not be completed."
    return _response(
        exc.status_code,
        _error_code(exc.status_code),
        message,
        headers=exc.headers,
    )


async def request_validation_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    details = [
        APIValidationIssue(
            location=list(error["loc"]),
            message="Invalid value.",
            type=error["type"],
        )
        for error in exc.errors()
    ]
    return _response(
        422,
        "validation_error",
        "Request validation failed.",
        details=details,
    )


async def persisted_validation_handler(
    request: Request,
    exc: ValidationError,
) -> JSONResponse:
    logger.exception("Persisted data failed validation.")
    return _response(
        500,
        "invalid_persisted_data",
        "Stored data is invalid.",
    )


async def database_error_handler(
    request: Request,
    exc: PyMongoError,
) -> JSONResponse:
    logger.exception("MongoDB request failed.")
    return _response(
        503,
        "service_unavailable",
        "Database service is unavailable.",
    )


async def unexpected_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    logger.exception("Unhandled API error.")
    return _response(
        500,
        "internal_error",
        "An unexpected error occurred.",
    )
