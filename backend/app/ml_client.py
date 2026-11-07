"""Bounded local ML transport with strict JSON and safe public errors."""

import asyncio
import json
import math
import re

import httpx


DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_JSON_MEDIA_TYPE = re.compile(r"application/(?:json|[a-z0-9!#$&^_.+-]+\+json)")
_HEALTH_FIELDS = ("status", "service", "provider", "model", "schema_version")


class MLUnavailable(Exception):
    pass


class MLInvalidResponse(Exception):
    pass


class MLClient:
    def __init__(self, client: httpx.AsyncClient, max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES):
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("ML response limit must be a positive integer.")
        self.client = client
        self.max_response_bytes = max_response_bytes

    async def _request_json(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        timeout_seconds: float | None = None,
    ) -> dict:
        options = {} if timeout_seconds is None else {"timeout": timeout_seconds}
        try:
            async with self.client.stream(
                method,
                path,
                json=payload,
                headers={"Accept": "application/json", "Accept-Encoding": "identity"},
                follow_redirects=False,
                **options,
            ) as response:
                if response.status_code == 503:
                    raise MLUnavailable("Local ML service is unavailable.")
                if not 200 <= response.status_code < 300:
                    # Includes invalid_provider_response (500), unexpected client
                    # errors, redirects, and other upstream server errors. Never
                    # echo an upstream URL, body, or error message to the caller.
                    raise MLInvalidResponse("ML returned an unexpected HTTP status.")
                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if not _JSON_MEDIA_TYPE.fullmatch(media_type):
                    raise MLInvalidResponse("ML response must have a JSON content type.")
                # The worker normally returns uncompressed JSON. Reject an
                # unexpected compressed stream rather than decompressing an
                # unbounded body before applying our decoded-byte limit.
                if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
                    raise MLInvalidResponse("ML response encoding is unsupported.")
                declared = response.headers.get("content-length")
                if declared is not None:
                    if not declared.isascii() or not declared.isdecimal():
                        raise MLInvalidResponse("ML response length is invalid.")
                    try:
                        too_large = int(declared) > self.max_response_bytes
                    except ValueError:
                        raise MLInvalidResponse("ML response length is invalid.") from None
                    if too_large:
                        raise MLInvalidResponse("ML response exceeds its size limit.")
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    if len(body) + len(chunk) > self.max_response_bytes:
                        raise MLInvalidResponse("ML response exceeds its size limit.")
                    body.extend(chunk)
        except httpx.RequestError:
            raise MLUnavailable("Local ML service is unavailable.") from None

        try:
            result = json.loads(
                body.decode("utf-8", errors="strict"),
                parse_constant=_reject_constant,
                parse_float=_finite_float,
                object_pairs_hook=_unique_object,
            )
            # UTF-8 bytes can still contain JSON escapes for unpaired surrogate
            # code points. Reject them in keys and nested values before storage,
            # response rendering, or diagnostic output attempts UTF-8 encoding.
            _validate_utf8_scalars(result)
        except (ValueError, RecursionError):
            raise MLInvalidResponse("ML did not return strict UTF-8 JSON.") from None
        if type(result) is not dict:
            raise MLInvalidResponse("ML response must be an object.")
        return result

    async def evaluate(self, request: dict) -> dict:
        return await self._request_json("POST", "/v1/evaluate", request)

    async def probe(self, timeout_seconds: float = 2.0) -> dict[str, str]:
        """Return checked health metadata, including an explicitly selected stub.

        This observes the provider; it does not declare a model ready. The caller
        must distinguish stub/unconfigured and inspect the returned status.
        The deadline covers the full probe, not just individual socket reads.
        """
        if (type(timeout_seconds) not in {int, float}
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("ML probe timeout must be finite and positive.")
        try:
            async with asyncio.timeout(timeout_seconds):
                result = await self._request_json("GET", "/health", timeout_seconds=timeout_seconds)
        except TimeoutError:
            raise MLUnavailable("Local ML health probe timed out.") from None
        metadata = {}
        for field in _HEALTH_FIELDS:
            value = result.get(field)
            if (type(value) is not str or not value.strip() or len(value) > 512
                    or any(ord(character) < 32 or ord(character) == 127 for character in value)):
                raise MLInvalidResponse("ML health metadata is invalid.")
            metadata[field] = value
        if metadata["service"] != "eva-ml" or metadata["schema_version"] != "1.0.0":
            raise MLInvalidResponse("ML health service or schema is incompatible.")
        return metadata


def _reject_constant(_value: str):
    raise ValueError("Non-finite JSON constant")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON object key")
        value[key] = item
    return value


def _validate_utf8_scalars(value: object) -> None:
    if type(value) is str:
        if "\x00" in value:
            raise ValueError("NUL is not allowed in ML response strings")
        value.encode("utf-8", errors="strict")
    elif type(value) is dict:
        for key, child in value.items():
            _validate_utf8_scalars(key)
            _validate_utf8_scalars(child)
    elif type(value) is list:
        for child in value:
            _validate_utf8_scalars(child)
