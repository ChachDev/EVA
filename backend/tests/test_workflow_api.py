"""Exercise public workflow routes using synthetic data and injected inference."""

from copy import deepcopy
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.main import create_app
from app.workflow_api import install_workflow_routes


@pytest.fixture
def workflow_client(tmp_path, contracts):
    async def evaluator(request):
        conversation, checklist = request["conversation"], request["checklist"]
        return {
            "schema_version": "1.0.0", "conversation_id": conversation["id"],
            "checklist_id": checklist["id"], "checklist_version": checklist["version"],
            "provider": "synthetic-test", "model": "workflow-fixture", "warnings": [],
            "criteria": [{
                "criterion_id": item["id"], "status": "scored",
                "value": max(item["scale"]) if item["type"] == "scale" else item["type"] == "boolean",
                "reason": "Synthetic test decision; no model call.",
                "evidence": [{"kind": "message", "message_ids": [conversation["messages"][0]["id"]]}],
            } for item in checklist["criteria"]],
        }

    settings = Settings(database_path=tmp_path / "workflow-api.sqlite3", seed_demo=True,
                        reviewer_id="synthetic-reviewer")
    app = create_app(settings, evaluator, start_workers=False)
    # Keep these tests usable before and after main installs the new adapter.
    if not any(getattr(route, "path", None) == "/api/v1/review-cases/{evaluation_id}" for route in app.routes):
        install_workflow_routes(app, contracts, app.state.store, settings)
    with TestClient(app) as client:
        yield client


def conversation_body():
    return {
        "operation_id": str(uuid4()), "channel": "agent_chat", "language": "RU",
        "messages": [
            {"timestamp": "00:00", "speaker": "client", "text": "Synthetic request"},
            {"timestamp": "00:01", "speaker": "operator", "text": "Synthetic answer"},
        ],
    }


def create_evaluation(client, request_data, **changes):
    response = client.post("/api/v1/evaluations", json={
        "conversation_id": request_data["conversation"]["id"],
        "checklist_id": request_data["checklist"]["id"],
        "checklist_version": request_data["checklist"]["version"], **changes,
    })
    assert response.status_code == 201, response.json()
    return response.json()


def appeal_body(**changes):
    return {"operation_id": str(uuid4()), "expected_revision": 0, "criterion_id": "contact",
            "reason": "Synthetic appeal reason", "message_ids": ["m0001"], **changes}


def test_manual_conversation_replay_conflict_and_read(workflow_client, contracts):
    body = conversation_body()
    first = workflow_client.post("/api/v1/conversations", json=body)
    replay = workflow_client.post("/api/v1/conversations", json=body)
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    conversation = first.json()
    assert UUID(conversation["id"]).version == 5
    contracts.validate("conversation", conversation)
    assert [message["id"] for message in conversation["messages"]] == ["m0001", "m0002"]
    assert [message["offset_seconds"] for message in conversation["messages"]] == [0, 1]
    assert conversation["audio_url"] is None
    assert workflow_client.get(f"/api/v1/conversations/{conversation['id']}").json() == conversation
    changed = deepcopy(body)
    changed["messages"][0]["text"] = "Different synthetic source"
    assert workflow_client.post("/api/v1/conversations", json=changed).status_code == 409
    assert workflow_client.get(f"/api/v1/conversations/{conversation['id']}").json() == conversation


def test_manual_voice_preserves_iso_time_without_inventing_audio_seek(workflow_client):
    body = conversation_body()
    body["channel"] = "voice_call"
    body["messages"][0]["timestamp"] = "2026-11-07T10:00:00+05:00"
    body["messages"][1]["timestamp"] = "2026-11-07T10:00:01+05:00"
    response = workflow_client.post("/api/v1/conversations", json=body)
    assert response.status_code == 201
    assert [item["timestamp"] for item in response.json()["messages"]] == [item["timestamp"] for item in body["messages"]]
    assert all(item["offset_seconds"] is None for item in response.json()["messages"])


def test_checklist_publication_and_new_criteria_are_used_without_restart(workflow_client, contracts, request_data):
    old = create_evaluation(workflow_client, request_data)
    body = {"base_version": 1, "name": "Synthetic version two", "criteria": [{
        "id": "synthetic-new-rule", "title": "Synthetic new rule",
        "instruction": "Use this newly published synthetic instruction.",
        "section_id": "synthetic", "type": "scale", "weight": 100,
        "allow_na": False, "na_condition": None, "scale": [0, 2, 7],
    }]}
    checklist_id = request_data["checklist"]["id"]
    response = workflow_client.post(f"/api/v1/checklists/{checklist_id}/versions", json=body)
    assert response.status_code == 201, response.json()
    published = response.json()
    contracts.validate("checklist", published)
    assert published == {"id": checklist_id, "version": 2, "name": body["name"], "criteria": body["criteria"]}
    assert workflow_client.post(f"/api/v1/checklists/{checklist_id}/versions", json=body).status_code == 409
    current = create_evaluation(workflow_client, request_data, checklist_version=2)
    assert [item["criterion_id"] for item in current["original"]["criteria"]] == ["synthetic-new-rule"]
    assert current["scoring"]["total"] == 100
    snapshot = workflow_client.get(f"/api/v1/evaluations/{current['id']}/snapshot")
    assert snapshot.status_code == 200
    assert snapshot.json()["checklist"] == published
    assert workflow_client.get(f"/api/v1/evaluations/{old['id']}/snapshot").json()["checklist"] == request_data["checklist"]


def test_new_checklist_starts_at_version_one_and_cannot_overwrite_it(workflow_client, request_data):
    body = {"base_version": 0, "name": "Synthetic new checklist",
            "criteria": request_data["checklist"]["criteria"]}
    response = workflow_client.post("/api/v1/checklists/synthetic-new-checklist/versions", json=body)
    assert response.status_code == 201, response.json()
    assert response.json()["id"] == "synthetic-new-checklist"
    assert response.json()["version"] == 1
    conflict = workflow_client.post("/api/v1/checklists/synthetic-new-checklist/versions", json=body)
    assert conflict.status_code == 409


def test_evaluation_history_pagination_filter_and_snapshot(workflow_client, contracts, request_data):
    records = [create_evaluation(workflow_client, request_data) for _ in range(3)]
    first = workflow_client.get("/api/v1/evaluations", params={"limit": 2})
    assert first.status_code == 200
    page = first.json()
    contracts.validate("evaluation-list", page)
    assert len(page["items"]) == 2
    assert page["next_cursor"]
    second = workflow_client.get("/api/v1/evaluations", params={"limit": 2, "cursor": page["next_cursor"]})
    assert second.status_code == 200
    assert second.json()["next_cursor"] is None
    assert {record["id"] for record in [*page["items"], *second.json()["items"]]} == {record["id"] for record in records}
    missing = workflow_client.get("/api/v1/evaluations", params={"conversation_id": "synthetic-absent"})
    assert missing.json() == {"items": [], "next_cursor": None}
    snapshot = workflow_client.get(f"/api/v1/evaluations/{records[0]['id']}/snapshot")
    assert snapshot.status_code == 200
    assert snapshot.json() == request_data


def test_appeal_acceptance_rescores_final_preserves_original_and_replays(workflow_client, contracts, request_data):
    record = create_evaluation(workflow_client, request_data)
    identifier = record["id"]
    initial = workflow_client.get(f"/api/v1/review-cases/{identifier}")
    assert initial.status_code == 200
    assert initial.json()["revision"] == 0
    assert initial.json()["original"] == record
    assert initial.json()["final"]["scoring"]["total"] == 100
    body = appeal_body()
    created = workflow_client.post(f"/api/v1/evaluations/{identifier}/appeals", json=body)
    assert created.status_code == 201, created.json()
    case = created.json()
    contracts.validate("review-case", case)
    assert case["revision"] == 1
    assert case["review_status"] == "appealed"
    assert workflow_client.post(f"/api/v1/evaluations/{identifier}/appeals", json=body).json() == case
    assert workflow_client.post(f"/api/v1/evaluations/{identifier}/appeals",
                                json={**body, "reason": "Changed synthetic reason"}).status_code == 409
    decision = {"operation_id": str(uuid4()), "expected_revision": 1, "decision": "accepted",
                "reason": "Synthetic reviewer decision", "override": {
                    "criterion_id": "contact", "status": "scored", "value": False,
                    "reason": "Synthetic corrected decision",
                    "evidence": [{"kind": "missing_action", "message_ids": ["m0001"]}],
                }}
    appeal_id = case["appeals"][0]["id"]
    accepted = workflow_client.post(f"/api/v1/appeals/{appeal_id}/decisions", json=decision)
    assert accepted.status_code == 201, accepted.json()
    final = accepted.json()
    contracts.validate("review-case", final)
    assert final["revision"] == 2
    assert final["review_status"] == "resolved"
    assert final["original"] == record
    assert final["original"]["scoring"]["total"] == 100
    assert final["final"]["scoring"]["total"] == 90
    assert final["decisions"][0]["reviewer_id"] == "synthetic-reviewer"
    assert workflow_client.post(f"/api/v1/appeals/{appeal_id}/decisions", json=decision).json() == final
    stale = {**decision, "operation_id": str(uuid4())}
    assert workflow_client.post(f"/api/v1/appeals/{appeal_id}/decisions", json=stale).status_code == 409
    assert workflow_client.get(f"/api/v1/evaluations/{identifier}").json() == record
    assert workflow_client.get(f"/api/v1/review-cases/{identifier}").json() == final


def test_rejected_appeal_keeps_original_decision(workflow_client, request_data):
    record = create_evaluation(workflow_client, request_data)
    case = workflow_client.post(f"/api/v1/evaluations/{record['id']}/appeals", json=appeal_body()).json()
    response = workflow_client.post(f"/api/v1/appeals/{case['appeals'][0]['id']}/decisions", json={
        "operation_id": str(uuid4()), "expected_revision": 1, "decision": "rejected", "reason": "Synthetic rejection",
    })
    assert response.status_code == 201
    assert response.json()["final"]["scoring"] == record["scoring"]
    assert response.json()["appeals"][0]["status"] == "rejected"


@pytest.mark.parametrize("path", [
    "/api/v1/conversations/synthetic-absent",
    "/api/v1/evaluations/synthetic-absent/snapshot",
    "/api/v1/review-cases/synthetic-absent",
])
def test_missing_workflow_ids_return_404(workflow_client, path):
    assert workflow_client.get(path).status_code == 404


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"cursor": "synthetic-private-invalid-cursor"}])
def test_invalid_history_queries_are_safe_422(workflow_client, params):
    response = workflow_client.get("/api/v1/evaluations", params=params)
    assert response.status_code == 422
    assert "synthetic-private" not in response.text


def test_invalid_create_and_appeal_input_are_sanitized(workflow_client, request_data):
    invalid = conversation_body()
    invalid["messages"][0]["timestamp"] = "synthetic-private-invalid-time"
    response = workflow_client.post("/api/v1/conversations", json=invalid)
    assert response.status_code == 422
    assert "synthetic-private" not in response.text
    record = create_evaluation(workflow_client, request_data)
    for body in (appeal_body(criterion_id="synthetic-private-missing-criterion"),
                 appeal_body(message_ids=["synthetic-private-missing-message"]),
                 appeal_body(operation_id="synthetic-private-invalid-uuid")):
        response = workflow_client.post(f"/api/v1/evaluations/{record['id']}/appeals", json=body)
        assert response.status_code == 422
        assert "synthetic-private" not in response.text
