from copy import deepcopy
import asyncio
import sqlite3

from fastapi.testclient import TestClient
import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.ml_client import MLClient, MLInvalidResponse, MLUnavailable


def create_body(request_data):
    return {
        "conversation_id": request_data["conversation"]["id"],
        "checklist_id": request_data["checklist"]["id"],
        "checklist_version": request_data["checklist"]["version"],
    }


def test_evaluation_roundtrip_survives_app_restart(tmp_path, contracts, request_data, result_data):
    settings = Settings(seed_demo=True, database_path=tmp_path / "eva.sqlite3")

    async def evaluator(request):
        contracts.validate_request(request)
        return deepcopy(result_data)

    with TestClient(create_app(settings, evaluator)) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/api/v1/checklists").json() == [request_data["checklist"]]
        assert client.get("/api/v1/conversations").json() == [request_data["conversation"]]
        response = client.post("/api/v1/evaluations", json=create_body(request_data))
        assert response.status_code == 201
        record = response.json()
        contracts.validate("evaluation-record", record)
        assert record["original"] == result_data
        assert record["status"] == "completed"
    with TestClient(create_app(settings, evaluator)) as restarted:
        assert restarted.get(f"/api/v1/evaluations/{record['id']}").json() == record


def test_unavailable_ml_is_503_without_fabricated_score(tmp_path, request_data):
    async def unavailable(_):
        raise MLUnavailable()

    with TestClient(create_app(Settings(seed_demo=True, database_path=tmp_path / "eva.sqlite3"), unavailable)) as client:
        response = client.post("/api/v1/evaluations", json=create_body(request_data))
        assert response.status_code == 503
        assert "scoring" not in response.json()


def test_invalid_ml_response_is_502(tmp_path, request_data, result_data):
    async def invalid(_):
        invalid_result = deepcopy(result_data)
        invalid_result["criteria"][0]["value"] = 1
        return invalid_result

    with TestClient(create_app(Settings(seed_demo=True, database_path=tmp_path / "eva.sqlite3"), invalid)) as client:
        assert client.post("/api/v1/evaluations", json=create_body(request_data)).status_code == 502


def test_pending_ml_result_is_persisted_as_review(tmp_path, request_data, result_data):
    async def pending(_):
        output = deepcopy(result_data)
        output["criteria"][0].update(
            status="needs_review", value=None,
            evidence=[{"kind": "uncertain", "message_ids": ["m0002"]}],
        )
        return output

    with TestClient(create_app(Settings(seed_demo=True, database_path=tmp_path / "eva.sqlite3"), pending)) as client:
        response = client.post("/api/v1/evaluations", json=create_body(request_data))
        assert response.status_code == 201
        assert response.json()["status"] == "needs_review"
        assert response.json()["scoring"]["total"] is None


def test_scored_uncertain_result_is_rejected_without_persistence(tmp_path, request_data, result_data):
    settings = Settings(seed_demo=True, database_path=tmp_path / "eva.sqlite3")

    async def uncertain(_):
        output = deepcopy(result_data)
        output["criteria"][0]["evidence"][0]["kind"] = "uncertain"
        return output

    with TestClient(create_app(settings, uncertain)) as client:
        response = client.post("/api/v1/evaluations", json=create_body(request_data))
        assert response.status_code == 502
        assert "scoring" not in response.json()
        with sqlite3.connect(settings.database_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0] == 0


def test_request_validation_and_cors(tmp_path, request_data, result_data):
    async def evaluator(_):
        return result_data

    with TestClient(create_app(Settings(seed_demo=True, database_path=tmp_path / "eva.sqlite3"), evaluator)) as client:
        body = create_body(request_data)
        assert client.post("/api/v1/evaluations", json={**body, "checklist_version": True}).status_code == 422
        assert client.post("/api/v1/evaluations", json={**body, "checklist_version": 999}).status_code == 409
        assert client.post("/api/v1/evaluations", json={**body, "conversation_id": "absent"}).status_code == 404
        assert client.get("/api/v1/evaluations/absent").status_code == 404
        allowed = client.get("/health", headers={"Origin": "http://localhost:5173"})
        assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
        untrusted = client.get("/health", headers={"Origin": "https://example.com"})
        assert "access-control-allow-origin" not in untrusted.headers


@pytest.mark.parametrize("behavior", ["timeout", "connection", "http_error", "invalid_json"])
def test_ml_transport_errors(behavior):
    def handler(request):
        if behavior == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        if behavior == "connection":
            raise httpx.ConnectError("connection", request=request)
        if behavior == "http_error":
            return httpx.Response(503)
        return httpx.Response(200, text="not-json")

    async def run():
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8001", transport=httpx.MockTransport(handler)) as client:
            await MLClient(client).evaluate({})

    with pytest.raises(MLInvalidResponse if behavior == "invalid_json" else MLUnavailable):
        asyncio.run(run())


def test_configuration_blocks_external_ml_and_wildcard_cors():
    with pytest.raises(ValueError):
        Settings(ml_url="https://example.com")
    with pytest.raises(ValueError):
        Settings(cors_origins=("*",))
