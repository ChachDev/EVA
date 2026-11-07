import asyncio
from copy import deepcopy
from uuid import UUID

from fastapi.testclient import TestClient
import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.parametrize("body", [
    b'{"conversation_id":"private-marker","checklist_version":NaN}',
    b'{"conversation_id":"private-marker","checklist_version":1e999}',
    b'{"conversation_id":"private-marker","conversation_id":"duplicate"}',
    b'{"conversation_id":"\\ud800"}',
    b'{"conversation_id":"private-marker\\u0000"}',
])
@pytest.mark.parametrize("content_type", [None, "application/json"])
def test_invalid_json_is_safe_and_has_request_id(tmp_path, body, content_type):
    app = create_app(Settings(database_path=tmp_path / "http.sqlite3"), start_workers=False)
    headers = {"Origin": "http://localhost:5173", "X-Request-ID": "untrusted-secret"}
    if content_type:
        headers["Content-Type"] = content_type
    with TestClient(app) as client:
        response = client.post("/api/v1/evaluations", content=body, headers=headers)
        assert response.status_code == 400
        assert "private-marker" not in response.text
        assert "untrusted-secret" not in response.text
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert str(UUID(response.headers["x-request-id"])) == response.headers["x-request-id"]


def test_evaluation_overload_is_bounded_and_releases_capacity(tmp_path, request_data, result_data):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        active = peak = 0

        async def evaluator(_request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            started.set()
            try:
                await release.wait()
                return deepcopy(result_data)
            finally:
                active -= 1

        settings = Settings(database_path=tmp_path / "capacity.sqlite3", seed_demo=True,
                            ml_max_concurrency=1, evaluation_queue_capacity=1, ml_timeout_seconds=5)
        app = create_app(settings, evaluator, start_workers=False)
        body = {"conversation_id": request_data["conversation"]["id"],
                "checklist_id": request_data["checklist"]["id"], "checklist_version": 1}
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                first = asyncio.create_task(client.post("/api/v1/evaluations", json=body))
                await asyncio.wait_for(started.wait(), 2)
                second = asyncio.create_task(client.post("/api/v1/evaluations", json=body))
                async with asyncio.timeout(2):
                    while app.state.evaluation_requests.qsize() != 2:
                        await asyncio.sleep(0.001)
                overloaded = await client.post("/api/v1/evaluations", json=body)
                assert overloaded.status_code == 503
                assert overloaded.headers["retry-after"] == "5"
                assert (await client.get("/health")).status_code == 200
                release.set()
                assert [response.status_code for response in await asyncio.gather(first, second)] == [201, 201]
                assert peak == 1
                assert app.state.evaluation_requests.qsize() == 0
    asyncio.run(run())


def test_all_contract_examples_validate(contracts):
    for path in (contracts.directory / "examples").glob("*.json"):
        contracts.example(path.stem)


def test_browser_can_use_upload_range_and_retry_headers(tmp_path):
    app = create_app(Settings(database_path=tmp_path / "cors.sqlite3"), start_workers=False)
    with TestClient(app) as client:
        preflight = client.options("/api/v1/audio-assets", headers={
            "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,idempotency-key,range,if-range",
        })
        assert preflight.status_code == 200
        response = client.get("/health", headers={"Origin": "http://localhost:5173"})
        exposed = {header.strip().lower() for header in response.headers["access-control-expose-headers"].split(",")}
        assert {"retry-after", "etag", "content-range", "x-request-id"} <= exposed


@pytest.mark.parametrize("changes", [
    {"ml_url": "http://127.0.0.1:99999"},
    {"asr_url": "https://external.example"},
    {"asr_url": "http://127.0.0.1:8001/transcribe"},
    {"evaluation_queue_capacity": -1}, {"request_max_bytes": 0},
    {"audio_max_bytes": 200 * 1024 * 1024}, {"asr_timeout_seconds": float("nan")},
    {"reviewer_id": "spoofed\nidentity"}, {"media_root": "outside"},
])
def test_new_configuration_guards(changes):
    with pytest.raises(ValueError):
        Settings(**changes)
