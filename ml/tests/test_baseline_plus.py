"""Check the verifier's authority separately from the language model's judgements."""
from copy import deepcopy
import pytest

from eva_ml.audit import COMPONENTS
from eva_ml.audit_events import select_auditor
from eva_ml.baseline_plus import BaselinePlusAuditor
from eva_ml.engine import EngineError
from eva_ml.issai import digest
from benchmarks.audit_pairs import cases


class Base:
    model="test-model"
    name="synthetic-test"
    engine=object()
    def __init__(self):
        self.requests=[]
    def evaluate(self,req):
        self.requests.append(deepcopy(req))
        ids=[m["id"] for m in req["conversation"]["messages"]]
        rows=[]
        for k in req["checklist"]["criteria"]:
            if k["type"]=="scale": value=max(k["scale"])
            else: value=(k["type"]=="boolean")
            rows.append({"criterion_id":k["id"],"status":"scored","value":value,"reason":"Synthetic decision.",
                         "evidence":[{"kind":"message","message_ids":[ids[0]]}]})
        return {"schema_version":"1.0.0","conversation_id":req["conversation"]["id"],"checklist_id":req["checklist"]["id"],
                "checklist_version":req["checklist"]["version"],"provider":self.name,"model":self.model,"criteria":rows,"warnings":[]}


def setup(req,case,components=COMPONENTS):
    base=Base()
    conv,goal,_=list(cases())[case]
    req={**req,"conversation":conv}
    verifier=select_auditor(base,components,"baseline-plus",cache_size=0)
    return req,base,verifier


def test_original_full_request_and_only_witnessed_identity_changes(evaluation_request):
    req,base,verifier=setup(evaluation_request,1)
    bundle=verifier.evaluate(req)
    previous=bundle["audit"]["verification"]["baseline"]
    assert base.requests==[req]
    for before,after in zip(previous["criteria"],bundle["response"]["criteria"]):
        if after["criterion_id"]=="identity":
            assert before["value"] is True and after["value"] is False
            assert after["evidence"][0]["message_ids"]==["m0002","m0003"]
        else: assert after==before
    assert bundle["audit"]["verification"]["state"]=="not_needed"


def test_secondary_failure_preserves_full_result_and_is_not_cached(evaluation_request,monkeypatch):
    req,base,verifier=setup(evaluation_request,2)
    verifier.cache_size=2
    def failed(*_):
        raise EngineError("Synthetic extra check unavailable.")
    monkeypatch.setattr(verifier,"_second_read",failed)
    for _ in range(2):
        bundle=verifier.evaluate(req)
        assert bundle["response"]==bundle["audit"]["verification"]["baseline"]
        assert bundle["audit"]["verification"]["state"]=="unavailable"
        assert not bundle["audit"]["cache_hit"]
    assert len(base.requests)==2


def test_shadow_endpoint_validates_saved_input(client,evaluation_request):
    req,base,verifier=setup(evaluation_request,1)
    client.app.state.auditor=verifier
    primary=base.evaluate(req)
    body={"request":req,"baseline":primary,"request_hash":digest(req)}
    response=client.post("/v1/verify",json=body)
    assert response.status_code==200 and len(base.requests)==1
    assert response.json()["audit"]["verification"]["baseline"]==primary
    assert client.post("/v1/verify",json={**body,"request_hash":"wrong"}).status_code==422
    assert client.post("/v1/verify",json={**body,"baseline":{**primary,"model":"other"}}).status_code==422


def test_primary_failure_is_not_fabricated(evaluation_request,monkeypatch):
    req,base,verifier=setup(evaluation_request,1)
    def failed(*_):raise EngineError()
    monkeypatch.setattr(base,"evaluate",failed)
    with pytest.raises(EngineError):verifier.evaluate(req)


def test_saved_base_is_not_rerun_and_cache_is_bound_to_it(evaluation_request):
    req,base,verifier=setup(evaluation_request,1)
    original=base.evaluate(req)
    a=verifier.evaluate(req,saved_baseline=original)
    edited=deepcopy(original);edited["warnings"].append("A different baseline run")
    b=verifier.evaluate(req,saved_baseline=edited)
    assert len(base.requests)==1 and a["audit"]["cache_key"]!=b["audit"]["cache_key"]
    assert b["audit"]["verification"]["baseline"]==edited and b["audit"]["stage_usage"]["baseline"]["calls"]==0


def test_disabled_profile_and_modified_policy_preserve_original(evaluation_request):
    req,base,verifier=setup(evaluation_request,1,set())
    bundle=verifier.evaluate(req)
    assert bundle["audit"]["verification"]["checks"]=={}
    assert bundle["audit"]["verification"]["baseline"]==bundle["response"]
    rule=next(r for r in req["checklist"]["criteria"] if r["id"]=="identity")
    rule["instruction"]="Verify a different action."
    assert "identity" not in BaselinePlusAuditor(base).evaluate(req)["audit"]["verification"]["checks"]


@pytest.mark.parametrize("broken",["wrong_author","bad_quote","conflict"])
def test_an_uncorroborated_accusation_is_only_advisory(evaluation_request,monkeypatch,broken):
    req,base,verifier=setup(evaluation_request,3)  # client offers; operator refuses
    cid,oid=[m["id"] for m in req["conversation"]["messages"]]
    text=req["conversation"]["messages"][1]["text"]
    def read(*_):
        out={k:{"act":"absent","operator_id":oid,"quote":"","why":"No such action."}
             for k in ("disclosure","insult","private_service")}
        out["private_service"].update(act="performed",operator_id=cid if broken=="wrong_author" else oid,
            quote="not in text" if broken=="bad_quote" else text)
        return out
    monkeypatch.setattr(verifier,"_second_read",read)
    bundle=verifier.evaluate(req)
    assert bundle["response"]==bundle["audit"]["verification"]["baseline"]
    assert not bundle["audit"]["sentinel"]


def test_only_corroborated_clarification_can_replace_na(evaluation_request,monkeypatch):
    req,base,verifier=setup(evaluation_request,1)
    msgs=req["conversation"]["messages"]
    for m,(speaker,text) in zip(msgs,[("operator","Which currency?"),("client","Tenge."),("operator","Use the KZT form.")]):
        m.update(speaker=speaker,text=text)
    original=base.evaluate(req)
    needs=next(r for r in original["criteria"] if r["criterion_id"]=="needs")
    needs.update(value=None,evidence=[{"kind":"not_applicable","message_ids":["m0001"]}])
    def read(*_):
        return {"needs":{"label":"clarified","first":{"id":"m0001","quote":"Which currency?"},
                "second":{"id":"m0002","quote":"Tenge."},"why":"Currency was necessary to answer."}}
    monkeypatch.setattr(verifier,"_second_read",read)
    out=verifier.evaluate(req,saved_baseline=original)
    row=next(r for r in out["response"]["criteria"] if r["criterion_id"]=="needs")
    assert row["value"] is True and row["evidence"][0]["message_ids"]==["m0001","m0002"]
    assert out["audit"]["verification"]["baseline"]["criteria"]==original["criteria"]


def test_bot_privacy_notice_does_not_trigger_a_model_call(evaluation_request, monkeypatch):
    req,base,verifier=setup(evaluation_request,0)
    req["conversation"]["messages"][1].update(speaker="bot",text="Do not share personal information.")
    monkeypatch.setattr(verifier,"_second_read",lambda *_:pytest.fail("Unnecessary model call"))
    bundle=verifier.evaluate(req)
    assert bundle["audit"]["verification"]["requested"]==[]


def test_corroborated_refusal_adds_operator_evidence_without_losing_clients_message(evaluation_request,monkeypatch):
    req,base,verifier=setup(evaluation_request,3)
    operator=req["conversation"]["messages"][1]
    def refusals(*_):
        return {key:{"act":"declined" if key=="private_service" else "absent","operator_id":operator["id"],
                     "quote":operator["text"],"why":"The operator refused."} for key in ("insult","disclosure","private_service")}
    monkeypatch.setattr(verifier,"_second_read",refusals)
    bundle=verifier.evaluate(req)
    row=next(r for r in bundle["response"]["criteria"] if r["criterion_id"]=="gross_errors")
    assert row["value"] is False and row["evidence"][0]["message_ids"]==["m0001","m0002"]
    assert bundle["audit"]["verification"]["checks"]["gross_errors"]["outcome"]=="corroborated"


def test_quoted_example_cannot_create_a_subtype_even_if_baseline_has_gross_error(evaluation_request,monkeypatch):
    req,base,verifier=setup(evaluation_request,2)
    op=req["conversation"]["messages"][1]
    op["text"]="Предлагаю платную консультацию лично, в обход компании — так говорить запрещено."
    original=base.evaluate(req)
    next(c for c in original["criteria"] if c["criterion_id"]=="gross_errors")["value"]=True
    def read(*_):
        out={k:{"act":"absent","operator_id":op["id"],"quote":"","why":"No act."}
             for k in ("insult","private_service","disclosure")}
        out["private_service"].update(act="performed",quote=op["text"])
        return out
    monkeypatch.setattr(verifier,"_second_read",read)
    result=verifier.evaluate(req,saved_baseline=original)
    assert result["response"]==original and result["audit"]["sentinel"]==[]
