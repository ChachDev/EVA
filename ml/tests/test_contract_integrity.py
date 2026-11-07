import copy
import json

import pytest
from referencing.exceptions import Unresolvable

from eva_ml.contracts import ContractError, Contracts
from eva_ml.stub import evaluate


@pytest.mark.parametrize(
    ("field", "value"),
    [("conversation_id", "wrong"), ("checklist_id", "wrong"), ("checklist_version", 999)],
)
def test_response_must_belong_to_request(contracts, evaluation_request, field, value):
    response = evaluate(evaluation_request)
    response[field] = value
    with pytest.raises(ContractError):
        contracts.validate_response(evaluation_request, response)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown"])
def test_results_cover_exactly_requested_criteria(contracts, evaluation_request, mutation):
    response = evaluate(evaluation_request)
    if mutation == "missing":
        response["criteria"].pop()
    elif mutation == "duplicate":
        response["criteria"].append(copy.deepcopy(response["criteria"][0]))
    else:
        response["criteria"][0]["criterion_id"] = "unknown-criterion"
    with pytest.raises(ContractError):
        contracts.validate_response(evaluation_request, response)


def test_evidence_must_reference_real_messages(contracts, evaluation_request):
    response = evaluate(evaluation_request)
    response["criteria"][0]["evidence"][0]["message_ids"] = ["hallucinated-id"]
    with pytest.raises(ContractError):
        contracts.validate_response(evaluation_request, response)


@pytest.mark.parametrize("value", [True, False, 0, 20])
def test_uncertain_results_cannot_contain_scores(contracts, evaluation_request, value):
    response = evaluate(evaluation_request)
    response["criteria"][0]["value"] = value
    with pytest.raises(ContractError):
        contracts.validate_response(evaluation_request, response)


def test_not_applicable_is_distinct_from_uncertainty(contracts, evaluation_request):
    response = evaluate(evaluation_request)
    response["criteria"][0]["evidence"][0]["kind"] = "not_applicable"
    with pytest.raises(ContractError):
        contracts.validate_response(evaluation_request, response)


def test_na_requires_checklist_permission(contracts, evaluation_request):
    response = evaluate(evaluation_request)
    result = response["criteria"][0]
    result["status"] = "scored"
    result["evidence"][0]["kind"] = "not_applicable"
    with pytest.raises(ContractError):
        contracts.validate_response(evaluation_request, response)
    evaluation_request["checklist"]["criteria"][0]["allow_na"] = True
    evaluation_request["checklist"]["criteria"][0]["na_condition"] = "Синтетическое условие N/A."
    contracts.validate_response(evaluation_request, response)


def test_boolean_result_rejects_numeric_score(contracts, evaluation_request):
    response = evaluate(evaluation_request)
    result = response["criteria"][0]
    result.update(status="scored", value=1)
    result["evidence"][0]["kind"] = "message"
    with pytest.raises(ContractError):
        contracts.validate_response(evaluation_request, response)
    result["value"] = True
    contracts.validate_response(evaluation_request, response)


def test_scale_result_rejects_bool_and_off_scale_score(contracts, evaluation_request):
    criterion = evaluation_request["checklist"]["criteria"][0]
    criterion.update(type="scale", scale=[0, 5, 10])
    response = evaluate(evaluation_request)
    result = response["criteria"][0]
    result["status"] = "scored"
    result["evidence"][0]["kind"] = "message"
    for invalid in (True, 7):
        result["value"] = invalid
        with pytest.raises(ContractError):
            contracts.validate_response(evaluation_request, response)
    result["value"] = 5
    contracts.validate_response(evaluation_request, response)


def test_schema_registry_does_not_resolve_external_urls(tmp_path):
    for kind in ("request", "response"):
        name = f"evaluation-{kind}.schema.json"
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"https://eva.local/schemas/{name}",
            "$ref": "https://example.invalid/remote.schema.json",
        }
        (tmp_path / name).write_text(json.dumps(schema), encoding="utf-8")
    contracts = Contracts(tmp_path)
    with pytest.raises(Unresolvable):
        contracts.validate_request({})
