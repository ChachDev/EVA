from copy import deepcopy

import pytest

from benchmarks.disclosure_trial import cases
from eva_ml.audit_trace import Trace
from eva_ml.consultation_v11 import select_provider
from eva_ml.disclosure_examples import trained_index
from eva_ml.disclosure_review import DisclosureReviewer


def facts(**overrides):
    return {"action": "own", "action_quote": "сорок два рубля", "greeting": "", "introduction": "Меня зовут Ирина",
            "farewell": "", "ending": "answered", "authority": "none", "other_risk": "no",
            "custom": {"monitor": "balance_only"}, **overrides}


@pytest.mark.parametrize("idx,expected", [(0, False), (1, True)])
def test_owner_before_and_after_keeping_broad_flag_separate(idx, expected):
    req, _ = list(cases())[idx]
    trace = Trace(req["conversation"])
    op = next(m for m in trace.records.values() if m["role"] == "operator")
    rows = DisclosureReviewer.decisions(facts(), trace, op, {"contact", "closing", "identity", "gross_errors"}, {"monitor": ""})
    assert rows["gross_errors"]["value"] is expected
    assert rows["identity"]["value"] is not expected
    assert rows["contact"]["value"] is False and rows["monitor"]["value"] is True
    assert rows["gross_errors"]["evidence"][0]["message_ids"] == ["m0002", "m0003"]


def test_refusal_does_not_clear_broader_flag_about_other_types_of_data():
    req, _ = list(cases())[3]
    trace = Trace(req["conversation"])
    op = list(trace.records.values())[-1]
    result = DisclosureReviewer.decisions(facts(action="no_value", action_quote=op["text"],
        custom={"monitor": "includes_balance"}), trace, op, {"gross_errors"}, {"monitor": ""})
    assert result["gross_errors"]["value"] is False
    assert "monitor" not in result


def test_third_party_report_preserves_request_and_does_not_extend_client_kyc():
    req, _ = list(cases())[2]
    trace = Trace(req["conversation"])
    op = list(trace.records.values())[-1]
    args = dict(action="third", action_quote="forty-one dollars", custom={})
    rows = DisclosureReviewer.decisions(facts(**args), trace, op, {"gross_errors", "identity"}, {})
    assert rows["gross_errors"]["evidence"][0]["message_ids"] == ["m0001", "m0002", "m0003"]
    assert rows["gross_errors"]["value"] and "identity" not in rows
    ambiguous = DisclosureReviewer.decisions(facts(**args, authority="possible_basis"), trace, op, {"gross_errors"}, {})
    assert ambiguous.get("gross_errors", {}).get("value") is not True


class NoCalls:
    model = "test-model"


def test_ineligible_sources_keep_primary_and_invalid_proof_cannot_change_it(monkeypatch):
    req, _ = next(cases())
    reviewer = select_provider(NoCalls(), "disclosure-review-v1")
    result = {"warnings": [], "criteria": []}
    partial = deepcopy(req)
    partial["conversation"]["source"]["complete_start"] = False
    assert reviewer.refine(partial, deepcopy(result)) == result
    monkeypatch.setattr(reviewer, "_generate", lambda *args: facts(greeting="Здравствуйте"))
    after = reviewer.refine(req, deepcopy(result))
    assert after["criteria"] == result["criteria"] and len(after["warnings"]) == 2


def test_examples_are_balanced_and_never_read_evaluation_data():
    examples = trained_index().examples_for("Her account has twelve euros")
    assert {r["action"] for r in examples} == {"own", "third", "no_value"}
    assert len(examples) == 3


@pytest.mark.parametrize("finding,expected", [("none", False), ("s1", True), ("unknown", None)])
def test_additional_types_need_their_own_evidence(monkeypatch, finding, expected):
    req, _ = list(cases())[3]
    req["checklist"]["criteria"][-1]["instruction"] = "Отметьте баланс или имя."
    if finding == "s1":
        req["conversation"]["messages"][-1]["text"] = "My name is Dana. I cannot share the account balance."
    reviewer = select_provider(NoCalls(), "disclosure-review-v1")
    monkeypatch.setattr(reviewer, "rule_scope", lambda *args: "includes_balance")
    def read(prompt, data, schema, name, limit):
        if name == "other_protected_data":
            return {"finding": finding, "type_quote": "имя"}
        parsed = facts(action="no_value", action_quote="s1", greeting="none", introduction="none", farewell="none")
        parsed.pop("custom")
        return parsed
    monkeypatch.setattr(reviewer, "read_facts", read)
    original = {"warnings": [], "criteria": [{"criterion_id": "monitor", "value": True, "status": "scored"}]}
    decision = reviewer.refine(req, original)["criteria"][0]
    assert decision["value"] is expected
    assert decision["status"] == ("needs_review" if finding == "unknown" else "scored")
