from copy import deepcopy

import pytest

from app.contracts import ContractError, validate_result_semantics
from app.scoring import score


def judgment(result, criterion_id):
    return next(item for item in result["criteria"] if item["criterion_id"] == criterion_id)


def test_allowed_na_excludes_weights_and_keeps_full_score(contracts, request_data, result_data):
    contracts.validate_result(request_data, result_data)
    summary = score(request_data, result_data)
    assert summary["status"] == "complete"
    assert summary["applicable_weight"] == 65
    assert summary["total"] == 100
    assert next(item for item in summary["criteria"] if item["criterion_id"] == "identity") == {
        "criterion_id": "identity", "points": None, "effective_weight": None,
    }


def test_sixth_criterion_uses_scale_max_and_weight(contracts, request_data, result_data):
    judgment(result_data, "dialogue")["value"] = 7
    contracts.validate_result(request_data, result_data)
    summary = score(request_data, result_data)
    assert summary["total"] == 93.08  # (10+20+15+15*7/10+5) / 65 * 100
    dialogue = next(item for item in summary["criteria"] if item["criterion_id"] == "dialogue")
    assert dialogue == {"criterion_id": "dialogue", "points": 16.15, "effective_weight": 23.08}
    communication = next(item for item in summary["sections"] if item["section_id"] == "communication")
    assert communication["score"] == 87.14  # (15+10.5+5)/(15+15+5)*100


def test_gross_error_flag_does_not_zero_total(contracts, request_data, result_data):
    judgment(result_data, "gross_errors")["value"] = True
    contracts.validate_result(request_data, result_data)
    summary = score(request_data, result_data)
    assert summary["total"] == 100
    assert summary["has_gross_error"] is True
    assert summary["criteria"][-1] == {"criterion_id": "gross_errors", "points": 0, "effective_weight": 0}
    assert summary["sections"][-1] == {"section_id": "violations", "score": None}


def test_pending_is_not_na_and_does_not_produce_total(contracts, request_data, result_data):
    item = judgment(result_data, "dialogue")
    item.update(status="needs_review", value=None, evidence=[{"kind": "uncertain", "message_ids": ["m0002"]}])
    contracts.validate_result(request_data, result_data)
    summary = score(request_data, result_data)
    assert summary["status"] == "needs_review"
    assert summary["total"] is None
    assert summary["applicable_weight"] == 50
    assert all(item["points"] is None for item in summary["criteria"][:-1])
    assert next(item for item in summary["sections"] if item["section_id"] == "communication")["score"] is None


def test_pending_flag_remains_unknown(contracts, request_data, result_data):
    judgment(result_data, "gross_errors").update(
        status="needs_review", value=None,
        evidence=[{"kind": "uncertain", "message_ids": ["m0002"]}],
    )
    contracts.validate_result(request_data, result_data)
    summary = score(request_data, result_data)
    assert summary["has_gross_error"] is None
    assert summary["total"] is None


def test_all_na_is_not_calculable(contracts, request_data, result_data):
    request_data["checklist"]["criteria"] = [criterion for criterion in request_data["checklist"]["criteria"] if criterion["id"] in {"needs", "identity"}]
    result_data["criteria"] = [item for item in result_data["criteria"] if item["criterion_id"] in {"needs", "identity"}]
    contracts.validate_result(request_data, result_data)
    summary = score(request_data, result_data)
    assert summary["status"] == "not_calculable"
    assert summary["total"] is None
    assert summary["applicable_weight"] == 0


def test_round_half_up_once_at_final_output(contracts, request_data, result_data):
    criterion = deepcopy(request_data["checklist"]["criteria"][3])
    criterion.update(weight=1, scale=[0, 2469, 20000])
    request_data["checklist"]["criteria"] = [criterion]
    result_data["criteria"] = [judgment(result_data, "consultation")]
    result_data["criteria"][0]["value"] = 2469
    contracts.validate_result(request_data, result_data)
    assert score(request_data, result_data)["total"] == 12.35


@pytest.mark.parametrize("criterion_id,value", [("contact", 1), ("gross_errors", 0), ("dialogue", True), ("dialogue", 6)])
def test_invalid_values_are_rejected(contracts, request_data, result_data, criterion_id, value):
    judgment(result_data, criterion_id)["value"] = value
    with pytest.raises(ContractError):
        contracts.validate_result(request_data, result_data)


def test_disallowed_na_is_rejected(contracts, request_data, result_data):
    item = judgment(result_data, "contact")
    item.update(value=None, evidence=[{"kind": "not_applicable", "message_ids": ["m0002"]}])
    with pytest.raises(ContractError, match="NA requires"):
        contracts.validate_result(request_data, result_data)


def test_na_requires_explicit_evidence(contracts, request_data, result_data):
    judgment(result_data, "needs")["evidence"][0]["kind"] = "message"
    with pytest.raises(ContractError):
        contracts.validate_result(request_data, result_data)
    with pytest.raises(ContractError, match="NA requires"):
        validate_result_semantics(request_data, result_data)


def test_unknown_evidence_is_rejected(contracts, request_data, result_data):
    judgment(result_data, "contact")["evidence"][0]["message_ids"] = ["missing-message"]
    with pytest.raises(ContractError, match="unknown message"):
        contracts.validate_result(request_data, result_data)


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "wrong_conversation", "wrong_version"])
def test_criterion_set_and_response_identity_must_match(contracts, request_data, result_data, mutation):
    if mutation == "missing":
        result_data["criteria"].pop()
    elif mutation == "extra":
        extra = deepcopy(result_data["criteria"][0])
        extra["criterion_id"] = "not-requested"
        result_data["criteria"].append(extra)
    elif mutation == "duplicate":
        result_data["criteria"].append(deepcopy(result_data["criteria"][0]))
    elif mutation == "wrong_conversation":
        result_data["conversation_id"] = "other-conversation"
    else:
        result_data["checklist_version"] += 1
    with pytest.raises(ContractError):
        contracts.validate_result(request_data, result_data)


def test_duplicate_input_message_ids_are_rejected(contracts, request_data):
    request_data["conversation"]["messages"].append(deepcopy(request_data["conversation"]["messages"][0]))
    with pytest.raises(ContractError, match="Duplicate message"):
        contracts.validate_request(request_data)


def test_pending_cannot_claim_na(contracts, request_data, result_data):
    judgment(result_data, "needs")["status"] = "needs_review"
    with pytest.raises(ContractError):
        contracts.validate_result(request_data, result_data)


@pytest.mark.parametrize("criterion_id", ["contact", "needs"])
def test_scored_and_na_cannot_include_uncertain_evidence(contracts, request_data, result_data, criterion_id):
    item = judgment(result_data, criterion_id)
    item["evidence"].append({"kind": "uncertain", "message_ids": ["m0002"]})
    with pytest.raises(ContractError):
        contracts.validate_result(request_data, result_data)
    with pytest.raises(ContractError, match="Scored judgments cannot contain uncertain"):
        validate_result_semantics(request_data, result_data)


@pytest.mark.parametrize("kinds", [
    ["message"], ["missing_action"], ["not_applicable"],
    ["uncertain", "message"], ["uncertain", "not_applicable"],
])
def test_pending_evidence_must_be_exclusively_uncertain(contracts, request_data, result_data, kinds):
    item = judgment(result_data, "contact")
    item.update(status="needs_review", value=None,
                evidence=[{"kind": kind, "message_ids": ["m0002"]} for kind in kinds])
    with pytest.raises(ContractError):
        contracts.validate_result(request_data, result_data)
    with pytest.raises(ContractError, match="only uncertain"):
        validate_result_semantics(request_data, result_data)


def test_na_cannot_mix_not_applicable_with_message_evidence(contracts, request_data, result_data):
    judgment(result_data, "needs")["evidence"].append({"kind": "message", "message_ids": ["m0002"]})
    with pytest.raises(ContractError):
        contracts.validate_result(request_data, result_data)
    with pytest.raises(ContractError, match="only not_applicable"):
        validate_result_semantics(request_data, result_data)


def test_nonfinite_number_is_rejected(contracts, request_data, result_data):
    judgment(result_data, "dialogue")["value"] = float("nan")
    with pytest.raises(ContractError, match="Non-finite"):
        contracts.validate_result(request_data, result_data)
