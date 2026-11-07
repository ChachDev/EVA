"""Local audio intake and durable transcription jobs."""

from functools import partial
from typing import Annotated, Any

from fastapi import Body, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from starlette.responses import FileResponse

from .contracts import ContractError
from .media import (
    MediaService, MediaInvalid, MediaNotFound, MediaConflict,
    MediaStorageError, MediaBacklogFull,
)


def install_media_routes(application, contracts, store, settings):
    media = MediaService(store, settings.media_root, max_upload_bytes=settings.audio_max_bytes,
                         media_base_url="http://127.0.0.1:8000")
    application.state.media = media

    async def call(method, *args, **kwargs):
        try:
            return await run_in_threadpool(partial(method, *args, **kwargs))
        except MediaNotFound:
            raise HTTPException(404, "Audio asset or transcription was not found.") from None
        except MediaConflict:
            raise HTTPException(409, "Operation conflicts with the current transcription or asset.") from None
        except MediaBacklogFull:
            raise HTTPException(503, "Transcription capacity is full. Please retry later.",
                                headers={"Retry-After": "5"}) from None
        except MediaStorageError:
            raise HTTPException(503, "Local audio storage is unavailable.") from None
        except MediaInvalid:
            raise HTTPException(422, "Invalid audio or transcription input.") from None

    def validate(name, value):
        try:
            contracts.validate(name, value)
        except ContractError:
            raise HTTPException(422, "Request does not satisfy its contract.") from None

    @application.post("/api/v1/audio-assets", status_code=201,
                      summary="Upload a local audio recording; Idempotency-Key must be a UUID")
    async def upload_audio(request: Request, idempotency_key: Annotated[str, Header()]):
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"audio/wav", "audio/x-wav", "audio/mpeg", "audio/flac", "audio/ogg"}:
            raise HTTPException(415, "Supported audio types: WAV, MP3, FLAC and Ogg.")
        asset = await call(media.upload, await request.body(), content_type, idempotency_key)
        contracts.validate("audio-asset", asset)
        return asset

    @application.get("/api/v1/audio-assets/{asset_id}")
    async def audio_metadata(asset_id: str):
        asset = await call(media.get_asset, asset_id)
        if asset is None:
            raise HTTPException(404, "Audio asset was not found.")
        return asset

    @application.get("/api/v1/audio-assets/{asset_id}/content")
    async def audio_content(asset_id: str):
        asset = await call(media.get_asset, asset_id)
        if asset is None:
            raise HTTPException(404, "Audio asset was not found.")
        path = await call(media.asset_path, asset_id)
        return FileResponse(
            path, media_type=asset["content_type"], filename=f"audio-{asset['id']}{path.suffix}",
            content_disposition_type="inline",
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
                     "ETag": '"' + asset["sha256"] + '"'},
        )

    @application.post("/api/v1/transcriptions", status_code=202)
    async def create_transcription(body: Annotated[Any, Body()]):
        validate("transcription-create", body)
        if settings.asr_url is None:
            raise HTTPException(503, "Local speech recognition is not configured. The audio asset remains saved.")
        job = await call(media.create_job, body["asset_id"], body["language"], body["operation_id"])
        contracts.validate("transcription-job", job)
        return job

    @application.get("/api/v1/transcriptions/{job_id}")
    async def get_transcription(job_id: str):
        job = await call(media.get_job, job_id)
        if job is None:
            raise HTTPException(404, "Transcription was not found.")
        return job

    @application.post("/api/v1/transcriptions/{job_id}/review")
    async def review_transcription(job_id: str, body: Annotated[Any, Body()]):
        validate("transcription-review", body)
        job = await call(media.review_job, job_id, body["roles"], body["operation_id"],
                         reviewer_id=settings.reviewer_id)
        contracts.validate("transcription-job", job)
        return job
