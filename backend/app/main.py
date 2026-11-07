from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
import httpx
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .contracts import ContractError, Contracts
from .ml_client import MLClient, MLInvalidResponse, MLUnavailable
from .scoring import score
from .storage import EvaluationStore


class CreateEvaluation(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    conversation_id: str = Field(min_length=1)
    checklist_id: str = Field(min_length=1)
    checklist_version: int = Field(ge=1)


def create_app(settings: Settings | None = None,
               evaluator: Callable[[dict], Awaitable[dict]] | None = None) -> FastAPI:
    settings = settings or Settings()
    contracts = Contracts(settings.contracts_dir)
    store = EvaluationStore(settings.database_path)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        await run_in_threadpool(store.initialize)
        async with httpx.AsyncClient(
            base_url=settings.ml_url, timeout=httpx.Timeout(settings.ml_timeout_seconds),
            trust_env=False, follow_redirects=False,
        ) as client:
            application.state.evaluate = evaluator or MLClient(client).evaluate
            yield

    application = FastAPI(title="EVA local backend", version="0.1.0", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware, allow_origins=list(settings.cors_origins),
        allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["Content-Type"],
    )

    @application.get("/health")
    def health():
        return {"status": "ok", "service": "backend", "schema_version": "1.0.0"}

    @application.get("/api/v1/checklists")
    def checklists():
        return [contracts.example("checklist")]

    @application.get("/api/v1/conversations")
    def conversations():
        return [contracts.example("conversation")]

    @application.post("/api/v1/evaluations", status_code=201)
    async def create_evaluation(body: CreateEvaluation):
        contracts.validate("evaluation-create", body.model_dump())
        conversation = contracts.example("conversation")
        checklist = contracts.example("checklist")
        if body.conversation_id != conversation["id"] or body.checklist_id != checklist["id"]:
            raise HTTPException(404, "Conversation or checklist was not found.")
        if body.checklist_version != checklist["version"]:
            raise HTTPException(409, "Checklist version does not match the selected snapshot.")
        request = {"schema_version": "1.0.0", "conversation": conversation, "checklist": checklist}
        contracts.validate_request(request)
        try:
            original = await application.state.evaluate(request)
            contracts.validate_result(request, original)
        except MLUnavailable as error:
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
        await run_in_threadpool(store.create, record)
        return record

    @application.get("/api/v1/evaluations/{evaluation_id}")
    def get_evaluation(evaluation_id: str):
        record = store.get(evaluation_id)
        if record is None:
            raise HTTPException(404, "Evaluation was not found.")
        return record

    return application


app = create_app()
