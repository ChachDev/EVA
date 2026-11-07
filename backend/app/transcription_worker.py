"""One durable local ASR worker per backend process, coordinated by DB leases."""

import asyncio
import logging
import math

import httpx
from fastapi.concurrency import run_in_threadpool

from .media import MediaConflict, MediaInvalid
from .transcription_client import TranscriptionClient, TranscriptionInvalidResponse, TranscriptionUnavailable


logger = logging.getLogger(__name__)


async def process_next_transcription(media, client, *, timeout_seconds=300):
    job = await run_in_threadpool(media.claim_job, lease_seconds=math.ceil(timeout_seconds) + 60)
    if job is None:
        return None
    job_id, lease = job["id"], job["lease_token"]
    try:
        asset = await run_in_threadpool(media.get_asset, job["asset_id"])
        path = await run_in_threadpool(media.asset_path, job["asset_id"])
        async with asyncio.timeout(timeout_seconds):
            result = await client.transcribe(path, asset["content_type"], job["language"], asset["sha256"])
        return await run_in_threadpool(media.complete_job, job_id, lease, result)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(run_in_threadpool(media.fail_job, job_id, lease, "interrupted"))
        except Exception:
            pass  # An expired lease is recovered on the next worker cycle.
        raise
    except Exception as error:
        code = "invalid_transcript" if isinstance(error, (MediaInvalid, TranscriptionInvalidResponse)) else "transcription_failed"
        if isinstance(error, TimeoutError):
            code = "transcription_timeout"
        elif isinstance(error, TranscriptionUnavailable):
            code = "asr_unavailable"
        try:
            return await run_in_threadpool(media.fail_job, job_id, lease, code)
        except MediaConflict:
            return None  # A recovered/expired lease must never overwrite another result.


async def run_transcription_worker(media, settings):
    if settings.asr_url is None:
        return
    async with httpx.AsyncClient(base_url=settings.asr_url, trust_env=False, follow_redirects=False,
                                 timeout=settings.asr_timeout_seconds) as transport:
        client = TranscriptionClient(transport, timeout_seconds=settings.asr_timeout_seconds,
                                     max_audio_bytes=settings.audio_max_bytes)
        while True:
            try:
                await run_in_threadpool(media.recover_expired_jobs)
                result = await process_next_transcription(media, client,
                                                          timeout_seconds=settings.asr_timeout_seconds)
                if result is not None:
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never log exception values: upstream errors may include client data.
                logger.warning("Transcription worker operation failed; retrying the queue scan.")
            await asyncio.sleep(1)
