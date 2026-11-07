from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4
import asyncio

from alembic.util.exc import CommandError
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from .config import Settings
from .contracts import ContractError, Contracts
from .ml_client import MLClient, MLInvalidResponse, MLUnavailable
from .scoring import score
from .storage import EvaluationStore, StorageConflict


class CreateEvaluation(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    conversation_id: str = Field(min_length=1)
    checklist_id: str = Field(min_length=1)
    checklist_version: int = Field(ge=1)


def create_app(settings: Settings | None = None,
               evaluator: Callable[[dict], Awaitable[dict]] | None = None) -> FastAPI:
    settings = settings or Settings()
    contracts = Contracts(settings.contracts_dir)
    store = EvaluationStore(settings.effective_database_url)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            try:
                await run_in_threadpool(store.initialize)
                if settings.seed_demo:
                    await run_in_threadpool(store.seed_checklist, contracts.example("checklist"))
                    await run_in_threadpool(store.import_conversations, "synthetic-demo", [contracts.example("conversation")])
            except (SQLAlchemyError, StorageConflict, RuntimeError, CommandError):
                raise RuntimeError("Database startup failed. Check the local database and migrations.") from None
            async with httpx.AsyncClient(
                base_url=settings.ml_url, timeout=httpx.Timeout(settings.ml_timeout_seconds),
                trust_env=False, follow_redirects=False,
            ) as client:
                application.state.evaluate = evaluator or MLClient(client).evaluate
                application.state.ml_slots = asyncio.Semaphore(settings.ml_max_concurrency)
                yield
        finally:
            await run_in_threadpool(store.close)

    application = FastAPI(title="EVA local backend", version="0.1.0", lifespan=lifespan)
    application.state.store = store

    @application.exception_handler(SQLAlchemyError)
    async def database_error(_request, _error):
        return JSONResponse(status_code=503, content={"detail": "Database operation failed. Please retry later."})

    @application.exception_handler(StorageConflict)
    async def storage_conflict(_request, _error):
        return JSONResponse(status_code=409, content={"detail": "Stored data conflicts with this operation."})
    application.add_middleware(
        CORSMiddleware, allow_origins=list(settings.cors_origins),
        allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["Content-Type"],
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
    async def create_evaluation(body: CreateEvaluation):
        contracts.validate("evaluation-create", body.model_dump())
        conversation = await run_in_threadpool(store.get_conversation, body.conversation_id)
        checklist = await run_in_threadpool(store.get_checklist, body.checklist_id, body.checklist_version)
        if conversation is None:
            raise HTTPException(404, "Conversation or checklist was not found.")
        if checklist is None:
            available = await run_in_threadpool(store.list_checklists)
            if any(item["id"] == body.checklist_id for item in available):
                raise HTTPException(409, "Checklist version does not match a stored version.")
            raise HTTPException(404, "Conversation or checklist was not found.")
        request = {"schema_version": "1.0.0", "conversation": conversation, "checklist": checklist}
        contracts.validate_request(request)
        try:
            # Include waiting for a slot in the wall-clock budget. Keep DB transactions
            # closed throughout inference and preserve the exact input for later review.
            async with asyncio.timeout(settings.ml_timeout_seconds):
                async with application.state.ml_slots:
                    original = await application.state.evaluate(deepcopy(request))
            contracts.validate_result(request, original)
        except (MLUnavailable, TimeoutError) as error:
            raise HTTPException(503, "ML service is unavailable. No evaluation was saved.") from error
        except (MLInvalidResponse, ContractError) as error:
            raise HTTPException(502, "ML returned an invalid result. No evaluation was saved.") from error
        scoring = score(request, original)
        contracts.validate("score-summary", scoring)
        record = {
            "id": str(uuid4()),
            "status": "completed" if scoring["status"] == "complete" else "needs_review",
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "original": original,
            "scoring": scoring,
        }
        contracts.validate("evaluation-record", record)
        await run_in_threadpool(store.create, record, request_snapshot=request)
        return record

    @application.get("/api/v1/evaluations/{evaluation_id}")
    def get_evaluation(evaluation_id: str):
        record = store.get(evaluation_id)
        if record is None:
            raise HTTPException(404, "Evaluation was not found.")
        return record

    return application


app = create_app()
