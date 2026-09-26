"""Response declarations shared by every router so the OpenAPI document is honest."""

from typing import Any

from fastapi import status

from shelfsense_api.schemas import ErrorResponse

AUTH_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse, "description": "Not authenticated"},
    status.HTTP_403_FORBIDDEN: {"model": ErrorResponse, "description": "Not allowed"},
}

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Not found"},
}

READ_RESPONSES: dict[int | str, dict[str, Any]] = {**AUTH_RESPONSES}
READ_ONE_RESPONSES: dict[int | str, dict[str, Any]] = {**AUTH_RESPONSES, **NOT_FOUND}
WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    **AUTH_RESPONSES,
    status.HTTP_409_CONFLICT: {"model": ErrorResponse, "description": "Already exists"},
}
