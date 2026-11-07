"""Synthetic HTTP transport checks; no live ML worker or network is used."""

import asyncio
import json
import traceback

import httpx
import pytest

from app.ml_client import MLClient, MLContextLimitExceeded, MLInvalidResponse, MLUnavailable


PRIVATE = "SYNTHETIC_PRIVATE_VALUE"
HEALTH = {
    "status": "ok", "service": "eva-ml", "provider": "llama.cpp",
    "model": "synthetic-model", "schema_version": "1.0.0",
}


def evaluate(handler, *, limit=2 * 1024 * 1024):
    async def run():
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8001", transport=httpx.MockTransport(handler), follow_redirects=True,
        ) as client:
            return await MLClient(client, max_response_bytes=limit).evaluate({"synthetic": True})
    return asyncio.run(run())


def probe(handler, *, timeout_seconds=2.0, limit=2 * 1024 * 1024):
    async def run():
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8001", transport=httpx.MockTransport(handler), follow_redirects=True,
        ) as client:
            return await MLClient(client, max_response_bytes=limit).probe(timeout_seconds=timeout_seconds)
    return asyncio.run(run())


class Chunks(httpx.AsyncByteStream):
    def __init__(self, parts):
        self.parts = parts
        self.read = 0
        self.closed = False

    async def __aiter__(self):
        for part in self.parts:
            self.read += 1
            yield part

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("media_type", ["application/json", "application/json; charset=utf-8", "Application/JSON", "application/vnd.eva+json"])
def test_valid_json_transport_preserves_payload_and_uses_one_post(media_type):
    requests = []
    expected = {"provider": "synthetic", "criteria": [], "text": "Синтетика", "null": None}

    def handler(request):
        requests.append(request)
        assert request.method == "POST" and request.url.path == "/v1/evaluate"
        assert json.loads(request.content) == {"synthetic": True}
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, headers={"Content-Type": media_type}, content=json.dumps(expected, ensure_ascii=False).encode())

    assert evaluate(handler) == expected
    assert len(requests) == 1


@pytest.mark.parametrize("status,expected", [
    (400, MLInvalidResponse), (401, MLInvalidResponse), (404, MLInvalidResponse),
    (413, MLInvalidResponse), (422, MLInvalidResponse), (429, MLInvalidResponse),
    (301, MLInvalidResponse), (302, MLInvalidResponse), (307, MLInvalidResponse), (308, MLInvalidResponse),
    (500, MLInvalidResponse), (501, MLInvalidResponse), (502, MLInvalidResponse),
    (503, MLUnavailable), (504, MLInvalidResponse),
])
def test_http_status_classification_is_safe_and_has_no_retries_or_redirects(status, expected):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={"Location": f"https://example.invalid/{PRIVATE}"},
                              json={"error": "invalid_provider_response", "private": PRIVATE})

    with pytest.raises(expected) as captured:
        evaluate(handler)
    rendered = "".join(traceback.format_exception(captured.value))
    assert PRIVATE not in rendered and "example.invalid" not in rendered
    assert len(requests) == 1


@pytest.mark.parametrize("media_type", ["application/json", "application/vnd.eva+json; charset=utf-8"])
def test_exact_context_error_has_a_typed_safe_exception_without_retry(media_type):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(422, headers={"Content-Type": media_type},
                              content=b'{"error":"context_limit_exceeded"}')

    with pytest.raises(MLContextLimitExceeded) as captured:
        evaluate(handler)
    assert "exceed the local model context" in str(captured.value)
    assert len(requests) == 1


@pytest.mark.parametrize("body", [
    b'{"error":"invalid_request"}', b'{"code":"context_limit_exceeded"}',
    b'{"error":"context_limit_exceeded","error":"context_limit_exceeded"}',
    b'{"error":"context_limit_exceeded","private":"SYNTHETIC_PRIVATE_VALUE"}',
    b'{"error":"context_limit_exceeded","value":NaN}',
    b'{"error":"context_limit_exceeded","value":1e999}',
    b'{"error":"context_limit_exceeded","value":"\\ud800"}',
    b'{"error":"context_limit_exceeded","value":"\xff"}',
    b'[{"error":"context_limit_exceeded"}]', b'{"error":"context_limit_exceeded",}',
])
def test_unrecognized_or_non_strict_422_remains_an_invalid_response(body):
    with pytest.raises(MLInvalidResponse) as captured:
        evaluate(lambda _: httpx.Response(422, headers={"Content-Type": "application/json"}, content=body))
    assert PRIVATE not in "".join(traceback.format_exception(captured.value))


@pytest.mark.parametrize("status", [400, 413, 500, 502])
def test_context_code_on_other_error_status_is_not_a_context_rejection(status):
    with pytest.raises(MLInvalidResponse):
        evaluate(lambda _: httpx.Response(status, json={"error": "context_limit_exceeded"}))


def test_context_code_on_success_is_left_for_canonical_response_validation():
    payload = {"error": "context_limit_exceeded"}
    assert evaluate(lambda _: httpx.Response(200, json=payload)) == payload


def test_health_422_cannot_be_classified_as_evaluation_context_limit():
    with pytest.raises(MLInvalidResponse):
        probe(lambda _: httpx.Response(422, json={"error": "context_limit_exceeded"}))


@pytest.mark.parametrize("headers", [{}, {"Content-Type": "text/plain"},
                                       {"Content-Type": "application/json", "Content-Encoding": "gzip"}])
def test_context_error_requires_uncompressed_json_before_reading(headers):
    stream = Chunks([b'{"error":"context_limit_exceeded"}'])
    with pytest.raises(MLInvalidResponse):
        evaluate(lambda _: httpx.Response(422, headers=headers, stream=stream))
    assert stream.read == 0 and stream.closed


def test_context_error_declared_length_is_bounded_more_tightly_than_success():
    stream = Chunks([b'{"error":"context_limit_exceeded"}'])
    with pytest.raises(MLInvalidResponse):
        evaluate(lambda _: httpx.Response(422, headers={
            "Content-Type": "application/json", "Content-Length": "4097",
        }, stream=stream))
    assert stream.read == 0 and stream.closed


def test_context_error_stream_is_bounded_without_content_length():
    stream = Chunks([b" " * 65536, PRIVATE.encode()])
    with pytest.raises(MLInvalidResponse) as captured:
        evaluate(lambda _: httpx.Response(422, headers={"Content-Type": "application/json"}, stream=stream))
    assert stream.read == 1 and stream.closed
    assert PRIVATE not in str(captured.value)


def test_context_error_obeys_configured_smaller_limit_and_exact_4096_boundary():
    body = b'{"error":"context_limit_exceeded"}'
    with pytest.raises(MLInvalidResponse):
        evaluate(lambda _: httpx.Response(422, headers={"Content-Type": "application/json"}, content=body), limit=8)
    padded = body + b" " * (4096 - len(body))
    with pytest.raises(MLContextLimitExceeded):
        evaluate(lambda _: httpx.Response(422, headers={"Content-Type": "application/json"}, content=padded))


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError])
def test_network_errors_are_unavailable_without_upstream_details(failure):
    requests = []

    def handler(request):
        requests.append(request)
        raise failure(PRIVATE, request=request)

    with pytest.raises(MLUnavailable) as captured:
        evaluate(handler)
    assert PRIVATE not in "".join(traceback.format_exception(captured.value))
    assert len(requests) == 1


@pytest.mark.parametrize("body", [
    b'{"value":NaN}', b'{"value":Infinity}', b'{"value":-Infinity}', b'{"value":1e999}',
    b'{"value":-1e999}', b'{"nested":{"duplicate":1,"duplicate":2}}', b'{"x":1,"\\u0078":2}',
    b'{"text":"\xff"}', b'\xff\xfe{\x00}\x00', b'[]', b'null', b'true', b'123', b'"string"',
    b'{"x":1} {"x":2}', b'{"x":1,}', b'', b'{',
])
def test_rejects_non_strict_utf8_json_and_non_objects(body):
    with pytest.raises(MLInvalidResponse):
        evaluate(lambda _: httpx.Response(200, headers={"Content-Type": "application/json"}, content=body))


def test_strings_that_mention_nonfinite_numbers_are_valid_json():
    assert evaluate(lambda _: httpx.Response(200, json={"value": "NaN Infinity"})) == {"value": "NaN Infinity"}


@pytest.mark.parametrize("body", [
    b'{"criteria":[{"reason":"\\ud800"}]}',
    b'{"criteria":[{"reason":"\\udfff"}]}',
    b'{"warnings":["\\ud800"]}',
    b'{"nested":{"\\ud800":"synthetic"}}',
    b'{"nested":[{"value":["\\udfff"]}]}',
], ids=["high-surrogate-reason", "low-surrogate-reason", "warning", "object-key", "nested-array"])
def test_escaped_unpaired_surrogates_are_rejected_before_storage(body):
    with pytest.raises(MLInvalidResponse) as captured:
        evaluate(lambda _: httpx.Response(200, headers={"Content-Type": "application/json"}, content=body))
    assert captured.value.__suppress_context__ is True
    assert "UnicodeEncodeError" not in "".join(traceback.format_exception(captured.value))


def test_paired_surrogate_escape_is_accepted_as_a_valid_unicode_scalar():
    body = b'{"reason":"\\ud83d\\ude00"}'
    answer = evaluate(lambda _: httpx.Response(200, headers={"Content-Type": "application/json"}, content=body))
    assert answer == {"reason": "\U0001f600"}
    assert json.dumps(answer, ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize("body", [
    b'{"criteria":[{"reason":"synthetic\\u0000text"}]}',
    b'{"warnings":["synthetic\\u0000text"]}',
    b'{"nested":{"synthetic\\u0000key":"value"}}',
], ids=["reason-nul", "array-nul", "key-nul"])
def test_escaped_nul_is_rejected_before_database_or_output_boundaries(body):
    with pytest.raises(MLInvalidResponse) as captured:
        evaluate(lambda _: httpx.Response(200, headers={"Content-Type": "application/json"}, content=body))
    assert captured.value.__suppress_context__ is True
    assert "synthetic" not in str(captured.value)


@pytest.mark.parametrize("field", ["provider", "model"])
@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"], ids=["high", "low"])
def test_probe_rejects_escaped_surrogates_with_a_safe_error(field, surrogate):
    body = json.dumps({**HEALTH, field: PRIVATE + surrogate}, ensure_ascii=True).encode("ascii")
    with pytest.raises(MLInvalidResponse) as captured:
        probe(lambda _: httpx.Response(200, headers={"Content-Type": "application/json"}, content=body))
    rendered = "".join(traceback.format_exception(captured.value))
    assert PRIVATE not in rendered and "UnicodeEncodeError" not in rendered


@pytest.mark.parametrize("body", [
    ('{"private":"' + PRIVATE + '",}').encode(),
    ('{"' + PRIVATE + '":1,"' + PRIVATE + '":2}').encode(),
    ('{"private":"' + PRIVATE + '","value":NaN}').encode(),
])
def test_invalid_json_does_not_expose_body_or_decoder_context(body):
    with pytest.raises(MLInvalidResponse) as captured:
        evaluate(lambda _: httpx.Response(200, headers={"Content-Type": "application/json"}, content=body))
    assert PRIVATE not in "".join(traceback.format_exception(captured.value))
    assert captured.value.__suppress_context__ is True


@pytest.mark.parametrize("media_type", [None, "text/plain", "text/html", "application/octet-stream", "application/+json", "application/x json"])
def test_rejects_missing_or_non_json_content_type(media_type):
    headers = {} if media_type is None else {"Content-Type": media_type}
    with pytest.raises(MLInvalidResponse):
        evaluate(lambda _: httpx.Response(200, headers=headers, content=b'{}'))


@pytest.mark.parametrize("declared", ["1000", "-1", "invalid", "1, 1"])
def test_rejects_oversized_or_invalid_declared_length_without_reading_body(declared):
    stream = Chunks([b'{}'])
    with pytest.raises(MLInvalidResponse):
        evaluate(lambda _: httpx.Response(200, headers={"Content-Type": "application/json", "Content-Length": declared}, stream=stream), limit=32)
    assert stream.read == 0 and stream.closed


def test_stream_limit_applies_without_content_length_and_stops_before_final_chunk():
    stream = Chunks([b'{"x":"', b'x' * 65536, b'y' * 65536, b'z' * 65536, b'"}'])
    with pytest.raises(MLInvalidResponse):
        evaluate(lambda _: httpx.Response(200, headers={"Content-Type": "application/json"}, stream=stream), limit=65536)
    assert stream.read < len(stream.parts) and stream.closed


def test_stream_limit_is_enforced_even_when_declared_length_is_too_small():
    stream = Chunks([b'{"x":"', b'x' * 65536, b'x' * 65536, b'"}'])
    with pytest.raises(MLInvalidResponse):
        evaluate(lambda _: httpx.Response(200, headers={"Content-Type": "application/json", "Content-Length": "2"}, stream=stream), limit=64)
    assert stream.closed


def test_byte_limit_allows_exact_boundary_and_counts_utf8_bytes():
    body = '{"text":"Синтетика"}'.encode()
    handler = lambda _: httpx.Response(200, headers={"Content-Type": "application/json"}, content=body)
    assert evaluate(handler, limit=len(body)) == {"text": "Синтетика"}
    with pytest.raises(MLInvalidResponse):
        evaluate(handler, limit=len(body) - 1)


def test_unexpected_compression_is_rejected_before_streaming():
    stream = Chunks([b'not-a-gzip-body'])
    with pytest.raises(MLInvalidResponse):
        evaluate(lambda _: httpx.Response(200, headers={"Content-Type": "application/json", "Content-Encoding": "gzip"}, stream=stream))
    assert stream.read == 0 and stream.closed


def test_streaming_read_timeout_closes_response_and_is_unavailable():
    class Interrupted(Chunks):
        async def __aiter__(self):
            yield b'{"x":'
            raise httpx.ReadTimeout(PRIVATE)
    stream = Interrupted([])
    with pytest.raises(MLUnavailable) as captured:
        evaluate(lambda _: httpx.Response(200, headers={"Content-Type": "application/json"}, stream=stream))
    assert stream.closed
    assert PRIVATE not in "".join(traceback.format_exception(captured.value))


@pytest.mark.parametrize("provider,model,status", [
    ("llama.cpp", "synthetic-model", "ok"), ("stub", "unconfigured", "ok"),
    ("llama.cpp", "synthetic-model", "loading"),
])
def test_probe_returns_checked_metadata_without_claiming_stub_ready(provider, model, status):
    health = {**HEALTH, "provider": provider, "model": model, "status": status, "ignored": PRIVATE}
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET" and request.url.path == "/health"
        assert request.extensions["timeout"]["read"] == 0.5
        return httpx.Response(200, json=health)

    assert probe(handler, timeout_seconds=0.5) == {field: health[field] for field in HEALTH}
    assert len(requests) == 1


@pytest.mark.parametrize("field,value", [
    ("status", None), ("status", ""), ("service", "backend"), ("provider", " \t"),
    ("model", 5), ("model", "x" * 513), ("model", "synthetic\nmodel"),
    ("schema_version", "2.0.0"), ("schema_version", 1),
])
def test_probe_rejects_wrong_service_schema_or_malformed_fields(field, value):
    with pytest.raises(MLInvalidResponse):
        probe(lambda _: httpx.Response(200, json={**HEALTH, field: value}))


@pytest.mark.parametrize("field", list(HEALTH))
def test_probe_requires_all_metadata_fields(field):
    health = {key: value for key, value in HEALTH.items() if key != field}
    with pytest.raises(MLInvalidResponse):
        probe(lambda _: httpx.Response(200, json=health))


def test_probe_has_a_total_deadline_when_transport_stalls():
    async def handler(_request):
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=HEALTH)
    with pytest.raises(MLUnavailable):
        probe(handler, timeout_seconds=0.01)


def test_probe_has_a_total_deadline_across_slow_chunks():
    class Slow(Chunks):
        async def __aiter__(self):
            for part in self.parts:
                await asyncio.sleep(0.02)
                yield part
    stream = Slow([b'{', b'"status":', b'"ok"', b'}'])
    with pytest.raises(MLUnavailable):
        probe(lambda _: httpx.Response(200, headers={"Content-Type": "application/json"}, stream=stream), timeout_seconds=0.03)
    assert stream.closed


@pytest.mark.parametrize("timeout", [True, 0, -1, float("nan"), float("inf"), "2"])
def test_probe_rejects_invalid_deadlines(timeout):
    with pytest.raises(ValueError):
        probe(lambda _: pytest.fail("Invalid timeout must not make a request"), timeout_seconds=timeout)


@pytest.mark.parametrize("limit", [True, False, 0, -1, 1.5, "32"])
def test_response_limit_must_be_a_positive_integer(limit):
    with pytest.raises(ValueError):
        evaluate(lambda _: pytest.fail("Invalid limit must not make a request"), limit=limit)
