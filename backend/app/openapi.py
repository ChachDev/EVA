"""Describe the existing HTTP API with its canonical, locally resolved contracts."""

from copy import deepcopy
from urllib.parse import unquote

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from .contracts import Contracts


_AUDIO_TYPES = ("audio/wav", "audio/x-wav", "audio/mpeg", "audio/flac", "audio/ogg")
_ERROR_DESCRIPTIONS = {
    400: "Malformed request headers or UTF-8 JSON.",
    404: "Requested saved resource was not found.",
    409: "Immutable content, checklist version or expected review revision conflicts with stored data.",
    413: "Request exceeds the configured byte limit.",
    415: "Unsupported content type or content encoding.",
    422: "Request does not satisfy its contract, or the evaluation exceeds the local model context.",
    502: "ML returned an invalid result; no evaluation was saved.",
    503: "Local storage, model service or configured processing capacity is unavailable.",
}
# (path, method): (request contract, success status, response contract, array response, errors)
_OPERATIONS = {
    ("/api/v1/checklists", "get"): (None, 200, "checklist", True, (503,)),
    ("/api/v1/conversations", "get"): (None, 200, "conversation", True, (503,)),
    ("/api/v1/conversations", "post"): ("conversation-create", 201, "conversation", False, (409, 422, 503)),
    ("/api/v1/conversations/{conversation_id}", "get"): (None, 200, "conversation", False, (404, 503)),
    ("/api/v1/checklists/{checklist_id}/versions", "post"): ("checklist-publish", 201, "checklist", False, (404, 409, 422, 503)),
    ("/api/v1/evaluations", "get"): (None, 200, "evaluation-list", False, (422, 503)),
    ("/api/v1/evaluations", "post"): ("evaluation-create", 201, "evaluation-record", False, (404, 409, 422, 502, 503)),
    ("/api/v1/evaluations/{evaluation_id}", "get"): (None, 200, "evaluation-record", False, (404, 503)),
    ("/api/v1/evaluations/{eid}/audit", "get"): (None, 200, "audit-export", False, (404, 422, 503)),
    ("/api/v1/evaluations/{evaluation_id}/snapshot", "get"): (None, 200, "evaluation-request", False, (404, 409, 422, 503)),
    ("/api/v1/review-cases/{evaluation_id}", "get"): (None, 200, "review-case", False, (404, 422, 503)),
    ("/api/v1/evaluations/{evaluation_id}/appeals", "post"): ("appeal-create", 201, "review-case", False, (404, 409, 422, 503)),
    ("/api/v1/appeals/{appeal_id}/decisions", "post"): ("appeal-decision", 201, "review-case", False, (404, 409, 422, 503)),
    ("/api/v1/audio-assets", "post"): (None, 201, "audio-asset", False, (409, 422, 503)),
    ("/api/v1/audio-assets/{asset_id}", "get"): (None, 200, "audio-asset", False, (404, 422, 503)),
    ("/api/v1/transcriptions", "post"): ("transcription-create", 202, "transcription-job", False, (404, 409, 422, 503)),
    ("/api/v1/transcriptions/{job_id}", "get"): (None, 200, "transcription-job", False, (404, 422, 503)),
    ("/api/v1/transcriptions/{job_id}/review", "post"): ("transcription-review", 200, "transcription-job", False, (404, 409, 422, 503)),
}


def _reference(name: str) -> dict:
    return {"$ref": f"#/components/schemas/Contract_{name}"}


def _embed_schema(value, owner: str, identifiers: dict[str, str]):
    if isinstance(value, list):
        return [_embed_schema(item, owner, identifiers) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, child in value.items():
        if key == "$ref":
            resource, _, fragment = child.partition("#")
            target = identifiers.get(resource) if resource else owner
            fragment = unquote(fragment)
            if target is None or (fragment and not fragment.startswith("/")):
                raise ValueError("Canonical OpenAPI references must target local schema JSON Pointers.")
            result[key] = _reference(target)["$ref"] + fragment
        else:
            result[key] = _embed_schema(child, owner, identifiers)
    return result


def _json_response(schema: dict, description: str) -> dict:
    return {"description": description, "content": {"application/json": {"schema": schema}}}


def install_contract_openapi(app: FastAPI, contracts: Contracts) -> None:
    """Customize documentation only; route validation and response handling are unchanged."""
    app.openapi_schema = None
    cached_routes = None

    def contract_openapi() -> dict:
        nonlocal cached_routes
        route_ids = tuple(id(route) for route in app.routes)
        if app.openapi_schema is not None and cached_routes == route_ids:
            return app.openapi_schema
        document = get_openapi(
            title=app.title, version=app.version, openapi_version=app.openapi_version,
            summary=app.summary, description=app.description, routes=app.routes,
            webhooks=app.webhooks.routes, tags=app.openapi_tags, servers=app.servers,
            terms_of_service=app.terms_of_service, contact=app.contact, license_info=app.license_info,
            separate_input_output_schemas=app.separate_input_output_schemas,
            external_docs=app.openapi_external_docs,
        )
        components = document.setdefault("components", {}).setdefault("schemas", {})
        identifiers = {schema["$id"]: name for name, schema in contracts.schemas.items()}
        for name, schema in contracts.schemas.items():
            embedded = _embed_schema(deepcopy(schema), name, identifiers)
            # Component references resolve against the OpenAPI document, not eva.local.
            embedded.pop("$id", None)
            components[f"Contract_{name}"] = embedded
        components["BackendError"] = {
            "type": "object", "required": ["detail"],
            "properties": {"detail": {"type": "string"},
                           "code": {"type": "string", "enum": ["ml_context_limit_exceeded"]}},
            "additionalProperties": False,
        }
        error_schema = {"$ref": "#/components/schemas/BackendError"}
        for (path, method), (request, status, response, array, errors) in _OPERATIONS.items():
            operation = document.get("paths", {}).get(path, {}).get(method)
            if operation is None:
                continue
            if request:
                operation["requestBody"] = {
                    "required": True, "content": {"application/json": {"schema": _reference(request)}},
                }
            response_schema = _reference(response)
            if array:
                response_schema = {"type": "array", "items": response_schema}
            operation["responses"][str(status)] = _json_response(response_schema, "Successful response.")
            codes = set(errors)
            if method == "post":
                codes.update((400, 413, 415))
            # FastAPI's default 422 body contains an array; our boundary returns a safe string.
            if "422" in operation["responses"]:
                codes.add(422)
            for code in sorted(codes):
                operation["responses"][str(code)] = _json_response(error_schema, _ERROR_DESCRIPTIONS[code])

        health = document.get("paths", {}).get("/health", {}).get("get")
        if health:
            health["responses"]["200"] = _json_response({
                "type": "object", "required": ["status", "service", "schema_version"],
                "properties": {"status": {"const": "ok"}, "service": {"const": "backend"},
                               "schema_version": {"const": "1.0.0"}}, "additionalProperties": False,
            }, "Process health; database and model readiness are checked by the local doctor command.")
        upload = document.get("paths", {}).get("/api/v1/audio-assets", {}).get("post")
        if upload:
            upload["requestBody"] = {
                "required": True, "description": "Raw audio bytes. Configured upload limits apply.",
                "content": {kind: {"schema": {"type": "string", "format": "binary"}} for kind in _AUDIO_TYPES},
            }
            for parameter in upload.get("parameters", []):
                if parameter.get("in") == "header" and parameter.get("name", "").lower() == "idempotency-key":
                    parameter["schema"] = {"type": "string", "format": "uuid"}
                    parameter["description"] = "Stable UUID; repeating identical audio returns its saved asset."
        content = document.get("paths", {}).get("/api/v1/audio-assets/{asset_id}/content", {}).get("get")
        if content:
            audio = {kind: {"schema": {"type": "string", "format": "binary"}} for kind in _AUDIO_TYPES if kind != "audio/x-wav"}
            headers = {
                "Accept-Ranges": {"schema": {"type": "string", "const": "bytes"}},
                "ETag": {"description": "Quoted SHA-256 of the saved audio.", "schema": {"type": "string"}},
            }
            content["responses"]["200"] = {"description": "Original saved audio bytes.", "content": audio, "headers": deepcopy(headers)}
            headers["Content-Range"] = {"schema": {"type": "string"}}
            content["responses"]["206"] = {
                "description": "Requested byte range or ranges.", "headers": headers,
                "content": {**deepcopy(audio), "multipart/byteranges": {"schema": {"type": "string", "format": "binary"}}},
            }
            for code in (404, 422, 503):
                content["responses"][str(code)] = _json_response(error_schema, _ERROR_DESCRIPTIONS[code])
            content["responses"]["400"] = {"description": "Malformed Range header.", "content": {"text/plain": {"schema": {"type": "string"}}}}
            content["responses"]["416"] = {"description": "Requested range is outside the saved audio.", "headers": {"Content-Range": {"schema": {"type": "string"}}}}
            content.setdefault("parameters", []).extend([
                {"in": "header", "name": "Range", "required": False, "schema": {"type": "string"}, "description": "HTTP byte range, for example bytes=0-1023."},
                {"in": "header", "name": "If-Range", "required": False, "schema": {"type": "string"}, "description": "Saved ETag or modification date required for a partial response."},
            ])
        app.openapi_schema = document
        cached_routes = route_ids
        return document

    app.openapi = contract_openapi
