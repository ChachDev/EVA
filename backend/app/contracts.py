"""Validate canonical, read-only JSON Schemas and cross-document invariants."""

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


class ContractError(ValueError):
    pass


def fingerprint(value: Any) -> str:
    document = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(document.encode()).hexdigest()


def _finite_numbers(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractError("Non-finite numbers are not valid contract data.")
    if isinstance(value, dict):
        for child in value.values():
            _finite_numbers(child)
    elif isinstance(value, list):
        for child in value:
            _finite_numbers(child)


def _unique(items: list[dict], key: str, label: str) -> set[str]:
    ids = [item[key] for item in items]
    if len(ids) != len(set(ids)):
        raise ContractError(f"Duplicate {label} IDs.")
    return set(ids)


def validate_request_semantics(request: dict) -> None:
    _unique(request["conversation"]["messages"], "id", "message")
    _unique(request["checklist"]["criteria"], "id", "criterion")
    for criterion in request["checklist"]["criteria"]:
        if criterion["section_id"] == "":
            raise ContractError("Section IDs must be nonempty or null.")


def validate_result_semantics(request: dict, result: dict) -> None:
    validate_request_semantics(request)
    checklist = request["checklist"]
    conversation = request["conversation"]
    if (result["conversation_id"] != conversation["id"]
            or result["checklist_id"] != checklist["id"]
            or result["checklist_version"] != checklist["version"]):
        raise ContractError("ML response identity does not match the request.")
    expected = {criterion["id"]: criterion for criterion in checklist["criteria"]}
    actual = _unique(result["criteria"], "criterion_id", "result criterion")
    if actual != set(expected):
        raise ContractError("ML must return every requested criterion exactly once.")
    message_ids = {message["id"] for message in conversation["messages"]}
    for item in result["criteria"]:
        criterion = expected[item["criterion_id"]]
        value = item["value"]
        evidence_kinds = set()
        for evidence in item["evidence"]:
            evidence_kinds.add(evidence["kind"])
            if not set(evidence["message_ids"]).issubset(message_ids):
                raise ContractError("Evidence refers to an unknown message.")
        if item["status"] == "needs_review":
            if value is not None or evidence_kinds != {"uncertain"}:
                raise ContractError("Pending judgments must be null and contain only uncertain evidence.")
        elif "uncertain" in evidence_kinds:
            raise ContractError("Scored judgments cannot contain uncertain evidence, including NA.")
        elif value is None:
            if not criterion["allow_na"] or evidence_kinds != {"not_applicable"}:
                raise ContractError("NA requires an allowed criterion and only not_applicable evidence.")
        else:
            if "not_applicable" in evidence_kinds:
                raise ContractError("A scored value cannot carry NA evidence.")
            if criterion["type"] in {"boolean", "flag"}:
                if type(value) is not bool:
                    raise ContractError("Boolean and flag values must be JSON booleans, not numbers.")
            elif type(value) not in {int, float} or value not in criterion["scale"]:
                raise ContractError("Scale values must be numbers from the configured scale, not booleans.")


class Contracts:
    def __init__(self, directory: Path):
        self.directory = directory
        self.schemas = {
            path.name.removesuffix(".schema.json"): json.loads(path.read_text(encoding="utf-8"))
            for path in (directory / "schemas").glob("*.schema.json")
        }
        required = {"checklist", "conversation", "evaluation-request", "evaluation-response",
                    "evaluation-create", "evaluation-record", "score-summary"}
        if not required.issubset(self.schemas):
            raise RuntimeError("Canonical contracts are missing; restore contracts/schemas first.")
        resources = []
        for name, schema in self.schemas.items():
            Draft202012Validator.check_schema(schema)
            expected_id = f"https://eva.local/schemas/{name}.schema.json"
            if schema.get("$id") != expected_id:
                raise RuntimeError("Unexpected canonical schema identifier.")
            resources.append((expected_id, Resource.from_contents(schema)))
        # No HTTP retrieval: even eva.local references resolve from local files only.
        registry = Registry().with_resources(resources)
        self.validators = {
            name: Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
            for name, schema in self.schemas.items()
        }

    def validate(self, name: str, value: Any) -> None:
        _finite_numbers(value)
        error = next(self.validators[name].iter_errors(value), None)
        if error:
            location = "/".join(str(part) for part in error.absolute_path) or "<root>"
            # Do not echo rejected values: future inputs may contain client data.
            raise ContractError(f"Invalid {name} at {location} ({error.validator}).")

    def example(self, name: str) -> dict:
        value = json.loads((self.directory / "examples" / f"{name}.json").read_text(encoding="utf-8"))
        self.validate(name, value)
        return value

    def validate_request(self, request: dict) -> None:
        self.validate("evaluation-request", request)
        validate_request_semantics(request)

    def validate_result(self, request: dict, result: dict) -> None:
        self.validate_request(request)
        self.validate("evaluation-response", result)
        validate_result_semantics(request, result)
