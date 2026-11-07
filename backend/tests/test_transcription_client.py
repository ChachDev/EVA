"""Only synthetic audio bytes and mock HTTP; no recognizer is launched."""

import asyncio
from copy import deepcopy
import hashlib
import json
import traceback

import httpx
import pytest

from app.transcription_client import (
    TranscriptionClient, TranscriptionInvalidResponse, TranscriptionUnavailable, validate_transcription,
)


AUDIO = b"SYNTHETIC_AUDIO_BYTES"
SHA = hashlib.sha256(AUDIO).hexdigest()
PRIVATE = "SYNTHETIC_PRIVATE_VALUE"


def result():
    return {"schema_version": "1.0.0", "audio_sha256": SHA, "language": "RU", "provider": "synthetic-asr",
            "model": "synthetic-model", "segments": [
                {"speaker": "client", "text": "Synthetic request", "start_seconds": 0, "end_seconds": 0.5},
                {"speaker": "operator", "text": "Synthetic response", "start_seconds": 0.5, "end_seconds": 1},
            ]}


def invoke(tmp_path, handler, *, options=None, data=AUDIO, sha=SHA, language="RU"):
    path = tmp_path / "synthetic.wav"
    path.write_bytes(data)

    async def run():
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8765", transport=httpx.MockTransport(handler),
                                     trust_env=False, follow_redirects=True) as client:
            return await TranscriptionClient(client, **(options or {})).transcribe(path, "audio/wav", language, sha)
    return asyncio.run(run())


def test_multipart_upload_and_verified_response(tmp_path):
    calls = []

    async def handler(request):
        calls.append(request)
        body = await request.aread()
        assert request.method == "POST" and request.url.path == "/v1/transcribe"
        assert "multipart/form-data" in request.headers["content-type"]
        assert b'name="file"; filename="audio.wav"' in body and AUDIO in body
        assert b'name="language"' in body and b'RU' in body
        assert b'name="audio_sha256"' in body and SHA.encode() in body
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, json=result())

    assert invoke(tmp_path, handler) == result()
    assert len(calls) == 1


@pytest.mark.parametrize("speaker", ["client", "operator", "unknown"])
def test_known_and_unknown_speakers_are_preserved(tmp_path, speaker):
    expected = result()
    expected["segments"][0]["speaker"] = speaker
    assert invoke(tmp_path, lambda _: httpx.Response(200, json=expected))["segments"][0]["speaker"] == speaker


@pytest.mark.parametrize("status,expected", [
    (503, TranscriptionUnavailable), (500, TranscriptionInvalidResponse), (502, TranscriptionInvalidResponse),
    (504, TranscriptionInvalidResponse), (400, TranscriptionInvalidResponse), (422, TranscriptionInvalidResponse),
    (301, TranscriptionInvalidResponse), (307, TranscriptionInvalidResponse),
])
def test_http_errors_never_retry_redirect_or_echo_body(tmp_path, status, expected):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://example.invalid/" + PRIVATE}, json={"error": PRIVATE})

    with pytest.raises(expected) as captured:
        invoke(tmp_path, handler)
    assert len(calls) == 1
    assert PRIVATE not in "".join(traceback.format_exception(captured.value))


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout])
def test_network_failures_are_sanitized(tmp_path, failure):
    def handler(request):
        raise failure(PRIVATE, request=request)
    with pytest.raises(TranscriptionUnavailable) as captured:
        invoke(tmp_path, handler)
    assert PRIVATE not in "".join(traceback.format_exception(captured.value))


@pytest.mark.parametrize("body", [b'{"x":NaN}', b'{"x":1e999}', b'{"x":1,"x":2}', b'{"x":"\xff"}', b'[]', b'null', b'{}'])
def test_strict_json_and_response_contract(tmp_path, body):
    with pytest.raises(TranscriptionInvalidResponse):
        invoke(tmp_path, lambda _: httpx.Response(200, headers={"Content-Type": "application/json"}, content=body))


@pytest.mark.parametrize("field,value", [
    ("schema_version", "2.0.0"), ("audio_sha256", "a" * 64), ("language", "KZ"),
    ("provider", "stub"), ("model", "unconfigured"), ("model", ""), ("provider", " \n"),
    ("model", "x" * 201), ("model", "https://example.invalid/private"), ("provider", "private value"),
    ("segments", []), ("segments", None),
])
def test_response_identity_and_provider_are_checked(tmp_path, field, value):
    answer = result()
    answer[field] = value
    with pytest.raises(TranscriptionInvalidResponse):
        invoke(tmp_path, lambda _: httpx.Response(200, json=answer))


@pytest.mark.parametrize("field,value", [
    ("speaker", "agent"), ("speaker", "system"), ("speaker", None), ("text", " \t"), ("text", False),
    ("start_seconds", True), ("start_seconds", -1), ("end_seconds", False), ("end_seconds", 0),
    ("end_seconds", 3601), ("end_seconds", 10**400),
])
def test_segments_require_real_roles_text_and_finite_ordered_offsets(tmp_path, field, value):
    answer = result()
    answer["segments"][0][field] = value
    with pytest.raises(TranscriptionInvalidResponse):
        invoke(tmp_path, lambda _: httpx.Response(200, json=answer))


def test_rejects_out_of_order_segments_and_past_audio_duration():
    answer = result()
    answer["segments"].reverse()
    with pytest.raises(TranscriptionInvalidResponse):
        validate_transcription(answer, SHA, "RU")
    with pytest.raises(TranscriptionInvalidResponse):
        validate_transcription(result(), SHA, "RU", duration_seconds=0.2)


@pytest.mark.parametrize("location", ["root", "segment"])
def test_extra_fields_are_rejected(location):
    answer = result()
    (answer if location == "root" else answer["segments"][0])["extra"] = PRIVATE
    with pytest.raises(TranscriptionInvalidResponse) as captured:
        validate_transcription(answer, SHA, "RU")
    assert PRIVATE not in str(captured.value)


def test_result_is_an_independent_copy():
    source = result()
    before = deepcopy(source)
    checked = validate_transcription(source, SHA, "RU")
    checked["segments"][0]["speaker"] = "unknown"
    assert source == before


def test_escaped_unicode_surrogates_are_rejected_before_database_utf8_encoding(tmp_path):
    answer = result()
    answer["segments"][0]["text"] = "\ud800"
    body = json.dumps(answer, ensure_ascii=True).encode("ascii")
    with pytest.raises(TranscriptionInvalidResponse):
        invoke(tmp_path, lambda _: httpx.Response(200, headers={"Content-Type": "application/json"}, content=body))


def test_escaped_nul_in_transcript_is_rejected_before_persistence(tmp_path):
    answer = result()
    answer["segments"][0]["text"] = "Synthetic\x00text"
    body = json.dumps(answer, ensure_ascii=True).encode("ascii")
    with pytest.raises(TranscriptionInvalidResponse):
        invoke(tmp_path, lambda _: httpx.Response(200, headers={"Content-Type": "application/json"}, content=body))


def test_audio_hash_and_upload_limit_are_checked_before_network(tmp_path):
    for options, sha in (({}, "0" * 64), ({"max_audio_bytes": 1}, SHA)):
        with pytest.raises(TranscriptionInvalidResponse):
            invoke(tmp_path, lambda _: pytest.fail("Unexpected request"), options=options, sha=sha)


@pytest.mark.parametrize("content_type", [None, "text/plain", "text/html"])
def test_json_content_type_is_required(tmp_path, content_type):
    headers = {} if content_type is None else {"Content-Type": content_type}
    with pytest.raises(TranscriptionInvalidResponse):
        invoke(tmp_path, lambda _: httpx.Response(200, headers=headers, content=json.dumps(result()).encode()))


def test_declared_and_streamed_response_size_are_bounded(tmp_path):
    with pytest.raises(TranscriptionInvalidResponse):
        invoke(tmp_path, lambda _: httpx.Response(200, json=result()), options={"max_response_bytes": 10})

    class Oversized(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield b'{"text":"'
            yield b'x' * 65536
            yield b'x' * 65536
            pytest.fail("The entire oversized body was consumed")
        async def aclose(self):
            self.closed = True
    stream = Oversized()
    with pytest.raises(TranscriptionInvalidResponse):
        invoke(tmp_path, lambda _: httpx.Response(200, headers={"Content-Type": "application/json"}, stream=stream),
               options={"max_response_bytes": 65536})
    assert stream.closed


def test_whole_request_has_a_deadline(tmp_path):
    async def handler(_):
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=result())
    with pytest.raises(TranscriptionUnavailable):
        invoke(tmp_path, handler, options={"timeout_seconds": 0.01})


@pytest.mark.parametrize("base", ["https://127.0.0.1:8001", "http://example.invalid", "http://127.0.0.1/path", "http://user:secret@127.0.0.1"])
def test_client_rejects_non_loopback_or_credentialed_origins(base):
    async def run():
        async with httpx.AsyncClient(base_url=base, trust_env=False) as client:
            with pytest.raises(ValueError):
                TranscriptionClient(client)
    asyncio.run(run())


def test_environment_proxies_are_not_allowed():
    async def run():
        async with httpx.AsyncClient(base_url="http://127.0.0.1", trust_env=True) as client:
            with pytest.raises(ValueError):
                TranscriptionClient(client)
    asyncio.run(run())
