"""HTTP adapters for versioned checklists and append-only review workflows."""

from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from .contracts import ContractError, Contracts
from .importer import DatasetImportError, normalize_dataset
from .storage import EvaluationStore, StorageConflict
from .workflows import WorkflowConflict, WorkflowInvalid, WorkflowNotFound, WorkflowService


_MANUAL_NAMESPACE = uuid5(NAMESPACE_URL, "https://eva.local/conversations/manual")
_SOURCE_CHANNELS = {"agent_chat": "agent chat", "voice_call": "voice call"}


def _safe_handler(status_code: int, detail: str):
    async def handler(_request, _error):
        return JSONResponse(status_code=status_code, content={"detail": detail})
    return handler


def install_workflow_routes(application: FastAPI, contracts: Contracts,
                            store: EvaluationStore, settings) -> None:
    """Install routes once, sharing the application's existing store and lifespan."""
    service = WorkflowService(store, contracts, reviewer_id=settings.reviewer_id)
    application.state.workflow = service
    for error, status, detail in (
        (WorkflowNotFound, 404, "Requested workflow data was not found."),
        (WorkflowConflict, 409, "The operation conflicts with the current stored revision."),
        (WorkflowInvalid, 422, "Workflow request validation failed."),
        (ContractError, 422, "Request does not match the supported contract."),
        (DatasetImportError, 422, "Conversation input could not be normalized."),
    ):
        application.add_exception_handler(error, _safe_handler(status, detail))

    @application.post("/api/v1/conversations", status_code=201,
                      summary="Create an idempotent normalized conversation")
    async def create_conversation(body: dict = Body(...)):
        contracts.validate("conversation-create", body)
        identifier = str(uuid5(_MANUAL_NAMESPACE, str(UUID(body["operation_id"]))))
        envelope = {"error": None, "isSuccess": True, "data": {
            "dataset_group_id": "manual", "total_records": 1, "records": [{
                "conversation_id": identifier,
                "channel": _SOURCE_CHANNELS[body["channel"]], "language": body["language"],
                "transcript": body["messages"], "audio_url": None,
            }],
        }}
        normalized = await run_in_threadpool(normalize_dataset, envelope, contracts)
        try:
            await run_in_threadpool(store.import_conversations, normalized.group_id,
                                    normalized.conversations, raw_records=normalized.raw_records)
        except StorageConflict:
            raise HTTPException(409, "The operation identity already belongs to different conversation content.") from None
        return normalized.conversations[0]

    @application.get("/api/v1/conversations/{conversation_id}", summary="Read a saved conversation")
    async def get_conversation(conversation_id: str):
        conversation = await run_in_threadpool(store.get_conversation, conversation_id)
        if conversation is None:
            raise WorkflowNotFound("Conversation was not found.")
        return conversation

    @application.post("/api/v1/checklists/{checklist_id}/versions", status_code=201,
                      summary="Publish a new immutable checklist version")
    async def publish_checklist(checklist_id: str, body: dict = Body(...)):
        contracts.validate("checklist-publish", body)
        return await run_in_threadpool(service.publish_checklist, checklist_id,
                                       base_version=body["base_version"], name=body["name"],
                                       criteria=body["criteria"])

    @application.get("/api/v1/evaluations", summary="List saved evaluations with a stable cursor")
    async def list_evaluations(conversation_id: str | None = Query(default=None, min_length=1),
                               limit: int = Query(default=25, ge=1, le=100),
                               cursor: str | None = Query(default=None, min_length=1)):
        return await run_in_threadpool(service.list_evaluations, conversation_id=conversation_id,
                                       limit=limit, cursor=cursor)

    @application.get("/api/v1/evaluations/{evaluation_id}/snapshot",
                     summary="Read the immutable inputs of an evaluation")
    async def get_snapshot(evaluation_id: str):
        return await run_in_threadpool(service.get_snapshot, evaluation_id)

    @application.get("/api/v1/review-cases/{evaluation_id}",
                     summary="Read original, review history and final result")
    async def get_review_case(evaluation_id: str):
        return await run_in_threadpool(service.get_review_case, evaluation_id)

    @application.post("/api/v1/evaluations/{evaluation_id}/appeals", status_code=201,
                      summary="Appeal one criterion at an expected review revision")
    async def create_appeal(evaluation_id: str, body: dict = Body(...)):
        contracts.validate("appeal-create", body)
        return await run_in_threadpool(service.create_appeal, evaluation_id,
                                       operation_id=body["operation_id"], expected_revision=body["expected_revision"],
                                       criterion_id=body["criterion_id"], reason=body["reason"],
                                       message_ids=body["message_ids"])

    @application.post("/api/v1/appeals/{appeal_id}/decisions", status_code=201,
                      summary="Accept or reject an appeal without rewriting the original")
    async def decide_appeal(appeal_id: str, body: dict = Body(...)):
        contracts.validate("appeal-decision", body)
        return await run_in_threadpool(service.decide_appeal, appeal_id,
                                       operation_id=body["operation_id"], expected_revision=body["expected_revision"],
                                       decision=body["decision"], reason=body["reason"], override=body.get("override"))
