"""Exercise the backend HTTP boundary with synthetic ML responses only."""

from copy import deepcopy
import json

from fastapi.testclient import TestClient
import httpx

from app.config import Settings
from app.main import create_app


def test_checklist_composition_and_instruction_change_without_restart(
    tmp_path, monkeypatch, contracts, request_data,
):
    first = deepcopy(request_data["checklist"])
    second = deepcopy(first)
    second.update(version=2, name="Synthetic replacement checklist")
    second["criteria"] = [
        {
            "id": "synthetic-next-action", "title": "Next action",
            "instruction": "Synthetic rule: check for an explicit next action.",
            "section_id": "synthetic-section", "type": "boolean", "weight": 30,
            "allow_na": False, "na_condition": None, "scale": [],
        },
        {
            "id": "synthetic-clarity", "title": "Clarity",
            "instruction": "Synthetic rule: rate clarity on the provided scale.",
            "section_id": "synthetic-section", "type": "scale", "weight": 70,
            "allow_na": False, "na_condition": None, "scale": [0, 2, 7],
        },
    ]
    third = deepcopy(second)
    third["version"] = 3
    third["criteria"][0]["instruction"] = "Synthetic replacement rule: check for an explicit deadline."
    sent = []

    def ml_response(request):
        assert request.method == "POST"
        assert str(request.url) == "http://127.0.0.1:8001/v1/evaluate"
        payload = json.loads(request.content)
        contracts.validate_request(payload)
        sent.append(payload)
        checklist = payload["checklist"]
        conversation = payload["conversation"]
        return httpx.Response(200, json={
            "schema_version": "1.0.0",
            "conversation_id": conversation["id"],
            "checklist_id": checklist["id"], "checklist_version": checklist["version"],
            "provider": "synthetic-test", "model": "transport-fixture",
            "warnings": [],
            "criteria": [
                {
                    "criterion_id": criterion["id"], "status": "scored",
                    "value": (max(criterion["scale"]) if criterion["type"] == "scale"
                              else criterion["type"] == "boolean"),
                    "reason": "Synthetic transport response; no model inference.",
                    "evidence": [{"kind": "message", "message_ids": [conversation["messages"][0]["id"]]}],
                }
                for criterion in checklist["criteria"]
            ],
        })

    async_client_type = httpx.AsyncClient

    def mock_ml_client(*args, **kwargs):
        return async_client_type(*args, **kwargs, transport=httpx.MockTransport(ml_response))

    # Keep the application's normal MLClient and serialization; replace only transport.
    monkeypatch.setattr("app.main.httpx.AsyncClient", mock_ml_client)
    app = create_app(Settings(database_path=tmp_path / "versions.sqlite3", seed_demo=False))
    with TestClient(app) as client:
        store = app.state.store
        store.import_conversations("synthetic-test", [request_data["conversation"]])
        records = []
        for checklist in (first, second, third, first):
            store.seed_checklist(checklist)
            response = client.post("/api/v1/evaluations", json={
                "conversation_id": request_data["conversation"]["id"],
                "checklist_id": checklist["id"], "checklist_version": checklist["version"],
            })
            assert response.status_code == 201, response.json()
            assert sent[-1] == {**request_data, "checklist": checklist}
            record = response.json()
            assert record["original"]["checklist_version"] == checklist["version"]
            assert [item["criterion_id"] for item in record["scoring"]["criteria"]] == [
                criterion["id"] for criterion in checklist["criteria"]
            ]
            assert record["scoring"]["total"] == 100
            records.append(record)

        assert len(sent) == 4
        assert client.get("/api/v1/checklists").json() == [first, second, third]
        for record, payload in zip(records, sent, strict=True):
            assert store.get_snapshot(record["id"]) == payload
            assert client.get(f"/api/v1/evaluations/{record['id']}").json() == record
