"""Bound HTTP input before FastAPI parses it, without exposing rejected data."""

import json
import math
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


DEFAULT_MAX_BODY_BYTES = 1024 * 1024
_BODYLESS_METHODS = {"GET", "HEAD", "OPTIONS"}


class _RejectedBody(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail


def _header_values(scope: Scope, name: bytes) -> list[bytes]:
    return [value for key, value in scope.get("headers", []) if key.lower() == name]


def _reject_constant(_value: str) -> Any:
    raise ValueError("Non-finite JSON numbers are not supported.")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite JSON numbers are not supported.")
    return result


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON members are not supported.")
        result[key] = value
    return result


def _validate_json(body: bytes) -> None:
    try:
        decoded = json.loads(
            body.decode("utf-8"),
            parse_constant=_reject_constant,
            parse_float=_finite_float,
            object_pairs_hook=_unique_object,
        )
        pending = [decoded]
        while pending:
            value = pending.pop()
            if isinstance(value, str):
                if "\x00" in value:
                    raise ValueError("NUL is not supported in stored text.")
                value.encode("utf-8")
            elif isinstance(value, dict):
                pending.extend(value.keys())
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
    except (ValueError, RecursionError):
        raise _RejectedBody(400, "Request body must contain valid UTF-8 JSON.") from None


def _response_request_id(request: Request) -> str:
    state = request.scope.setdefault("state", {})
    request_id = state.get("request_id")
    if not isinstance(request_id, str):
        request_id = str(uuid4())
        state["request_id"] = request_id
    return request_id


async def sanitized_validation_exception_handler(
    request: Request, _error: RequestValidationError,
) -> JSONResponse:
    # Even error locations or custom validator messages may contain user input.
    return JSONResponse(
        status_code=422,
        content={"detail": "Request validation failed."},
        headers={"X-Request-ID": _response_request_id(request)},
    )


async def sanitized_server_error_handler(request: Request, _error: Exception) -> JSONResponse:
    """Register for Exception so outer ServerErrorMiddleware also returns the ID."""
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
        headers={"X-Request-ID": _response_request_id(request)},
    )


class RequestBoundaryMiddleware:
    """Buffer a bounded mutation body and replay its original bytes downstream.

    JSON checks apply only to application/json and application/*+json. Other
    media types, including raw audio, retain their bytes and have the same
    default size limit unless an exact path override is explicitly configured.
    GET, HEAD and OPTIONS are passed through without body/header restrictions.
    """

    def __init__(
        self,
        app: ASGIApp,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        path_body_limits: Mapping[str, int] | None = None,
    ):
        if type(max_body_bytes) is not int or max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be a positive integer.")
        limits = dict(path_body_limits or {})
        if any(
            not isinstance(path, str) or not path.startswith("/")
            or type(limit) is not int or limit <= 0
            for path, limit in limits.items()
        ):
            raise ValueError("Path body limits require absolute paths and positive integers.")
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.path_body_limits = limits

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (name, value) for name, value in message.get("headers", [])
                    if name.lower() != b"x-request-id"
                ]
                message = {**message, "headers": [
                    *headers, (b"x-request-id", request_id.encode("ascii")),
                ]}
            await send(message)

        if scope.get("method", "GET").upper() in _BODYLESS_METHODS:
            await self.app(scope, receive, send_with_request_id)
            return

        try:
            maximum = self.path_body_limits.get(scope.get("path", ""), self.max_body_bytes)
            declared = self._declared_size(scope, maximum)
            encoding = _header_values(scope, b"content-encoding")
            if len(encoding) > 1 or (encoding and encoding[0].strip().lower() != b"identity"):
                raise _RejectedBody(415, "Content-Encoding is not supported.")
            content_types = _header_values(scope, b"content-type")
            if len(content_types) > 1:
                raise _RejectedBody(400, "Content-Type must be unambiguous.")
            body = await self._read_body(receive, declared, maximum)
            if body is None:
                return  # The client disconnected before completing the request.
            media_type = content_types[0].split(b";", 1)[0].strip().lower() if content_types else b""
            if (not media_type and body) or media_type == b"application/json" or (
                media_type.startswith(b"application/") and media_type.endswith(b"+json")
            ):
                _validate_json(body)
        except _RejectedBody as error:
            await JSONResponse(status_code=error.status_code, content={"detail": error.detail})(
                scope, receive, send_with_request_id,
            )
            return

        delivered = False

        async def replay_body() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_body, send_with_request_id)

    @staticmethod
    def _declared_size(scope: Scope, maximum: int) -> int | None:
        values = _header_values(scope, b"content-length")
        if not values:
            return None
        if len(values) != 1:
            raise _RejectedBody(400, "Content-Length must be unambiguous.")
        value = values[0].strip()
        if not value or not value.isdigit():
            raise _RejectedBody(400, "Content-Length must be a nonnegative integer.")
        normalized = value.lstrip(b"0") or b"0"
        # Avoid converting an unbounded integer supplied in a malicious header.
        if len(normalized) > len(str(maximum)):
            raise _RejectedBody(413, "Request body exceeds the configured size limit.")
        declared = int(normalized)
        if declared > maximum:
            raise _RejectedBody(413, "Request body exceeds the configured size limit.")
        return declared

    @staticmethod
    async def _read_body(receive: Receive, declared: int | None, maximum: int) -> bytes | None:
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return None
            if message["type"] != "http.request":
                raise _RejectedBody(400, "Request body could not be read.")
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > maximum:
                raise _RejectedBody(413, "Request body exceeds the configured size limit.")
            if declared is not None and size > declared:
                raise _RejectedBody(400, "Request body does not match Content-Length.")
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        if declared is not None and size != declared:
            raise _RejectedBody(400, "Request body does not match Content-Length.")
        return b"".join(chunks)
