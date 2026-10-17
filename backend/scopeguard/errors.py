"""Safe, structured API errors: {"error": message, "code"?: snake_code, "details"?: {...}}."""

from __future__ import annotations

from typing import Any, Optional


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: Optional[str] = None, details: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.details = details

    def body(self) -> dict:
        out: dict[str, Any] = {"error": self.message}
        if self.code:
            out["code"] = self.code
        if self.details is not None:
            out["details"] = self.details
        return out


def bad_request(message: str, code: str = "bad_request", details: Optional[dict] = None) -> ApiError:
    return ApiError(400, message, code, details)


def not_found(message: str, code: str = "not_found") -> ApiError:
    return ApiError(404, message, code)


def conflict(message: str, code: str, details: Optional[dict] = None) -> ApiError:
    return ApiError(409, message, code, details)


def unprocessable(message: str, code: str, details: Optional[dict] = None) -> ApiError:
    return ApiError(422, message, code, details)
