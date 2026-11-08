from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4
import asyncio

from alembic.util.exc import CommandError
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from . import audit as audit_api
from .config import Settings
from .contracts import ContractError, Contracts
from .ml_client import MLClient, MLContextLimitExceeded, MLInvalidResponse, MLUnavailable
from .scoring import score
from .storage import EvaluationStore, StorageConflict
from .http_boundary import (
    RequestBoundaryMiddleware, sanitized_validation_exception_handler,
    sanitized_server_error_handler,
)
from .workflow_api import install_workflow_routes
from .media_api import install_media_routes
from .transcription_worker import run_transcription_worker
from .openapi import install_contract_openapi


class CreateEvaluation(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    conversation_id: str = Field(min_length=1)
    checklist_id: str = Field(min_length=1)
    checklist_version: int = Field(ge=1)


def create_app(settings: Settings | None = None,
               evaluator: Callable[[dict], Awaitable[dict]] | None = None,
               *, start_workers: bool = True) -> FastAPI:
    settings = settings or Settings()
    contracts = Contracts(settings.contracts_dir)
    store = EvaluationStore(settings.effective_database_url)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            try:
                await run_in_threadpool(store.initialize)
                if settings.audit_mode != "baseline":
                    await run_in_threadpool(store.initialize_audit)
                if settings.seed_demo:
                    await run_in_threadpool(store.seed_checklist, contracts.example("checklist"))
                    await run_in_threadpool(store.import_conversations, "synthetic-demo", [contracts.example("conversation")])
            except (SQLAlchemyError, StorageConflict, RuntimeError, CommandError):
                raise RuntimeError("Database startup failed. Check the local database and migrations.") from None
            async with httpx.AsyncClient(
                base_url=settings.ml_url, timeout=httpx.Timeout(settings.ml_timeout_seconds),
                trust_env=False, follow_redirects=False,
            ) as client:
                transport = MLClient(client, max_response_bytes=settings.ml_max_response_bytes)
                application.state.evaluate = evaluator or transport.evaluate
                application.state.audit_evaluate = transport.audit
                application.state.audit_shadow = transport.audit_shadow
                application.state.ml_slots = asyncio.Semaphore(settings.ml_max_concurrency)
                application.state.evaluation_requests = asyncio.Queue(
                    maxsize=settings.ml_max_concurrency + settings.evaluation_queue_capacity,
                )
                worker = (asyncio.create_task(run_transcription_worker(application.state.media, settings))
                          if start_workers and settings.asr_url else None)
                try:
                    yield
                finally:
                    if worker is not None:
                        worker.cancel()
                        with suppress(asyncio.CancelledError):
                            await worker
        finally:
            await run_in_threadpool(store.close)

    application = FastAPI(title="EVA local backend", version="0.1.0", lifespan=lifespan)
    application.state.store = store
    application.add_exception_handler(RequestValidationError, sanitized_validation_exception_handler)
    application.add_exception_handler(Exception, sanitized_server_error_handler)

    @application.exception_handler(SQLAlchemyError)
    async def database_error(_request, _error):
        return JSONResponse(status_code=503, content={"detail": "Database operation failed. Please retry later."})

    @application.exception_handler(StorageConflict)
    async def storage_conflict(_request, _error):
        return JSONResponse(status_code=409, content={"detail": "Stored data conflicts with this operation."})
    application.add_middleware(
        RequestBoundaryMiddleware, max_body_bytes=settings.request_max_bytes,
        path_body_limits={"/api/v1/audio-assets": settings.audio_max_bytes},
    )
    application.add_middleware(
        CORSMiddleware, allow_origins=list(settings.cors_origins),
        allow_credentials=False, allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key", "Range", "If-Range"],
        expose_headers=["X-Request-ID", "Content-Range", "Accept-Ranges", "ETag", "Retry-After"],
    )

    @application.get("/health")
    def health():
        return {"status": "ok", "service": "backend", "schema_version": "1.0.0"}

    @application.get("/api/v1/checklists")
    def checklists():
        return store.list_checklists()

    @application.get("/api/v1/conversations")
    def conversations():
        return store.list_conversations()

    @application.post("/api/v1/evaluations", status_code=201)
    async def create_evaluation(body: CreateEvaluation, background_tasks: BackgroundTasks):
        try:
            application.state.evaluation_requests.put_nowait(None)
        except asyncio.QueueFull:
            raise HTTPException(503, "Evaluation capacity is full. Please retry later.",
                                headers={"Retry-After": "5"}) from None
        try:
            return await evaluate_and_store(body, background_tasks)
        finally:
            application.state.evaluation_requests.get_nowait()

    async def shadow(record_id, request, baseline):
        sidecar = {"mode":"shadow"}
        try:
            async with asyncio.timeout(settings.ml_timeout_seconds):
                async with application.state.ml_slots:
                    bundle = audit_api.checked(contracts, request, await application.state.audit_shadow(request,baseline))
                    verification=bundle["audit"].get("verification")
                    if verification is not None and verification["baseline"]!=baseline:
                        raise MLInvalidResponse("Verification used a different primary result.")
            sidecar.update(status="completed", candidate=bundle, scoring=score(request,bundle["response"]))
        except (MLInvalidResponse, MLUnavailable, TimeoutError, MLContextLimitExceeded):
            sidecar.update(status="failed",reason="Candidate unavailable or invalid.")
        await run_in_threadpool(store.save_shadow,record_id,sidecar)

    async def evaluate_and_store(body: CreateEvaluation, background_tasks: BackgroundTasks):
        contracts.validate("evaluation-create", body.model_dump())
        conversation = await run_in_threadpool(store.get_conversation, body.conversation_id)
        checklist = await run_in_threadpool(store.get_checklist, body.checklist_id, body.checklist_version)
        if conversation is None:
            raise HTTPException(404, "Conversation or checklist was not found.")
        if checklist is None:
            if await run_in_threadpool(store.has_checklist, body.checklist_id):
                raise HTTPException(409, "Checklist version does not match a stored version.")
            raise HTTPException(404, "Conversation or checklist was not found.")
        request = {"schema_version": "1.0.0", "conversation": conversation, "checklist": checklist}
        try:
            contracts.validate_request(request)
        except ContractError:
            raise HTTPException(409, "Stored inputs do not satisfy the evaluation contract.") from None
        try:
            # Include waiting for a slot in the wall-clock budget. Keep DB transactions
            # closed throughout inference and preserve the exact input for later review.
            async with asyncio.timeout(settings.ml_timeout_seconds):
                async with application.state.ml_slots:
                    companion = None
                    if settings.audit_mode == "experiment":
                        bundle = audit_api.checked(contracts,request,await application.state.audit_evaluate(deepcopy(request)))
                        original = bundle["response"]
                        companion = {"mode":"experiment","candidate":bundle}
                    else:
                        original = await application.state.evaluate(deepcopy(request))
                        if settings.audit_mode == "shadow":
                            companion = {"mode":"shadow","status":"pending"}
            contracts.validate_result(request, original)
        except MLContextLimitExceeded:
            return JSONResponse(status_code=422, content={
                "detail": "Conversation and checklist exceed the local model context. No evaluation was saved.",
                "code": "ml_context_limit_exceeded",
            })
        except (MLUnavailable, TimeoutError) as error:
            raise HTTPException(503, "ML service is unavailable. No evaluation was saved.") from error
        except (MLInvalidResponse, ContractError) as error:
            raise HTTPException(502, "ML returned an invalid result. No evaluation was saved.") from error
        scoring = score(request, original)
        contracts.validate("score-summary", scoring)
        record = {
            "id": str(uuid4()),
            "status": "completed" if scoring["status"] == "complete" else "needs_review",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "original": original,
            "scoring": scoring,
        }
        contracts.validate("evaluation-record", record)
        await run_in_threadpool(store.create, record, request_snapshot=request, audit=companion)
        if settings.audit_mode == "shadow":
            background_tasks.add_task(shadow,record["id"],request,original)
        return record

    install_workflow_routes(application, contracts, store, settings)
    audit_api.install(application,contracts,store,settings)
    install_media_routes(application, contracts, store, settings)

    @application.get("/api/v1/evaluations/{evaluation_id}")
    def get_evaluation(evaluation_id: str):
        record = store.get(evaluation_id)
        if record is None:
            raise HTTPException(404, "Evaluation was not found.")
        return record

    install_contract_openapi(application, contracts)
    return application


app = create_app()
