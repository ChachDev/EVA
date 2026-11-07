"""These tests validate event arbitration independently of the model emitting the events."""
from copy import deepcopy
import pytest
from benchmarks.audit_pairs import cases
from eva_ml.audit_events import identity_decision, violation_decision
from eva_ml.audit_policy import rules_for
from eva_ml.audit_trace import Trace


@pytest.mark.parametrize("index",range(18))
def test_freedom_event_pairs(index,evaluation_request):
    c,key,expected=list(cases())[index];t=Trace(c);r=rules_for(evaluation_request["checklist"])[key]
    operator=next(m["id"] for m in c["messages"] if m["speaker"]=="operator")
    if key=="identity":
        event=next(m["id"] for m in c["messages"] if m["speaker"]=="system")
        row=identity_decision(r,dict(apply="yes",action=operator,verified=event),t)
        assert row["evidence"][0]["message_ids"]==sorted([operator,event])
    else:
        row=violation_decision(r,dict(act="performed" if expected else "declined",operator_id=operator,why="Synthetic evidence."),t)
    assert row["value"] is expected and row["status"]=="scored"


def test_operator_claim_is_not_identity_confirmation(evaluation_request):
    c,key,_=next(cases());c["messages"][1]["speaker"]="client"
    r=rules_for(evaluation_request["checklist"])[key]
    assert identity_decision(r,dict(apply="yes",action="m0003",verified="m0002"),Trace(c))["status"]=="needs_review"
    assert identity_decision(r,dict(apply="yes",action="m0003",verified="none"),Trace(c))["value"] is False
    c["source"]={"complete_start":False}
    assert identity_decision(r,dict(apply="yes",action="m0003",verified="none"),Trace(c))["status"]=="needs_review"


@pytest.mark.parametrize("act",["declined","reported","unknown"])
def test_discussion_and_quoting_not_actions(act,evaluation_request):
    c,_,_=next(cases());r=rules_for(evaluation_request["checklist"])["gross_errors"]
    c["messages"][-1]["text"]="Ол «pay me personally» деді. Мен мұндай қызмет көрсете алмаймын."
    out=violation_decision(r,dict(act=act,operator_id="m0003",why="Reported conversation."),Trace(c))
    assert out["value"] is (None if act=="unknown" else False)
    out=violation_decision(r,dict(act="performed",operator_id="m0001",why="Misattributed."),Trace(c))
    assert out["status"]=="needs_review"
