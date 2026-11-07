from copy import deepcopy
import pytest
from benchmarks.audit_pairs import cases
from eva_ml.audit_events import identity_decision
from eva_ml.audit_policy import rules_for
from eva_ml.audit_trace import Trace
from eva_ml.banking_events import grounded_order


@pytest.mark.parametrize("c,key,value",[x for x in cases() if x[1]=="identity"])
def test_source_bound_chronology(c,key,value,evaluation_request):
    t=Trace(c); facts=grounded_order(t)
    assert facts is not None
    row=identity_decision(rules_for(evaluation_request["checklist"])[key],facts,t)
    assert row["value"] is value and len(row["evidence"][0]["message_ids"])==2


@pytest.mark.parametrize("text",["Your balance is 100 tenge?","They said: ‘Your balance is 100 tenge.’",
                                 "If your balance is 100 tenge, call us.","Your balance is not 100 tenge."])
def test_question_quote_hypothesis_and_negation_do_not_declare_balance(text):
    c=deepcopy(next(cases())[0]); c["messages"][-1]["text"]=text
    assert grounded_order(Trace(c)) is None


def test_asserted_verification_must_have_system_role():
    c=next(cases())[0];c["messages"][1]["speaker"]="client"
    assert grounded_order(Trace(c)) is None
    c["messages"][1].update(speaker="system",text="KYC event 718",event="identity_verified")
    assert grounded_order(Trace(c))["verified"]=="m0002"
    c["source"]={"gaps":True}
    assert grounded_order(Trace(c)) is None
