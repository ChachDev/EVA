"""Remove a selected workspace record and its dependent history in one transaction."""
import json

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete, inspect, select, update
from starlette.responses import Response

from .batches import guard, items, jobs
from .blind import labels
from .media import transcription_jobs
from .models import (appeals, audit_runs, checklist_versions, conversations, evaluation_inputs, evaluations,
                     operation_receipts, review_cases, review_decisions)


def remove(store, kind, key, version=None):
    with store._write_connection() as db:
        guard(db, store)
        if kind == "checklist":
            if key == "freedom-cqa" and version == 1:
                raise HTTPException(409, "The base Freedom checklist is required.")
            in_flight = db.execute(select(items.c.id).join(jobs, jobs.c.id == items.c.job).where(
                jobs.c.checklist_id == key, jobs.c.version == version, items.c.state.in_(("queued", "running"))).limit(1)).first()
            if in_flight:
                raise HTTPException(409, "An active batch uses this checklist.")
            found = db.execute(update(checklist_versions).where(checklist_versions.c.checklist_id == key,
                checklist_versions.c.version == version).values(archived=True)).rowcount
            if not found:
                raise HTTPException(404, "Checklist version not found.")
            return

        if kind == "conversation":
            if not db.execute(select(conversations.c.id).where(conversations.c.id == key).with_for_update()).first():
                raise HTTPException(404, "Conversation not found.")
            condition = evaluations.c.conversation_id == key
            batch_condition = items.c.cid == key
        else:
            condition = evaluations.c.id == key
            batch_condition = items.c.id == key
        ids = set(db.execute(select(evaluations.c.id).where(condition).with_for_update()).scalars())
        if kind == "evaluation" and not ids:
            raise HTTPException(404, "Evaluation not found.")
        batch = db.execute(select(items.c.id, items.c.state).where(batch_condition).with_for_update()).all()
        if any(item.state in ("queued", "running") for item in batch):
            raise HTTPException(409, "A batch is still processing this conversation.")
        if batch:
            item_ids = [item.id for item in batch]
            db.execute(delete(labels).where(labels.c.eid.in_(item_ids)))
            db.execute(update(items).where(items.c.id.in_(item_ids)).values(state="failed", error=f"{kind}_deleted"))
        if ids:
            receipts = db.execute(select(operation_receipts.c.operation_id, operation_receipts.c.response_payload))
            expired = [r.operation_id for r in receipts if json.loads(r.response_payload).get("evaluation_id") in ids]
            if expired:
                db.execute(delete(operation_receipts).where(operation_receipts.c.operation_id.in_(expired)))
            for table in (review_decisions, appeals, review_cases, evaluation_inputs):
                db.execute(delete(table).where(table.c.evaluation_id.in_(ids)))
            if inspect(db).has_table(audit_runs.name):
                db.execute(delete(audit_runs).where(audit_runs.c.evaluation_id.in_(ids)))
            db.execute(delete(evaluations).where(evaluations.c.id.in_(ids)))
        if kind == "conversation":
            db.execute(update(transcription_jobs).where(transcription_jobs.c.conversation_id == key).values(conversation_id=None))
            db.execute(delete(conversations).where(conversations.c.id == key))


def install(application, store):
    @application.delete("/api/v1/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(conversation_id: str):
        await run_in_threadpool(remove, store, "conversation", conversation_id)
        return Response(status_code=204)

    @application.delete("/api/v1/evaluations/{evaluation_id}", status_code=204)
    async def delete_evaluation(evaluation_id: str):
        await run_in_threadpool(remove, store, "evaluation", evaluation_id)
        return Response(status_code=204)

    @application.delete("/api/v1/checklists/{checklist_id}/versions/{version}", status_code=204)
    async def delete_checklist(checklist_id: str, version: int):
        await run_in_threadpool(remove, store, "checklist", checklist_id, version)
        return Response(status_code=204)
