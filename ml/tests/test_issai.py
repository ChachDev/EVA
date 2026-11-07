from contextlib import nullcontext
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from eva_ml import app as service
from eva_ml.engine import EngineError
from eva_ml.issai import InvalidDecision, resolve_evidence


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


@pytest.mark.parametrize("failure,expected", [("wrong_evidence",200),("offline",503)])
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
    assert response.status_code == expected
    if failure == "offline":
        assert "criteria" not in response.json()
    else:
        row = response.json()["criteria"][0]
        assert row["status"] == "needs_review" and row["value"] is None
        assert row["evidence"][0]["kind"] == "uncertain"


def test_evidence_representation_preserves_real_ids_and_order():
    ids = {"m0001", "m0014", "custom"}
    assert resolve_evidence(["m014", ",", "custom", "m0001"], ids) == ["m0014", "custom", "m0001"]
    assert resolve_evidence([","], {","}) == [","]  # exact contract IDs take precedence
    assert resolve_evidence(["m01"], {"m01", "m0001"}) == ["m01"]


@pytest.mark.parametrize("references,choices", [
    ([","], {"m0001"}), (["m19"], {"m0001"}), (["m1"], {"m01","m001"}),
    (["m0001", "invented"], {"m0001"}), (["m0001-m0010"], {"m0001","m0010"}),
])
def test_bad_evidence_cannot_acquire_an_arbitrary_source(references, choices):
    with pytest.raises(InvalidDecision):
        resolve_evidence(references, choices)


def test_context_overflow_returns_all_pending_with_real_boundaries(monkeypatch, evaluation_request):
    import io
    from urllib.error import HTTPError
    from eva_ml.engine import LocalEngine

    class Rejected:
        def open(self, req, timeout):
            raise HTTPError(req.full_url, 400, "Bad request", {}, io.BytesIO(
                b'{"error":{"type":"exceed_context_size_error","n_prompt_tokens":21000,"n_ctx":16384}}'))

    class OverflowOnEvaluation(Inference):
        def request(self, route, payload):
            if payload["max_tokens"] == 1:
                return {"choices":[{"finish_reason":"length"}]}
            engine = LocalEngine(1, self.model)
            engine.client = Rejected()
            return engine.request(route, payload)

    response = evaluate(monkeypatch, evaluation_request, OverflowOnEvaluation({}))
    assert response.status_code == 200
    results = response.json()["criteria"]
    assert len(results) == len(evaluation_request["checklist"]["criteria"])
    ids = [m["id"] for m in evaluation_request["conversation"]["messages"]]
    assert all(row["status"] == "needs_review" and row["value"] is None and
               "21000" in row["reason"] and "16384" in row["reason"] and
               row["evidence"] == [{"kind":"uncertain","message_ids":[ids[0],ids[-1]]}]
               for row in results)


@pytest.mark.parametrize("change_facts", [False, True])
def test_evidence_selection_is_bounded_to_original_decisions(monkeypatch, evaluation_request, change_facts):
    request = deepcopy(evaluation_request)
    request["checklist"]["criteria"] = [c for c in request["checklist"]["criteria"] if c["id"] in ("contact", "needs")]
    ids = [m["id"] for m in request["conversation"]["messages"]]
    first = {"contact":{"facts":["yes","yes"],"evidence":[ids[0]]},
             "needs":{"facts":["no","no","unknown"],"evidence":[","]}}
    calls = []

    class SelectReference(Inference):
        def request(self, route, payload):
            if payload["max_tokens"] == 1:
                return {"choices":[{"finish_reason":"length"}]}
            calls.append(payload)
            rows = deepcopy(first)
            if len(calls) == 2:
                schema = payload["response_format"]["json_schema"]["schema"]["properties"]
                assert schema["contact"] == {"const": first["contact"]}
                assert schema["needs"]["properties"]["facts"]["const"] == first["needs"]["facts"]
                assert schema["needs"]["properties"]["evidence"]["items"]["enum"] == ids
                assert {k:v for k,v in calls[0].items() if k!="response_format"} == {
                    k:v for k,v in payload.items() if k!="response_format"}
                rows["needs"]["evidence"] = [ids[-1]]
                if change_facts:
                    rows["needs"]["facts"][0] = "yes"
            return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps(rows)}}]}

    response = evaluate(monkeypatch, request, SelectReference({}))
    assert len(calls) == 2
    if change_facts:
        assert response.status_code == 200
        good, failed = response.json()["criteria"]
        assert good["value"] is True and good["evidence"][0]["message_ids"] == [ids[0]]
        assert failed["status"] == "needs_review" and failed["value"] is None
    else:
        assert response.status_code == 200
        assert response.json()["criteria"][1]["evidence"][0]["message_ids"] == [ids[-1]]


def test_prepared_plan_invalidates_all_semantically_distinct_keys(evaluation_request):
    from eva_ml.issai import compiled_plan, plan_for
    cs = evaluation_request["checklist"]
    _, _, original = plan_for(cs, "model@a")
    for field, value in [("instruction", "Новый смысл"), ("scale", [0, 1, 2]),
                          ("na_condition", "Другое условие"), ("allow_na", False)]:
        other = deepcopy(cs)
        c = next(c for c in other["criteria"] if c["id"] == "consultation")
        c[field] = value
        assert plan_for(other, "model@a")[2] != original
    assert plan_for({**cs, "version":cs["version"]+1}, "model@a")[2] != original
    assert plan_for(cs, "model@b")[2] != original
    assert compiled_plan(cs["id"], cs["version"], json.dumps(cs["criteria"]), "model@a", "new-prompt")[2] != original
    assert plan_for(cs, "model@a")[2] == original


def test_malformed_row_and_bad_correction_do_not_hide_valid_neighbor(monkeypatch, evaluation_request):
    request = deepcopy(evaluation_request)
    request["checklist"]["criteria"] = [c for c in request["checklist"]["criteria"] if c["id"] in ("contact","needs")]
    mid = request["conversation"]["messages"][0]["id"]
    good = {"facts":["yes","yes"],"evidence":[mid]}
    class Damaged(Inference):
        attempts = 0
        def request(self, route, payload):
            if payload["max_tokens"] == 1: return {"choices":[{"finish_reason":"length"}]}
            self.attempts += 1
            if self.attempts == 2:
                assert payload["response_format"]["json_schema"]["schema"]["properties"]["contact"] == {"const":good}
                raise EngineError("worker disconnected during row correction")
            rows = {"contact":good,"needs":{"facts":"bad shape","evidence":[mid]}}
            return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps(rows)}}]}
    damaged = Damaged({})
    resp = evaluate(monkeypatch, request, damaged)
    assert resp.status_code == 200 and damaged.attempts == 2
    contact, needs = resp.json()["criteria"]
    assert contact["value"] is True and contact["evidence"][0]["message_ids"] == [mid]
    assert needs["status"] == "needs_review" and needs["value"] is None
