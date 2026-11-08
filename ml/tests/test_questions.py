from copy import deepcopy
import pytest
import json
from benchmarks.question_trial import examples
from eva_ml.audit_trace import Trace
from eva_ml.questions import checked_maps
from eva_ml.questions import QuestionsAuditor,anchors
from benchmarks.audit_run import CHECK

@pytest.mark.parametrize("conv,gold",list(examples()))
def test_question_proof_validates_exact_source_and_order(conv,gold):
    trace=Trace(conv);text=conv["messages"][0]["text"]
    cut={"RU":" и ","EN":" and ","KZ":" және "}[conv["language"]]
    q1,q2=text.split(cut,1)
    a=conv["messages"][1]["text"].split(".")[0]
    full=gold["consultation"]==20
    result={"complete":True,"questions":[dict(client="m1",question=q1,status="answered",operator="m2",reply=a),
        dict(client="m1",question=q2,status="answered" if full else "open",operator="m2" if full else "",
            reply=conv["messages"][1]["text"][len(a)+2:] if full else "")]}
    pairs=checked_maps(trace,result)
    assert pairs[0]["answer"]["role"]=="operator"
    assert (pairs[1]["answer"] is not None)==full
    for mutation in ("quote","role","overlap","incomplete"):
        bad=deepcopy(result)
        if mutation=="quote":bad["questions"][0]["reply"]+="XYZ"
        if mutation=="role":bad["questions"][0].update(operator="m1",reply=q1)
        if mutation=="overlap":bad["questions"][1]["question"]=q1
        if mutation=="incomplete":bad["complete"]=False
        with pytest.raises(ValueError):checked_maps(trace,bad)
    conv=deepcopy(conv);conv["source"]["complete_end"]=False
    with pytest.raises(ValueError):checked_maps(Trace(conv),result)

@pytest.mark.parametrize("conv,gold",list(examples()))
def test_injected_maximum_is_checked_not_assumed(conv,gold):
    from types import SimpleNamespace
    q=anchors(conv["messages"][0]["text"],True)[:2]
    a=anchors(conv["messages"][1]["text"],False)
    full=gold["consultation"]==20
    raw={"complete":True,"why":"Synthetic fixture.","dialogue":{"finding":"none",
        **{k:{"id":"","quote":""} for k in ("first","response","repeat")}},
        "questions":[{"client":"m1","question":q[0],"operator":"m2","reply":a[0],"status":"answered"},
        {"client":"m1","question":q[1],"operator":"m2" if full else "","reply":a[1] if full else "",
         "status":"answered" if full else "open"}]}
    req={"schema_version":"1.0.0","conversation":conv,"checklist":CHECK}
    # This is an injected error to exercise the gate; it is not an observed prediction.
    base={"schema_version":"1.0.0","conversation_id":conv["id"],"checklist_id":CHECK["id"],"checklist_version":1,
          "provider":"fixture","model":"synthetic","warnings":[],"criteria":[{"criterion_id":k["id"],"status":"scored",
          "value":max(k["scale"]) if k["scale"] else k["type"]!="flag","reason":"synthetic",
          "evidence":[{"kind":"message","message_ids":["m2"]}]} for k in CHECK["criteria"]]}
    engine=SimpleNamespace(request=lambda *_:{"choices":[{"finish_reason":"stop","message":{"content":json.dumps(raw)}}]})
    auditor=QuestionsAuditor(SimpleNamespace(engine=engine,model="synthetic"),cache_size=0)
    bundle=auditor.evaluate(req,saved_baseline=base)
    assert bundle["audit"]["verification"]["baseline"]==base
    assert next(r for r in bundle["response"]["criteria"] if r["criterion_id"]=="consultation")["value"]==gold["consultation"]
    assert bundle["audit"]["question_map"]["applied"]==([] if full else ["consultation"])
    assert next(r for r in bundle["response"]["criteria"] if r["criterion_id"]=="dialogue")["value"]==10
    raw["questions"][0]["reply"]+="invented"
    invalid=auditor.evaluate(req,saved_baseline=base)
    assert invalid["response"]==base and invalid["audit"]["verification"]["state"]=="partial"
