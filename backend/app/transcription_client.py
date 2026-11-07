"""Opt-in, bounded loopback ASR transport; no model or transcription fallback."""

import asyncio
import hashlib
import json
import math
from pathlib import Path
import re

import httpx


class TranscriptionUnavailable(Exception):
    pass


class TranscriptionInvalidResponse(ValueError):
    pass


_FIELDS = {"schema_version", "audio_sha256", "language", "provider", "model", "segments"}
_SEGMENT_FIELDS = {"speaker", "text", "start_seconds", "end_seconds"}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+:-]{0,199}")
_MIME = {"audio/wav": "wav", "audio/mpeg": "mp3", "audio/flac": "flac", "audio/ogg": "ogg"}


def validate_transcription(
    result: object, audio_sha256: str, language: str, duration_seconds: float | None = None,
) -> dict:
    """Validate identity, diarization labels and offsets without guessing roles."""
    def reject():
        raise TranscriptionInvalidResponse("ASR response does not satisfy the transcription contract.")

    if type(result) is not dict or set(result) != _FIELDS:
        reject()
    if (result["schema_version"] != "1.0.0" or result["audio_sha256"] != audio_sha256
            or result["language"] != language or not _SHA256.fullmatch(audio_sha256)):
        reject()
    for name in ("provider", "model"):
        value = result[name]
        if type(value) is not str or not _IDENTIFIER.fullmatch(value) or "://" in value:
            reject()
        if value.strip().lower() in {"stub", "mock", "fixture", "demo", "unconfigured"}:
            reject()
    segments = result["segments"]
    if type(segments) is not list or not 1 <= len(segments) <= 10000:
        reject()
    previous = -1.0
    text_size = 0
    for segment in segments:
        if type(segment) is not dict or set(segment) != _SEGMENT_FIELDS:
            reject()
        if (type(segment["speaker"]) is not str or segment["speaker"] not in {"client", "operator", "unknown"}
                or type(segment["text"]) is not str or not segment["text"].strip() or "\x00" in segment["text"]):
            reject()
        try:
            segment["text"].encode("utf-8", errors="strict")
        except UnicodeError:
            reject()
        text_size += len(segment["text"])
        if len(segment["text"]) > 65536 or text_size > 1_000_000:
            reject()
        start, end = segment["start_seconds"], segment["end_seconds"]
        if (type(start) not in {int, float} or type(end) not in {int, float}
                or start < 0 or end <= start or end > 3600 or start < previous
                or not math.isfinite(start) or not math.isfinite(end)):
            reject()
        if duration_seconds is not None and end > duration_seconds + 0.05:
            reject()
        previous = start
    return {**result, "segments": [dict(segment) for segment in segments]}


def _constant(_value):
    raise ValueError("Non-finite JSON")


def _number(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON")
    return number


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


class TranscriptionClient:
    def __init__(self, client: httpx.AsyncClient, *, max_response_bytes: int = 2 * 1024 * 1024,
                 timeout_seconds: float = 120, max_audio_bytes: int = 25 * 1024 * 1024):
        url = client.base_url
        if (url.scheme != "http" or url.host not in {"127.0.0.1", "localhost", "::1"}
                or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}
                or client.trust_env):
            raise ValueError("ASR requires a plain loopback HTTP client with environment proxies disabled.")
        if (type(timeout_seconds) not in {int, float} or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0):
            raise ValueError("ASR timeout must be finite and positive.")
        if any(type(value) is not int or value <= 0 for value in (max_response_bytes, max_audio_bytes)):
            raise ValueError("ASR byte limits must be positive integers.")
        self.client = client
        self.max_response_bytes = max_response_bytes
        self.timeout_seconds = timeout_seconds
        self.max_audio_bytes = max_audio_bytes

    async def transcribe(self, path: Path, content_type: str, language: str, audio_sha256: str) -> dict:
        if (type(language) is not str or language not in {"RU", "KZ", "EN"}
                or type(audio_sha256) is not str or not _SHA256.fullmatch(audio_sha256)
                or type(content_type) is not str or content_type not in _MIME):
            raise ValueError("Invalid ASR request metadata.")
        try:
            with Path(path).open("rb") as audio:
                digest, size = hashlib.sha256(), 0
                while block := audio.read(64 * 1024):
                    size += len(block)
                    if size > self.max_audio_bytes:
                        raise TranscriptionInvalidResponse("Audio exceeds the ASR upload limit.")
                    digest.update(block)
                if not size or digest.hexdigest() != audio_sha256:
                    raise TranscriptionInvalidResponse("Audio does not match its stored identity.")
                audio.seek(0)
                async with asyncio.timeout(self.timeout_seconds):
                    async with self.client.stream(
                        "POST", "/v1/transcribe",
                        files={"file": ("audio." + _MIME[content_type], audio, content_type)},
                        data={"language": language, "audio_sha256": audio_sha256},
                        headers={"Accept": "application/json", "Accept-Encoding": "identity"},
                        follow_redirects=False, timeout=self.timeout_seconds,
                    ) as response:
                        if response.status_code == 503:
                            raise TranscriptionUnavailable("Local ASR service is unavailable.")
                        if response.status_code != 200:
                            raise TranscriptionInvalidResponse("ASR returned an unexpected HTTP status.")
                        media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                        if media != "application/json" and not re.fullmatch(r"application/[a-z0-9!#$&^_.+-]+\+json", media):
                            raise TranscriptionInvalidResponse("ASR response must have a JSON content type.")
                        if response.headers.get("content-encoding", "identity").lower().strip() != "identity":
                            raise TranscriptionInvalidResponse("ASR response encoding is unsupported.")
                        declared = response.headers.get("content-length")
                        if declared is not None:
                            normalized = declared.lstrip("0") or "0"
                            if (not declared.isascii() or not declared.isdecimal()
                                    or len(normalized) > len(str(self.max_response_bytes))
                                    or int(normalized) > self.max_response_bytes):
                                raise TranscriptionInvalidResponse("ASR response exceeds its size limit.")
                        body = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                            if len(body) + len(chunk) > self.max_response_bytes:
                                raise TranscriptionInvalidResponse("ASR response exceeds its size limit.")
                            body.extend(chunk)
        except (httpx.RequestError, TimeoutError):
            raise TranscriptionUnavailable("Local ASR service is unavailable.") from None
        except OSError:
            raise TranscriptionUnavailable("Stored audio could not be read.") from None
        try:
            result = json.loads(body.decode("utf-8"), parse_constant=_constant,
                                parse_float=_number, object_pairs_hook=_object)
        except (ValueError, RecursionError):
            raise TranscriptionInvalidResponse("ASR did not return strict UTF-8 JSON.") from None
        return validate_transcription(result, audio_sha256, language)
