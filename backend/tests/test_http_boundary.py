"""HTTP parsing and safe errors, without a database, ML process or network."""

import asyncio
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
import pytest

from app.http_boundary import (
    RequestBoundaryMiddleware,
    sanitized_server_error_handler,
    sanitized_validation_exception_handler,
)


class CreateBody(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    conversation_id: str
    checklist_id: str
    checklist_version: int


def application(max_body_bytes=1024, path_body_limits=None):
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, sanitized_validation_exception_handler)
    app.add_exception_handler(Exception, sanitized_server_error_handler)
    app.add_middleware(RequestBoundaryMiddleware, max_body_bytes=max_body_bytes,
                       path_body_limits=path_body_limits)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/v1/evaluations", status_code=201)
    def create(body: CreateBody, request: Request):
        assert UUID(request.state.request_id).version == 4
        return body.model_dump()

    @app.post("/api/v1/audio-assets")
    async def raw_audio(request: Request):
        return {"bytes": len(await request.body())}

    @app.get("/broken")
    def broken():
        raise RuntimeError("synthetic-private-token and dialogue must not be echoed")

    return app


def valid_body():
    return {"conversation_id": "synthetic-conversation", "checklist_id": "synthetic-checklist",
            "checklist_version": 1}


def assert_request_id(response):
    request_id = response.headers["x-request-id"]
    assert UUID(request_id).version == 4
    assert str(UUID(request_id)) == request_id
    return request_id


def test_success_payload_is_preserved_and_incoming_request_id_is_not_trusted():
    with TestClient(application()) as client:
        first = client.post("/api/v1/evaluations", json=valid_body(),
                            headers={"X-Request-ID": "untrusted-private-marker"})
        second = client.post("/api/v1/evaluations", json=valid_body())
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json() == valid_body()
    assert assert_request_id(first) != assert_request_id(second)
    assert "untrusted" not in first.headers["x-request-id"]


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_nonfinite_json_numbers_return_safe_400(number):
    body = '{"conversation_id":"synthetic-private-marker","checklist_id":"x","checklist_version":' + number + '}'
    with TestClient(application()) as client:
        response = client.post("/api/v1/evaluations", content=body,
                               headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert "synthetic-private-marker" not in response.text
    assert_request_id(response)


@pytest.mark.parametrize("body", [
    b'{"checklist_version":1,"checklist_version":2}',
    b'{"nested":{"private-marker":1,"private-marker":2}}',
    b'{"x":"\xff"}', b'{"x":', b'',
])
def test_invalid_utf8_json_and_duplicate_keys_are_rejected(body):
    with TestClient(application(max_body_bytes=4096)) as client:
        response = client.post("/api/v1/evaluations", content=body,
                               headers={"Content-Type": "application/problem+json; charset=utf-8"})
    assert response.status_code == 400
    assert "private-marker" not in response.text
    assert_request_id(response)


def test_deep_json_is_rejected_without_an_unhandled_serialization_error():
    # JSON decoder recursion limits can vary with interpreter and caller depth.
    body = b'[' * 1100 + b'0' + b']' * 1100
    with TestClient(application(max_body_bytes=4096)) as client:
        response = client.post("/api/v1/evaluations", content=body,
                               headers={"Content-Type": "application/json"})
    assert response.status_code in {400, 422}
    assert_request_id(response)


@pytest.mark.parametrize("changes", [
    {"checklist_version": "synthetic-private-token"},
    {"checklist_version": True},
    {"conversation_id": {"dialogue": "synthetic-private-token"}},
    {"synthetic-private-field": "synthetic-private-token"},
])
def test_validation_errors_do_not_echo_input_context_or_unknown_field_names(changes):
    with TestClient(application()) as client:
        response = client.post("/api/v1/evaluations", json={**valid_body(), **changes})
    assert response.status_code == 422
    assert response.json() == {"detail": "Request validation failed."}
    assert "synthetic-private" not in response.text
    assert_request_id(response)


def test_unhandled_error_returns_generic_500_with_request_id():
    with TestClient(application(), raise_server_exceptions=False) as client:
        response = client.get("/broken")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error."}
    assert "private" not in response.text
    assert "Traceback" not in response.text
    assert_request_id(response)


@pytest.mark.parametrize("length", ["-1", "private-token", "1.5", "", "1, 1"])
def test_invalid_content_length_is_safe_400(length):
    with TestClient(application()) as client:
        response = client.post("/api/v1/evaluations", content=b'{}',
                               headers={"Content-Type": "application/json", "Content-Length": length})
    assert response.status_code == 400
    assert "private-token" not in response.text
    assert_request_id(response)


@pytest.mark.parametrize("encoding", ["gzip", "br", "private-token", "identity, gzip"])
def test_encoded_mutation_bodies_are_not_implicitly_decompressed(encoding):
    with TestClient(application()) as client:
        response = client.post("/api/v1/evaluations", json=valid_body(),
                               headers={"Content-Encoding": encoding})
    assert response.status_code == 415
    assert "private-token" not in response.text
    assert_request_id(response)


def test_get_is_not_subject_to_mutation_body_rules():
    with TestClient(application(max_body_bytes=1)) as client:
        response = client.get("/health", headers={"Content-Length": "-1", "Content-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert_request_id(response)


def test_raw_audio_is_preserved_and_an_exact_path_can_have_a_larger_limit():
    with TestClient(application(max_body_bytes=4, path_body_limits={"/api/v1/audio-assets": 16})) as client:
        response = client.post("/api/v1/audio-assets", content=b'\xff\x00audio-bytes',
                               headers={"Content-Type": "audio/wav", "Content-Encoding": "identity"})
        oversized = client.post("/api/v1/audio-assets", content=b'a' * 17,
                                headers={"Content-Type": "audio/wav"})
    assert response.status_code == 200
    assert response.json() == {"bytes": 13}
    assert oversized.status_code == 413
    assert_request_id(response)
    assert_request_id(oversized)


def raw_asgi_request(chunks, headers=(), *, maximum=8):
    """Exercise real ASGI chunk messages without any HTTP client's buffering."""
    messages = []
    received = 0
    called = False
    scope = {"type": "http", "method": "POST", "path": "/upload", "headers": list(headers), "state": {}}

    async def endpoint(_scope, receive, send):
        nonlocal called
        called = True
        message = await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": message["body"]})

    async def receive():
        nonlocal received
        chunk = chunks[received]
        received += 1
        return {"type": "http.request", "body": chunk, "more_body": received < len(chunks)}

    async def send(message):
        messages.append(message)

    asyncio.run(RequestBoundaryMiddleware(endpoint, max_body_bytes=maximum)(scope, receive, send))
    return messages, received, called


def test_declared_oversized_body_is_rejected_before_reading():
    messages, received, called = raw_asgi_request([b'private-content'], [(b'content-length', b'100')])
    assert messages[0]["status"] == 413
    assert received == 0
    assert called is False
    assert UUID(dict(messages[0]["headers"])[b'x-request-id'].decode()).version == 4
    assert b'private' not in messages[1]["body"]


def test_chunked_body_without_content_length_is_bounded_during_read():
    messages, received, called = raw_asgi_request([b'1234', b'5678', b'9', b'unread'])
    assert messages[0]["status"] == 413
    assert received == 3
    assert called is False


def test_valid_chunked_body_is_replayed_without_changes():
    messages, received, called = raw_asgi_request([b'ab', b'cd'], [(b'content-type', b'application/octet-stream')])
    assert messages[0]["status"] == 200
    assert messages[1]["body"] == b'abcd'
    assert received == 2
    assert called is True


@pytest.mark.parametrize("length", [b'1', b'6'])
def test_content_length_mismatch_is_rejected(length):
    messages, _, called = raw_asgi_request([b'123'], [(b'content-length', length)])
    assert messages[0]["status"] == 400
    assert called is False


def test_duplicate_content_length_is_rejected():
    messages, received, called = raw_asgi_request([b'{}'], [(b'content-length', b'2'), (b'content-length', b'2')])
    assert messages[0]["status"] == 400
    assert received == 0
    assert called is False


@pytest.mark.parametrize("maximum,overrides", [(True, None), (0, None), (1, {"relative": 10}), (1, {"/raw": False})])
def test_invalid_limit_configuration_is_rejected(maximum, overrides):
    with pytest.raises(ValueError):
        RequestBoundaryMiddleware(application(), max_body_bytes=maximum, path_body_limits=overrides)
