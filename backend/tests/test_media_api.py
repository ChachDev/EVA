import asyncio
from contextlib import contextmanager
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import wave

from fastapi.testclient import TestClient
import pytest

from app.config import BACKEND_ROOT, Settings
from app.main import create_app
from app.transcription_worker import process_next_transcription
from app.transcription_client import TranscriptionUnavailable


def wav_bytes(sample=0):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(sample.to_bytes(2, "little") * 8000)
    return output.getvalue()


@pytest.fixture
def app_client(tmp_path):
    @contextmanager
    def factory(**changes):
        data_root = BACKEND_ROOT / "data"
        data_root.mkdir(exist_ok=True)
        with TemporaryDirectory(prefix="synthetic-media-api-", dir=data_root) as directory:
            settings = Settings(database_path=tmp_path / (uuid4().hex + ".sqlite3"),
                                media_root=Path(directory), seed_demo=True, **changes)

            async def synthetic_evaluator(request):
                return {
                    "schema_version": "1.0.0", "conversation_id": request["conversation"]["id"],
                    "checklist_id": request["checklist"]["id"], "checklist_version": request["checklist"]["version"],
                    "provider": "fixture", "model": "synthetic-no-inference", "warnings": [],
                    "criteria": [{"criterion_id": c["id"], "status": "needs_review", "value": None,
                                  "reason": "Synthetic transport check only.",
                                  "evidence": [{"kind": "uncertain", "message_ids": ["m0001"]}]}
                                 for c in request["checklist"]["criteria"]],
                }
            app = create_app(settings, synthetic_evaluator, start_workers=False)
            with TestClient(app) as client:
                yield app, client
    return factory


def upload(client, data=None, operation=None):
    return client.post("/api/v1/audio-assets", content=data or wav_bytes(),
                       headers={"Content-Type": "audio/wav", "Idempotency-Key": operation or str(uuid4())})


def create_job(client, asset):
    response = client.post("/api/v1/transcriptions", json={
        "asset_id": asset["id"], "language": "RU", "operation_id": str(uuid4()),
    })
    assert response.status_code == 202, response.json()
    return response.json()


class SyntheticASR:
    def __init__(self, speaker="operator"):
        self.speaker = speaker

    async def transcribe(self, path, content_type, language, audio_sha256):
        assert path.is_file()
        return {"schema_version": "1.0.0", "audio_sha256": audio_sha256, "language": language,
                "provider": "test-local", "model": "synthetic-asr-response",
                "segments": [{"speaker": self.speaker, "text": "Synthetic utterance.",
                              "start_seconds": 0.2, "end_seconds": 0.9}]}


def test_audio_upload_idempotency_and_range(app_client, contracts):
    with app_client() as (_app, client):
        operation, data = str(uuid4()), wav_bytes()
        response = upload(client, data, operation)
        assert response.status_code == 201
        asset = response.json()
        contracts.validate("audio-asset", asset)
        assert upload(client, data, operation).json() == asset
        assert upload(client, wav_bytes(1), operation).status_code == 409
        ranged = client.get(f"/api/v1/audio-assets/{asset['id']}/content", headers={"Range": "bytes=0-15"})
        assert ranged.status_code == 206
        assert ranged.content == data[:16]
        assert ranged.headers["content-range"] == f"bytes 0-15/{len(data)}"
        assert ranged.headers["x-content-type-options"] == "nosniff"
        assert client.get(f"/api/v1/audio-assets/{asset['id']}/content",
                          headers={"Range": "bytes=999999-"}).status_code == 416
        assert client.get(f"/api/v1/audio-assets/{asset['id']}").json() == asset


def test_unconfigured_asr_is_explicit_and_preserves_audio(app_client):
    with app_client() as (_app, client):
        asset = upload(client).json()
        response = client.post("/api/v1/transcriptions", json={
            "asset_id": asset["id"], "language": "RU", "operation_id": str(uuid4()),
        })
        assert response.status_code == 503
        assert "not configured" in response.json()["detail"]
        assert client.get(f"/api/v1/audio-assets/{asset['id']}/content").status_code == 200


def test_audio_limits_and_invalid_content(app_client):
    with app_client(audio_max_bytes=100) as (_app, client):
        assert upload(client).status_code == 413
        assert upload(client, b"not-wav").status_code == 422
        assert client.post("/api/v1/audio-assets", content=b"<html>", headers={
            "Content-Type": "text/html", "Idempotency-Key": str(uuid4()),
        }).status_code == 415


def test_audio_job_to_conversation_to_evaluation(app_client, contracts):
    with app_client(asr_url="http://127.0.0.1:8001") as (app, client):
        asset = upload(client).json()
        job = create_job(client, asset)
        result = asyncio.run(process_next_transcription(app.state.media, SyntheticASR()))
        assert result["id"] == job["id"]
        assert result["status"] == "completed"
        contracts.validate("transcription-job", result)
        conversation = client.get(f"/api/v1/conversations/{result['conversation_id']}").json()
        contracts.validate("conversation", conversation)
        assert conversation["messages"][0]["offset_seconds"] == 0.2
        assert conversation["audio_url"].endswith(f"/api/v1/audio-assets/{asset['id']}/content")
        checklist = client.get("/api/v1/checklists").json()[0]
        evaluation = client.post("/api/v1/evaluations", json={
            "conversation_id": conversation["id"], "checklist_id": checklist["id"], "checklist_version": checklist["version"],
        })
        assert evaluation.status_code == 201
        assert evaluation.json()["scoring"]["total"] is None
        saved = client.get(f"/api/v1/evaluations/{evaluation.json()['id']}/snapshot").json()
        assert saved["conversation"] == conversation


def test_unknown_speaker_requires_review_and_retains_original_asr(app_client):
    with app_client(asr_url="http://127.0.0.1:8001") as (app, client):
        job = create_job(client, upload(client).json())
        result = asyncio.run(process_next_transcription(app.state.media, SyntheticASR("unknown")))
        assert result["status"] == "needs_review"
        assert result["conversation_id"] is None
        body = {"operation_id": str(uuid4()), "roles": ["operator"]}
        reviewed = client.post(f"/api/v1/transcriptions/{job['id']}/review", json=body)
        assert reviewed.status_code == 200, reviewed.json()
        assert reviewed.json()["status"] == "completed"
        assert reviewed.json()["result"] == result["result"]
        assert client.post(f"/api/v1/transcriptions/{job['id']}/review", json=body).json() == reviewed.json()
        changed = {**body, "roles": ["client"]}
        assert client.post(f"/api/v1/transcriptions/{job['id']}/review", json=changed).status_code == 409


def test_worker_failure_does_not_create_fake_transcript(app_client):
    class FailedASR:
        async def transcribe(self, *_args):
            raise RuntimeError("private audio content and credentials")

    with app_client(asr_url="http://127.0.0.1:8001") as (app, client):
        job = create_job(client, upload(client).json())
        result = asyncio.run(process_next_transcription(app.state.media, FailedASR()))
        assert result["status"] == "failed"
        assert result["conversation_id"] is None
        assert result["result"] is None
        response = client.get(f"/api/v1/transcriptions/{job['id']}")
        assert "private" not in response.text


def test_worker_cancellation_marks_job_interrupted(app_client):
    async def run(media):
        started = asyncio.Event()

        class WaitingASR:
            async def transcribe(self, *_args):
                started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(process_next_transcription(media, WaitingASR()))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with app_client(asr_url="http://127.0.0.1:8001") as (app, client):
        job = create_job(client, upload(client).json())
        asyncio.run(run(app.state.media))
        result = client.get(f"/api/v1/transcriptions/{job['id']}").json()
        assert result["status"] == "failed"
        assert result["error_code"] == "interrupted"


def test_worker_classifies_unavailable_provider_without_transcript(app_client):
    class UnavailableASR:
        async def transcribe(self, *_args):
            raise TranscriptionUnavailable("private upstream error")

    with app_client(asr_url="http://127.0.0.1:8001") as (app, client):
        job = create_job(client, upload(client).json())
        result = asyncio.run(process_next_transcription(app.state.media, UnavailableASR()))
        assert result["id"] == job["id"]
        assert result["error_code"] == "asr_unavailable"
        assert result["status"] == "failed"
        assert result["conversation_id"] is None
        assert result["result"] is None
