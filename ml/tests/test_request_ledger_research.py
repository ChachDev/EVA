from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from benchmarks.human_ballots import reconcile
from benchmarks.ledger_cases import synthetic
from eva_ml.audit_policy import rules_for
from eva_ml.audit_trace import Trace
from eva_ml.ledger_calibration import examples_for
from eva_ml.request_ledger import decision
from eva_ml.ledger_knowledge import applicable
from eva_ml.issai import digest
from eva_ml.criterion_reader import CriterionReader
from eva_ml.contracts import Contracts
from eva_ml.engine import EngineError


def test_two_goals_in_one_message_can_support_different_states():
    req, _, _ = list(synthetic())[1]
    trace = Trace(req["conversation"])
    answer = trace.records["m0002"]["text"]
    raw = {"complete":True, "requests":[
        {"client_id":"m0001","client_quote":"открыть копию квитанции","importance":"essential",
         "state":"closed","reply_id":"m0002","reply_quote":answer},
        {"client_id":"m0001","client_quote":"изменить адрес доставки","importance":"essential",
         "state":"open","reply_id":"","reply_quote":""}]}
    row = decision(raw,trace)
    assert row["value"] == 10
    assert row["evidence"][0] == {"kind":"missing_action","message_ids":["m0001","m0002","m0003"]}
    duplicated = deepcopy(raw)
    duplicated["requests"][1]["client_quote"] = "копию квитанции"
    with pytest.raises(ValueError):
        decision(duplicated,trace)
    for role in ("bot","client"):
        chat = deepcopy(req["conversation"])
        chat["messages"][1]["speaker"] = role
        with pytest.raises(ValueError):
            decision(raw,Trace(chat))


def test_unverified_external_truth_or_missing_source_never_issues_a_new_grade():
    req,_,_ = next(synthetic())
    trace = Trace(req["conversation"])
    raw = {"complete":True,"requests":[{"client_id":"m0001","client_quote":"квитанцию",
            "importance":"essential","state":"unknown","reply_id":"","reply_quote":""}]}
    with pytest.raises(ValueError):
        decision(raw,trace)
    trace.gaps = True
    with pytest.raises(ValueError):
        decision(raw,trace)


def test_training_family_and_changed_rubric_are_excluded():
    req,_,_ = next(synthetic())
    key=rules_for(req["checklist"])["consultation"]["hash"]
    cid=req["conversation"]["id"]
    content={"rubric_hash":key,"fragment":"Учебный фрагмент","value":10,"reason":"Существенная просьба открыта"}
    catalog={"chats":{cid:{"family":"A"},"dev-1":{"family":"A"},"dev-2":{"family":"B"}},
             "examples":[dict(content,id="translated",source_chat="dev-1"),dict(content,id="eligible",source_chat="dev-2")]}
    assert [item["id"] for item in examples_for(catalog,req)]==["eligible"]
    changed=deepcopy(req)
    changed["checklist"]["criteria"][3]["instruction"]="Другое правило"
    assert not examples_for(catalog,changed)


def test_ballot_excludes_disputes_without_a_third_and_requires_evidence():
    source=[{"conversation_id":"c","transcript":[{"turn":1}],"annotation":[
        {"criterion_id":"consultation","human_status":"unlabeled","allowed":[0,5,10,15,20,None]}]}]
    row={"chat":"c","criterion":"consultation","status":"scored","value":20,"reason":"Проверено","evidence_ids":["m0001"]}
    other=dict(row,value=10)
    assert reconcile(source,[row],[other],[])["excluded"]==1
    assert reconcile(source,[row],[other],[other])["gold"][0]["value"]==10
    with pytest.raises(ValueError):
        reconcile(source,[row],[dict(other,evidence_ids=["bad"])],[])


def test_product_scope_requires_same_snapshot_product_date_and_approval():
    req,_,_=next(synthetic())
    conv=req["conversation"]
    scope=dict(transcript_sha256=digest(conv),entity="lab",product="widget",jurisdiction="test",
               approved_by="fixture",date_evidence="synthetic-clock",conversation_date="2026-11-08")
    fact=dict(id="demo",entity="lab",product="widget",jurisdiction="test",review="expert_approved",
              approved_by="fixture",source="fixture",locator="§1",period_evidence="fixture",text="Demo only",
              **{"from":"2026-11-01","to":"2026-11-30"})
    catalog={"contexts":{conv["id"]:scope},"facts":[fact]}
    assert applicable(catalog,req)==[fact]
    for patch in ({"product":"another"},{"review":"draft"},{"to":"2026-11-07"},{"period_evidence":""}):
        assert applicable({**catalog,"facts":[{**fact,**patch}]},req)==[]
    updated=deepcopy(req)
    updated["conversation"]["messages"][0]["text"]+="!"
    assert not applicable(catalog,updated)


def test_one_broken_reader_call_preserves_the_other_seven_decisions():
    req,_,_=next(synthetic())
    class Worker:
        def request(self, route, payload):
            criterion=json.loads(payload["messages"][1]["content"])["criterion"]
            if criterion["id"]=="needs":
                raise EngineError("synthetic failure")
            label=str(max(criterion["scale"])) if criterion["type"]=="scale" else "no"
            out={"basis":"Правило проверено в данном тесте.","ids":["m0002"],"kind":"observed","label":label}
            return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps(out)}}]}
    model=SimpleNamespace(engine=Worker(),name="mock-test",model="mock-test")
    result=CriterionReader(model).evaluate(req)
    Contracts().validate_response(req,result)
    assert [row["criterion_id"] for row in result["criteria"] if row["status"]=="needs_review"]==["needs"]
