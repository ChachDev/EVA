"""Synthetic local media and SQLite jobs only; no live ASR or audio downloads."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import hashlib
import io
from pathlib import Path
from uuid import uuid4
import wave

import pytest
from sqlalchemy import insert, select

from app import media
from app.media import MediaBacklogFull, MediaConflict, MediaInvalid, MediaNotFound, MediaService, MediaStorageError
from app.storage import EvaluationStore


def operation():
    return str(uuid4())


def wav(seconds=1, rate=8000):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b'\0\0' * int(seconds * rate))
    return output.getvalue()


@pytest.fixture
def service(tmp_path):
    store = EvaluationStore(tmp_path / "media.sqlite3")
    store.initialize()
    service = MediaService(store, tmp_path / "audio", media_base_url="http://127.0.0.1:8000")
    yield service
    store.close()


def asr(asset, *, unknown=False, language="RU"):
    return {"schema_version": "1.0.0", "audio_sha256": asset["sha256"], "language": language,
            "provider": "synthetic-asr", "model": "synthetic-model", "segments": [
                {"speaker": "unknown" if unknown else "client", "text": "Synthetic request", "start_seconds": 0.1, "end_seconds": 0.4},
                {"speaker": "operator", "text": "Synthetic reply", "start_seconds": 0.5, "end_seconds": 0.9},
            ]}


def running(service, *, language="RU"):
    asset = service.upload(wav(), "audio/wav", operation())
    job = service.create_job(asset["id"], language, operation())
    claim = service.claim_job(job["id"])
    return asset, job, claim


def test_upload_has_private_uuid_path_hash_and_duration(service):
    data = wav()
    asset = service.upload(data, "Audio/X-Wav; ignored=parameter", operation())
    assert set(asset) == {"id", "content_type", "size_bytes", "sha256", "duration_seconds", "created_at"}
    assert asset["content_type"] == "audio/wav" and asset["duration_seconds"] == 1
    assert asset["size_bytes"] == len(data) and asset["sha256"] == hashlib.sha256(data).hexdigest()
    path = service.asset_path(asset["id"])
    assert path.name == asset["id"] + ".wav" and path.read_bytes() == data
    assert path.is_relative_to(service.root)
    assert service.get_asset(asset["id"]) == asset
    assert not list(service.root.glob(".tmp-*"))


def test_upload_replay_is_idempotent_but_changed_content_conflicts(service):
    key = operation()
    asset = service.upload(wav(), "audio/wav", key)
    assert service.upload(wav(), "audio/x-wav", key) == asset
    with pytest.raises(MediaConflict):
        service.upload(wav(0.5), "audio/wav", key)
    with pytest.raises(MediaConflict):
        service.upload(wav(), "audio/ogg", key)
    assert len(list(service.root.iterdir())) == 1


def test_parallel_same_upload_has_one_asset_and_no_orphan(service):
    key = operation()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.upload(wav(), "audio/wav", key), range(2)))
    assert results[0] == results[1]
    assert len(list(service.root.iterdir())) == 1


@pytest.mark.parametrize("body,content_type", [
    (b'', "audio/wav"), (b'not audio', "audio/wav"), (wav(), "text/plain"),
    (b'RIFF' + b'\0' * 80, "audio/wav"), (wav(0), "audio/wav"),
    (wav()[:-1], "audio/wav"), (wav() + b'extra', "audio/wav"),
    (b'ID3\4\0\0\0\0\0\0', "audio/mpeg"), (b'fLaC', "audio/flac"), (b'OggS', "audio/ogg"),
], ids=["empty", "not-wav", "unsupported-mime", "bad-header", "empty-frames", "truncated", "trailing", "id3-only", "flac-truncated", "ogg-truncated"])
def test_invalid_or_empty_audio_is_rejected_before_storage(service, body, content_type):
    with pytest.raises(MediaInvalid):
        service.upload(body, content_type, operation())
    assert not service.root.exists()


def test_wav_over_one_hour_is_rejected(service):
    # One sample/second keeps this boundary fixture small.
    with pytest.raises(MediaInvalid):
        service.upload(wav(3601, rate=1), "audio/wav", operation())


@pytest.mark.parametrize("body,content_type", [
    (b'\xff\xfb\x90\x64' + b'\0' * 413, "audio/mpeg"),
    (b'fLaC\x80\x00\x00\x22' + b'\0' * 34 + b'\xff\xf8', "audio/flac"),
    (b'OggS\x00' + b'\0' * 21 + b'\x01\x08OpusHead', "audio/ogg"),
], ids=["mp3-signature", "flac-signature", "ogg-signature"])
def test_compressed_signature_intake_does_not_invent_duration(service, body, content_type):
    asset = service.upload(body, content_type, operation())
    assert asset["content_type"] == content_type and asset["duration_seconds"] is None
    assert service.asset_path(asset["id"]).read_bytes() == body


def test_idempotent_upload_does_not_claim_a_missing_file_is_available(service):
    key = operation()
    asset = service.upload(wav(), "audio/wav", key)
    service.asset_path(asset["id"]).unlink()
    with pytest.raises(MediaStorageError):
        service.upload(wav(), "audio/wav", key)


@pytest.mark.parametrize("resize", [True, False])
def test_asset_path_refuses_corrupted_file_instead_of_old_metadata(service, resize):
    asset = service.upload(wav(), "audio/wav", operation())
    path = service.asset_path(asset["id"])
    original = path.read_bytes()
    damaged = original[:-1] if resize else original[:-1] + bytes([original[-1] ^ 0x01])
    path.write_bytes(damaged)
    with pytest.raises(MediaStorageError):
        service.asset_path(asset["id"])


def test_asset_path_refuses_symlink_even_to_another_asset_inside_root(service):
    asset = service.upload(wav(), "audio/wav", operation())
    another = service.upload(wav(0.5), "audio/wav", operation())
    original = service.asset_path(asset["id"])
    target = service.asset_path(another["id"])
    original.unlink()
    try:
        original.symlink_to(target)
    except OSError:
        pytest.skip("This Windows account cannot create symbolic links")
    with pytest.raises(MediaStorageError):
        service.asset_path(asset["id"])


def test_size_and_operation_id_are_checked(service):
    service.max_upload_bytes = 10
    with pytest.raises(MediaInvalid):
        service.upload(wav(), "audio/wav", operation())
    with pytest.raises(MediaInvalid):
        service.upload(b'bad', "audio/wav", "../../private")
    with pytest.raises(MediaInvalid):
        service.asset_path("../private")


def test_failed_db_insert_cleans_saved_audio(service, monkeypatch):
    from contextlib import contextmanager
    @contextmanager
    def unavailable():
        raise RuntimeError("synthetic database failure")
        yield
    monkeypatch.setattr(service.store, "_write_connection", unavailable)
    with pytest.raises(RuntimeError):
        service.upload(wav(), "audio/wav", operation())
    assert not list(service.root.iterdir())


def test_atomic_file_replace_failure_cleans_temporary_audio(service, monkeypatch):
    def broken(*_):
        raise OSError("SYNTHETIC_PRIVATE_PATH")
    monkeypatch.setattr(media.os, "replace", broken)
    with pytest.raises(MediaStorageError) as captured:
        service.upload(wav(), "audio/wav", operation())
    assert "SYNTHETIC_PRIVATE_PATH" not in str(captured.value)
    assert not list(service.root.iterdir())


def test_job_metadata_idempotency_and_backlog(service):
    asset = service.upload(wav(), "audio/wav", operation())
    key = operation()
    job = service.create_job(asset["id"], "RU", key)
    assert set(job) == {"id", "asset_id", "language", "status", "created_at", "updated_at", "conversation_id", "error_code", "result"}
    assert job["status"] == "queued" and job["result"] is None
    assert service.create_job(asset["id"], "RU", key) == job
    with pytest.raises(MediaConflict):
        service.create_job(asset["id"], "KZ", key)
    service.max_pending_jobs = 1
    with pytest.raises(MediaBacklogFull):
        service.create_job(asset["id"], "RU", operation())


def test_concurrent_claim_has_only_one_owner(service):
    asset = service.upload(wav(), "audio/wav", operation())
    job = service.create_job(asset["id"], "EN", operation())
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: service.claim_job(job["id"]), range(2)))
    assert sum(claim is not None for claim in claims) == 1
    assert service.claim_job(job["id"]) is None
    assert "lease_token" not in service.get_job(job["id"])


@pytest.mark.parametrize("language", ["RU", "KZ", "EN"])
def test_completion_creates_one_canonical_conversation_and_preserves_asr(service, language):
    asset, job, claim = running(service, language=language)
    answer = asr(asset, language=language)
    before = deepcopy(answer)
    done = service.complete_job(job["id"], claim["lease_token"], answer)
    assert done["status"] == "completed" and done["result"] == before and answer == before
    conversation = service.store.get_conversation(done["conversation_id"])
    service.contracts.validate("conversation", conversation)
    assert conversation["channel"] == "voice_call" and conversation["language"] == language
    assert [m["id"] for m in conversation["messages"]] == ["m0001", "m0002"]
    assert [m["speaker"] for m in conversation["messages"]] == ["client", "operator"]
    assert [m["offset_seconds"] for m in conversation["messages"]] == [0.1, 0.5]
    assert [m["timestamp"] for m in conversation["messages"]] == ["00:00", "00:00"]
    assert conversation["audio_url"].endswith(f"/api/v1/audio-assets/{asset['id']}/content")
    assert service.complete_job(job["id"], claim["lease_token"], answer) == done
    assert len(service.store.list_conversations()) == 1


def test_wrong_lease_or_hash_never_creates_a_conversation(service):
    asset, job, claim = running(service)
    with pytest.raises(MediaConflict):
        service.complete_job(job["id"], operation(), asr(asset))
    answer = asr(asset)
    answer["audio_sha256"] = "f" * 64
    with pytest.raises(MediaInvalid):
        service.complete_job(job["id"], claim["lease_token"], answer)
    assert service.get_job(job["id"])["status"] == "running"
    assert service.store.list_conversations() == []


def test_unknown_speaker_requires_review_and_original_asr_is_immutable(service):
    asset, job, claim = running(service)
    original = asr(asset, unknown=True)
    checked = service.complete_job(job["id"], claim["lease_token"], original)
    assert checked["status"] == "needs_review" and checked["conversation_id"] is None
    assert checked["result"] == original
    assert service.store.list_conversations() == []
    key = operation()
    reviewed = service.review_job(job["id"], ["client", "operator"], key)
    assert reviewed["status"] == "completed" and reviewed["result"] == original
    conversation = service.store.get_conversation(reviewed["conversation_id"])
    assert [m["speaker"] for m in conversation["messages"]] == ["client", "operator"]
    assert [m["text"] for m in conversation["messages"]] == [segment["text"] for segment in original["segments"]]
    assert service.review_job(job["id"], ["client", "operator"], key) == reviewed
    with pytest.raises(MediaConflict):
        service.review_job(job["id"], ["operator", "operator"], key)
    with pytest.raises(MediaConflict):
        service.review_job(job["id"], ["client", "operator"], operation())
    with service.store.engine.connect() as connection:
        audit = connection.execute(select(media.transcription_reviews)).mappings().one()
        stored = connection.execute(select(media.transcription_jobs.c.result_payload)).scalar_one()
    assert audit["reviewer_id"] == "local-reviewer" and audit["request_hash"]
    assert '"speaker":"unknown"' in stored


@pytest.mark.parametrize("roles", [[], ["client"], ["unknown", "operator"], [True, "operator"], "client"])
def test_invalid_reviews_do_not_modify_state(service, roles):
    asset, job, claim = running(service)
    service.complete_job(job["id"], claim["lease_token"], asr(asset, unknown=True))
    with pytest.raises(MediaInvalid):
        service.review_job(job["id"], roles, operation())
    assert service.get_job(job["id"])["status"] == "needs_review"
    assert service.store.list_conversations() == []


def test_interrupted_leases_are_failed_not_requeued_and_survive_restart(service, monkeypatch):
    asset, job, claim = running(service)
    future = media._now() + timedelta(seconds=601)
    monkeypatch.setattr(media, "_now", lambda: future)
    with pytest.raises(MediaConflict):
        service.complete_job(job["id"], claim["lease_token"], asr(asset))
    assert service.recover_expired_jobs() == 1
    assert service.recover_expired_jobs() == 0
    restarted = MediaService(service.store, service.root)
    failed = restarted.get_job(job["id"])
    assert failed["status"] == "failed" and failed["error_code"] == "interrupted"
    assert restarted.claim_job(job["id"]) is None
    with pytest.raises(MediaConflict):
        restarted.complete_job(job["id"], claim["lease_token"], asr(asset))


def test_failed_job_sanitizes_error_and_releases_backlog(service):
    asset, job, claim = running(service)
    failed = service.fail_job(job["id"], claim["lease_token"], "SYNTHETIC_PRIVATE_DIAGNOSTIC")
    assert failed["status"] == "failed" and failed["error_code"] == "transcription_failed"
    assert failed["result"] is None and failed["conversation_id"] is None
    assert service.fail_job(job["id"], claim["lease_token"]) == failed
    assert service.create_job(asset["id"], "RU", operation())["status"] == "queued"


def test_missing_resources_and_bad_language_are_explicit(service):
    assert service.get_asset(operation()) is None and service.get_job(operation()) is None
    with pytest.raises(MediaNotFound):
        service.asset_path(operation())
    with pytest.raises(MediaNotFound):
        service.create_job(operation(), "RU", operation())
    with pytest.raises(MediaInvalid):
        service.create_job(operation(), "ru", operation())


def test_root_must_remain_in_backend(service):
    with pytest.raises(ValueError):
        MediaService(service.store, Path(service.root.anchor) / "outside-eva")
