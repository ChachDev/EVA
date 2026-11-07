from contextlib import nullcontext
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from eva_ml import app as service
from eva_ml.engine import EngineError


class Inference:
    model = "test-model"

    def __init__(self, result):
        self.result = result

    def request(self, route, payload):
        if payload["max_tokens"] == 1:
            return {"choices":[{"finish_reason":"length"}]}
        assert route == "/v1/chat/completions"
        assert payload["cache_prompt"] is False
        assert set(payload["response_format"]["json_schema"]["schema"]["properties"]) == set(self.result)
        return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps(self.result)}}]}


def evaluate(monkeypatch, request, engine):
    monkeypatch.setenv("EVA_ML_PROVIDER","issai")
    monkeypatch.setattr(service,"loaded_engine",lambda: nullcontext(engine))
    with TestClient(service.app) as client:
        assert client.get("/health").json()["model"] == engine.model
        return client.post("/v1/evaluate",json=request)


def test_facts_and_replacement_on_http_boundary(monkeypatch, evaluation_request):
    request = deepcopy(evaluation_request)
    criteria = request["checklist"]["criteria"]
    contact = next(c for c in criteria if c["id"] == "contact")
    needs = next(c for c in criteria if c["id"] == "needs")
    edited = {**contact, "id":"changed", "instruction":"Проверяется новое условие",
              "type":"scale", "scale":[0,0.5,1]}
    request["checklist"]["criteria"] = [contact, needs, edited]
    evidence = [request["conversation"]["messages"][0]["id"]]
    rows = {"contact":{"facts":["yes","no"],"evidence":evidence},
            "needs":{"facts":["no","no","unknown"],"evidence":evidence},
            "changed":{"value":"0.5","kind":"message","reason":"Часть требования выполнена.","evidence":evidence}}
    r = evaluate(monkeypatch,request,Inference(rows))
    assert r.status_code == 200
    outcomes = r.json()["criteria"]
    assert [(row["value"], row["evidence"][0]["kind"]) for row in outcomes] == [
        (False,"missing_action"), (None,"not_applicable"), (0.5,"message")]
    assert outcomes[1]["reason"] == needs["na_condition"]


@pytest.mark.parametrize("failure,expected", [("wrong_evidence",500),("offline",503)])
def test_provider_failure_never_returns_a_score(monkeypatch, evaluation_request, failure, expected):
    request = deepcopy(evaluation_request)
    request["checklist"]["criteria"] = [next(c for c in request["checklist"]["criteria"] if c["id"] == "contact")]

    class BrokenEngine(Inference):
        def request(self, *args):
            if args[1]["max_tokens"] == 1:
                return {"choices":[{"finish_reason":"length"}]}
            if failure == "offline":
                raise EngineError("offline")
            return {"choices":[{"finish_reason":"stop","message":{"content":'{"contact":{"evidence":["invented"],"facts":["yes","yes"]}}'}}]}

    response = evaluate(monkeypatch,request,BrokenEngine({}))
    assert response.status_code == expected and "criteria" not in response.json()
