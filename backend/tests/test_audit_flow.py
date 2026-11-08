"""Audit persistence and compatibility across configuration changes."""
from copy import deepcopy
from uuid import uuid4
import hashlib
import json
import pytest
from fastapi.testclient import TestClient
from app.audit import checked, delta
from app.config import Settings
from app.main import create_app
from app.ml_client import MLInvalidResponse, MLUnavailable


def fp(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()


@pytest.fixture
def run_data(request_data,contracts):
    fixture=contracts.example("audit-bundle")
    async def experimental(request):
        rows=[r for r in fixture["response"]["criteria"] if r["criterion_id"] in {x["id"] for x in request["checklist"]["criteria"]}]
        audit=deepcopy(fixture["audit"])
        audit["versions"].update(transcript=fp(request["conversation"]),checklist=fp(request["checklist"]),model=fixture["response"]["model"])
        return {"audit":audit,"response":{**fixture["response"],"criteria":rows,"conversation_id":request["conversation"]["id"],
                "checklist_id":request["checklist"]["id"],"checklist_version":request["checklist"]["version"]}}
    async def baseline(request): return (await experimental(request))["response"]
    return experimental,baseline


def test_reweight_selective_recheck_and_rollback_keep_original(tmp_path,run_data,request_data):
    exp,base=run_data
    path=tmp_path/"state.sqlite"
    cfg=Settings(database_path=path,seed_demo=True,audit_mode="experiment")
    app=create_app(cfg,base,start_workers=False)
    calls=[]
    async def spy(req):
        calls.append([c["id"] for c in req["checklist"]["criteria"]])
        return await exp(req)
    body={"conversation_id":request_data["conversation"]["id"],"checklist_id":"freedom-cqa","checklist_version":1}
    with TestClient(app) as cli:
        app.state.audit_evaluate=spy
        response=cli.post("/api/v1/evaluations",json=body)
        assert response.status_code==201
        original=response.json()
        path_api="/api/v1/evaluations/"+original["id"]
        criteria=deepcopy(request_data["checklist"]["criteria"])
        criteria[0]["weight"],criteria[1]["weight"]=criteria[1]["weight"],criteria[0]["weight"]
        def publish(version):
            res=cli.post("/api/v1/checklists/freedom-cqa/versions",json={"base_version":version,"name":"Test","criteria":criteria})
            assert res.status_code==201
        publish(1)
        run2=cli.post(path_api+"/versions/2",json={}).json()
        assert run2["original"]["criteria"]==original["original"]["criteria"] and len(calls)==1
        assert cli.post(path_api+"/versions/2",json={}).json()==run2
        criteria[0]["instruction"]="New meaning";criteria[1]["depends_on"]=["contact"]
        publish(2)
        modified=cli.post(path_api+"/versions/3",json={})
        assert modified.status_code==201 and calls[-1]==["contact","needs"]
        appeal=cli.post(path_api+"/appeals",json={"operation_id":str(uuid4()),"expected_revision":0,"criterion_id":"contact",
            "reason":"Human review","message_ids":[request_data["conversation"]["messages"][0]["id"]]}).json()["appeals"][0]
        decision={"operation_id":str(uuid4()),"expected_revision":1,"decision":"rejected","reason":"Reviewed source evidence."}
        route="/api/v1/appeals/"+appeal["id"]+"/decisions"
        after=cli.post(route,json=decision).json()
        assert cli.post(route,json=decision).json()==after and len(after["decisions"])==1
        after_review=cli.post(path_api+"/versions/2",json={}).json()
        assert after_review["id"]!=run2["id"] and cli.get("/api/v1/evaluations/"+run2["id"]).json()==run2
        assert cli.get("/api/v1/evaluations/"+after_review["id"]+"/audit").json()["audit"]["review_projection"]["parent_revision"]==2
    rollback=create_app(Settings(database_path=path,audit_mode="baseline"),base,start_workers=False)
    with TestClient(rollback) as cli:
        assert cli.get(path_api).json()==original
        export=cli.get(path_api+"/audit").json()
        assert export["review"]==after and export["audit"]["mode"]=="experiment"
        assert len(export["history"])==1 and export["history"][0]["after"]==export["history"][0]["before"]
        assert export["history"][0]["source"]=="reviewer"
        assert cli.post("/api/v1/evaluations",json=body).status_code==201
        assert cli.post(path_api+"/versions/3",json={}).status_code==409


def test_failed_shadow_preserves_baseline(tmp_path,run_data,request_data):
    _,base=run_data
    app=create_app(Settings(database_path=tmp_path/"shadow.sqlite",seed_demo=True,audit_mode="shadow"),base,start_workers=False)
    async def unavailable(_req,_base): raise MLUnavailable()
    with TestClient(app) as cli:
        app.state.audit_shadow=unavailable
        rec=cli.post("/api/v1/evaluations",json={"conversation_id":request_data["conversation"]["id"],"checklist_id":"freedom-cqa",
                                                "checklist_version":1}).json()
        detail=cli.get("/api/v1/evaluations/"+rec["id"]+"/audit").json()
        assert detail["audit"]["status"]=="failed" and detail["review"]["original"]==rec


def test_source_role_or_offset_mismatch_rejected(request_data,contracts):
    bundle=contracts.example("audit-bundle"); m=request_data["conversation"]["messages"][0]
    proof={"utterance_id":m["id"],"role":m["speaker"],"speaker_id":None,"order":0,"timestamp":m["timestamp"],
           "start":m["offset_seconds"],"end":None,"chars":[0,1],"quote":m["text"][0]}
    detail={"decision":True,"status":"scored","action":"Synthetic","applicability":"applicable","evidence":[proof],
            "technical":"valid","semantic":"model_assessed","uncertainty":None,"source":"model","scope":{}}
    bundle["audit"]["criteria"]={"contact":detail}
    checked(contracts,request_data,bundle)
    for field,value in [("quote","invented"),("role","unknown"),("start",999),("chars",[1,2])]:
        invalid=deepcopy(bundle);invalid["audit"]["criteria"]["contact"]["evidence"][0][field]=value
        with pytest.raises(MLInvalidResponse):checked(contracts,request_data,invalid)


def test_transitive_dependencies_and_removed_prerequisite():
    old={"criteria":[{"id":"a","instruction":"old"},{"id":"b","depends_on":["a"]},{"id":"c","depends_on":["b"]}]}
    new=deepcopy(old);new["criteria"][0]["instruction"]="new"
    assert all(d["evaluate"] for d in delta(old,new).values())
