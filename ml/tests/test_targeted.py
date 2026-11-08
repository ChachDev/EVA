from copy import deepcopy
import pytest

from eva_ml.audit import COMPONENTS
from eva_ml.audit_policy import rules_for
from eva_ml.audit_trace import Trace
from eva_ml.targeted import RubricFollowup


def conversation(*lines, **source):
    return Trace({"id":"fixture","language":"EN","source":source,"messages":[
        {"id":str(i), "speaker":speaker, "text":text, "timestamp":None}
        for i,(speaker,text) in enumerate(lines,1)]})


def test_official_dialogue_rule_is_independent_of_first_pass_fact_table(evaluation_request):
    checklist = deepcopy(evaluation_request["checklist"])
    assert rules_for(checklist)["dialogue"]["official"]
    dialogue = next(c for c in checklist["criteria"] if c["id"] == "dialogue")
    dialogue["instruction"] = "A new meaning"
    assert not rules_for(checklist)["dialogue"]["official"]


def test_terminal_system_event_can_ground_closing_failure(evaluation_request):
    trace = conversation(("client","What are the fees?"),("operator","Use the fee schedule."),("system","Agent closed the chat."))
    rules = rules_for(evaluation_request["checklist"])
    plan = RubricFollowup.plan(trace,rules,{k:{"status":"needs_review","value":None} for k in rules},COMPONENTS)
    assert plan["closing"][0]["properties"]["context"]["enum"] == ["3"]
    item = {"finding":"missed_close","client":"","operator":"2","context":"3","why":"The agent closed without a farewell."}
    row, proofs = RubricFollowup.resolve("closing",item,trace)
    assert row["value"] is False and [p["utterance_id"] for p in proofs] == ["2","3"]
    trace.records["3"]["text"] = 'An example says "Agent closed the chat."'
    assert RubricFollowup.resolve("closing",item,trace) is None


@pytest.mark.parametrize("source", [{"complete_end":False}, {"gaps":True}])
def test_unreliable_source_or_disabled_score_gets_no_targeted_checks(evaluation_request,source):
    trace = conversation(("client","Can you help?"),("operator","Let me check."),**source)
    rules = rules_for(evaluation_request["checklist"])
    assert RubricFollowup.plan(trace,rules,{},COMPONENTS) == {}
    assert RubricFollowup.plan(trace,rules,{},COMPONENTS-{"score"}) == {}


def test_client_repetition_needs_a_real_intervening_operator_turn():
    trace = conversation(("client","Where do I pay?"),("operator","Use the portal."),
                         ("client","Where exactly in the portal?"))
    item = {"finding":"reask","client":"1","operator":"2","context":"3","why":"The client had to ask where."}
    row, proofs = RubricFollowup.resolve("dialogue",item,trace)
    assert row["value"] == 4 and [p["quote"] for p in proofs] == [m["text"] for m in trace.records.values()]
    for change in ({"operator":"3"}, {"context":"2"}, {"context":"999"}, {"context":""}):
        assert RubricFollowup.resolve("dialogue",{**item,**change},trace) is None


def test_personal_request_cannot_be_converted_into_general_na():
    trace = conversation(("client","What is my balance?"),("operator","I can explain the general procedure."))
    item = {"finding":"general","client":"1","operator":"2","context":"","why":"General information."}
    assert RubricFollowup.resolve("identity",item,trace) is None
    trace.records["1"]["text"] = "Where can I find the public fee schedule?"
    row, _ = RubricFollowup.resolve("identity",item,trace)
    assert row["status"] == "scored" and row["value"] is None
