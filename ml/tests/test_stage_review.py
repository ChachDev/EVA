from copy import deepcopy
from types import SimpleNamespace

import pytest

from eva_ml.engine import EngineError
from eva_ml.stage_review import StageReview, opportunity, rubric_key


def scenario(template, lines, criterion="closing"):
    req = deepcopy(template)
    rule = next(c for c in req["checklist"]["criteria"] if c["id"]==criterion)
    req["checklist"]["criteria"]=[rule]
    req["conversation"]["messages"]=[{"speaker":role,"id":f"m{i}","text":text,"timestamp":"00:00","offset_seconds":0}
                                     for i,(role,text) in enumerate(lines,1)]
    row={"criterion_id":criterion,"status":"scored","value":None if rule["allow_na"] else 10,
         "reason":"Исходное решение","evidence":[{"kind":"not_applicable" if rule["allow_na"] else "message","message_ids":["m1"]}]}
    base={"schema_version":"1.0.0","conversation_id":req["conversation"]["id"],
          "checklist_id":req["checklist"]["id"],"checklist_version":req["checklist"]["version"],
          "provider":"fixture","model":"fixture","warnings":[],"criteria":[row]}
    return req,base


@pytest.mark.parametrize("event",["Оператор закрыл чат.","Agent closed the chat.","Оператор чатты жапты."])
def test_closure_requires_real_terminal_system_event(evaluation_request,event):
    lines=[("client","Can you help?"),("operator","Open Settings."),("system",event)]
    req,base=scenario(evaluation_request,lines)
    model=SimpleNamespace(model="fixture",name="fixture",_generate=lambda *_:{
        "closing":{"finding":"missing","refs":["u2.1","u3.1"],"brief":"Чат закрыт без прощания."}})
    review=StageReview(model)
    changed,calls=review.refine(req,base)
    assert calls==1 and changed["criteria"][0]["value"] is False and base["criteria"][0]["value"] is None
    assert changed["criteria"][0]["evidence"][0]["message_ids"]==["m2","m3"]
    req["conversation"]["messages"][-1]["speaker"]="client"
    assert review.refine(req,base)==(base,0)
    req["conversation"]["messages"][-1]["speaker"]="system"
    req["conversation"]["messages"][-1]["text"]="Quoted: "+event
    assert not opportunity(req["conversation"]["messages"])


def test_repeat_requires_client_operator_client_with_source_refs(evaluation_request):
    req,base=scenario(evaluation_request,[("client","Which menu?"),("operator","That one."),("client","Which menu exactly?")],"dialogue")
    answer={"finding":"repeat","refs":["u1.1","u2.1","u3.1"],"brief":"Клиент повторил вопрос после неясного ответа."}
    model=SimpleNamespace(model="fixture",name="fixture",_generate=lambda *_:{"dialogue":answer})
    review=StageReview(model)
    assert review.refine(req,base)[0]["criteria"][0]["value"]==4
    for refs in (["u3.1","u2.1","u1.1"],["u1.1","u1.1","u3.1"],["invented","u2.1","u3.1"]):
        answer["refs"]=refs
        assert review.refine(req,base)[0]["criteria"]==base["criteria"]
    req["checklist"]["criteria"][0]["instruction"]="Оцените только вежливость."
    assert rubric_key(req["checklist"]["criteria"][0]) is None
    assert review.refine(req,base)==(base,0)


def test_secondary_failure_preserves_primary(evaluation_request):
    req,base=scenario(evaluation_request,[("client","A?"),("operator","B."),("system","Agent closed the chat.")])
    def fail(*_):raise EngineError("test outage")
    review=StageReview(SimpleNamespace(name="fixture",model="fixture",_generate=fail))
    result,calls=review.refine(req,base)
    assert result["criteria"]==base["criteria"] and calls==1 and result["warnings"]
