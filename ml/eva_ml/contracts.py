"""Validate shared contracts using only the repository's local schemas."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource


SCHEMA_DIRECTORY = Path(__file__).resolve().parents[2] / "contracts" / "schemas"


class ContractError(ValueError):
    """Safe errors contain paths and rules, never conversation text."""

    def __init__(self, issues: list[dict[str, str]]) -> None:
        self.issues = issues
        super().__init__("Contract validation failed")


def _reject_retrieval(uri: str) -> Resource:
    # Even an accidentally added HTTP $ref must never make a network request.
    raise NoSuchResource(ref=uri)


def _reject(path: str, rule: str) -> None:
    raise ContractError([{"path": path, "rule": rule}])


class Contracts:
    def __init__(self, directory: Path = SCHEMA_DIRECTORY) -> None:
        schemas: dict[str, dict[str, Any]] = {}
        registry = Registry(retrieve=_reject_retrieval)
        for path in sorted(directory.glob("*.schema.json")):
            schema = json.loads(path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            expected_id = f"https://eva.local/schemas/{path.name}"
            if schema.get("$id") != expected_id:
                raise RuntimeError(f"Unexpected schema identifier: {path.name}")
            registry = registry.with_resource(expected_id, Resource.from_contents(schema))
            schemas[path.name] = schema

        self.validators: dict[str, Draft202012Validator] = {}
        for kind in ("request", "response"):
            name = f"evaluation-{kind}.schema.json"
            if name not in schemas:
                raise RuntimeError(f"Required shared schema missing: {name}")
            self.validators[kind] = Draft202012Validator(
                schemas[name], registry=registry, format_checker=FormatChecker()
            )

    def _validate_schema(self, kind: str, payload: Any) -> None:
        issues = [
            {
                "path": "/" + "/".join(str(part) for part in error.absolute_path),
                "rule": str(error.validator),
            }
            for error in self.validators[kind].iter_errors(payload)
        ]
        if issues:
            raise ContractError(issues)

    def validate_request(self, payload: Any) -> None:
        self._validate_schema("request", payload)
        messages = payload["conversation"]["messages"]
        criteria = payload["checklist"]["criteria"]
        if not messages:
            _reject("/conversation/messages", "at_least_one_message")
        for i,message in enumerate(messages):
            if message.get("event") and message["speaker"]!="system":
                _reject(f"/conversation/messages/{i}/event","trusted_system_source")
            end=message.get("end_seconds")
            start=message.get("offset_seconds")
            if end is not None and (start is None or end<start or not math.isfinite(end)):
                _reject(f"/conversation/messages/{i}/end_seconds","source_fragment_bounds")
        if len({message["id"] for message in messages}) != len(messages):
            _reject("/conversation/messages", "unique_message_ids")
        if len({criterion["id"] for criterion in criteria}) != len(criteria):
            _reject("/checklist/criteria", "unique_criterion_ids")
        for index, criterion in enumerate(criteria):
            if not math.isfinite(criterion["weight"]):
                _reject(f"/checklist/criteria/{index}/weight", "finite_number")
            if any(not math.isfinite(value) for value in criterion["scale"]):
                _reject(f"/checklist/criteria/{index}/scale", "finite_numbers")
            if criterion["type"] == "scale" and not criterion["scale"]:
                _reject(f"/checklist/criteria/{index}/scale", "nonempty_scale")

    def validate_response(self, request: dict[str, Any], response: Any) -> None:
        """Check referential integrity that JSON Schema alone cannot express."""
        self._validate_schema("response", response)
        expected = {
            "schema_version": request["schema_version"],
            "conversation_id": request["conversation"]["id"],
            "checklist_id": request["checklist"]["id"],
            "checklist_version": request["checklist"]["version"],
        }
        for key, value in expected.items():
            if response[key] != value:
                _reject(f"/{key}", "matches_request")

        criteria = {item["id"]: item for item in request["checklist"]["criteria"]}
        results = response["criteria"]
        ids = [result["criterion_id"] for result in results]
        if len(ids) != len(set(ids)) or set(ids) != set(criteria):
            _reject("/criteria", "exactly_one_result_per_requested_criterion")

        message_ids = {message["id"] for message in request["conversation"]["messages"]}
        for index, result in enumerate(results):
            path = f"/criteria/{index}"
            criterion = criteria[result["criterion_id"]]
            evidence = result["evidence"]
            if not evidence:
                _reject(f"{path}/evidence", "evidence_required")
            for item in evidence:
                if not set(item["message_ids"]).issubset(message_ids):
                    _reject(f"{path}/evidence", "known_message_ids")
                if item["kind"] == "message" and not item["message_ids"]:
                    _reject(f"{path}/evidence", "message_evidence_requires_ids")

            kinds = {item["kind"] for item in evidence}
            value = result["value"]
            if result["status"] == "needs_review":
                if value is not None or kinds != {"uncertain"}:
                    _reject(path, "uncertainty_is_not_a_score_or_na")
                continue
            if "uncertain" in kinds:
                _reject(path, "scored_result_cannot_have_uncertain_evidence")
            if value is None:
                if not criterion["allow_na"] or kinds != {"not_applicable"}:
                    _reject(path, "na_requires_permission_and_explanation")
            elif "not_applicable" in kinds:
                _reject(path, "na_evidence_requires_null_value")
            elif criterion["type"] in {"boolean", "flag"}:
                if type(value) is not bool:
                    _reject(f"{path}/value", "boolean_required")
            elif type(value) not in (int, float) or value not in criterion["scale"]:
                _reject(f"{path}/value", "value_must_be_on_requested_scale")
