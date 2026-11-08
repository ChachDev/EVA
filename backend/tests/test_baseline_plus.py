from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.audit import checked, fingerprint
from app.config import Settings
from app.main import create_app
from app.ml_client import MLInvalidResponse


def make_bundle(contracts,request):
    result=contracts.example("audit-bundle")
    audit=result["audit"]
    audit["versions"].update(profile="baseline-plus",model=result["response"]["model"],
                             transcript=fingerprint(request["conversation"]),checklist=fingerprint(request["checklist"]))
    audit["verification"]={"baseline":deepcopy(result["response"]),"state":"not_needed","requested":[],"checks":{}}
    return result


@pytest.mark.parametrize("profile", ["baseline-plus","baseline-plus-targeted"])
def test_primary_result_is_bound_and_unchecked_rows_cannot_be_rewritten(contracts,request_data,profile):
    bundle=make_bundle(contracts,request_data)
    bundle["audit"]["versions"]["profile"]=profile
    checked(contracts,request_data,bundle)
    changed=deepcopy(bundle)
    changed["response"]["criteria"][0]["reason"]="Replaced silently."
    with pytest.raises(MLInvalidResponse):checked(contracts,request_data,changed)
    bad=deepcopy(bundle);bad["audit"]["verification"]["baseline"]["checklist_version"]+=1
    with pytest.raises(MLInvalidResponse):checked(contracts,request_data,bad)


def test_amendment_needs_a_verified_source(contracts,request_data):
    bundle=make_bundle(contracts,request_data)
    first=bundle["response"]["criteria"][0]
    first["value"]=not first["value"]
    bundle["audit"]["verification"]["checks"][first["criterion_id"]]={"outcome":"amended","reason":"New fact."}
    with pytest.raises(MLInvalidResponse):checked(contracts,request_data,bundle)


def test_shadow_history_survives_switch_to_baseline(tmp_path, contracts, request_data):
    database = tmp_path / "shadow.sqlite"
    async def baseline(request):
        return make_bundle(contracts, request)["response"]
    async def verify(request, saved):
        bundle = make_bundle(contracts, request)
        assert saved == bundle["response"]
        return bundle
    body = {"conversation_id":request_data["conversation"]["id"], "checklist_id":"freedom-cqa", "checklist_version":1}
    app = create_app(Settings(database_path=database, seed_demo=True, audit_mode="shadow"), baseline, start_workers=False)
    with TestClient(app) as client:
        app.state.audit_shadow = verify
        record = client.post("/api/v1/evaluations", json=body).json()
        path = "/api/v1/evaluations/" + record["id"]
        appeal = client.post(path+"/appeals", json={"operation_id":str(uuid4()), "expected_revision":0,
            "criterion_id":"contact", "message_ids":[request_data["conversation"]["messages"][0]["id"]],
            "reason":"Check the original greeting."}).json()["appeals"][0]
        decision = {"operation_id":str(uuid4()), "expected_revision":1, "decision":"rejected",
                    "reason":"The greeting is present in the cited message."}
        route = "/api/v1/appeals/"+appeal["id"]+"/decisions"
        after = client.post(route, json=decision).json()
        assert client.post(route, json=decision).json() == after
        original = client.get(path+"/audit").json()
        assert original["audit"]["status"] == "completed" and original["review"]["original"] == record
    rollback = create_app(Settings(database_path=database, audit_mode="baseline"), baseline, start_workers=False)
    with TestClient(rollback) as client:
        assert client.get(path).json() == record
        restored = client.get(path+"/audit").json()
        assert restored["audit"] == original["audit"] and restored["history"] == original["history"]
        assert len(restored["history"]) == 1
        new = client.post("/api/v1/evaluations", json=body).json()
        assert client.get("/api/v1/evaluations/"+new["id"]+"/audit").json()["audit"] is None
