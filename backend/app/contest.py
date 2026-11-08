"""Local export of AUTOMATIC decisions in the Freedom submission appendix JSON 1.0."""
import json
from collections import Counter

from fastapi import HTTPException
from sqlalchemy import select

from .models import conversations
from .scoring import score

CODES={"contact":"contact","needs":"discovery","identity":"identification","consultation":"consultation",
       "empathy":"empathy","dialogue":"dialogue","closing":"closure","gross_errors":"critical"}

def invalid(reason):raise HTTPException(409,reason)
def failure(cid,why):
    return dict(conversation_id=cid,status="error",criteria=[],score=None,error=why)

def export(queue,jid,contracts):
    return export_manifest(queue.store,queue.get(jid),contracts)

def export_manifest(store,batch,contracts):
    group=batch["group_id"]
    if group.upper() not in {"DEV","PROD"}:invalid("Not an organizer DEV or PROD group.")
    if any(r["state"] not in {"done","failed"} for r in batch["items"]):invalid("Batch still has unfinished items.")
    with store.engine.connect() as c:
        raw={r.id:json.loads(r.raw_payload) if r.raw_payload else None for r in c.execute(
            select(conversations.c.id,conversations.c.raw_payload).where(conversations.c.group_id==group))}
    expected={"agent chat":60,"voice call":5} if group.upper()=="DEV" else {"agent chat":10}
    ids=[r["cid"] for r in batch["items"]]
    if set(ids)!=set(raw) or len(set(ids))!=len(ids) or any(not v or v.get("conversation_id")!=k for k,v in raw.items()):
        invalid("Dataset manifest or original sources are incomplete.")
    if Counter(v["channel"] for v in raw.values())!=expected:invalid("Unexpected number of organizer chats or calls.")
    official=contracts.example("checklist")
    if store.get_checklist(batch["checklist_id"],batch["version"])!=official:invalid("Not the verified Freedom checklist.")
    results=[];models=set()
    for item in batch["items"]:
        cid=item["cid"]
        if item["state"]=="failed":
            results.append(failure(cid,item["error"] or "Ошибка обработки."));continue
        record=store.get(item["id"]); snapshot=store.get_snapshot(item["id"])
        if not record or not snapshot or snapshot["conversation"]["id"]!=cid or snapshot["checklist"]!=official:
            invalid("Result belongs to a different source or checklist.")
        model=record["original"]
        if model["provider"]!="llama.cpp" or any(s in model["model"].lower() for s in ("stub","fixture","mock","demo")):
            invalid("Only recorded real local model results may be exported.")
        contracts.validate_result(snapshot,model)
        models.add(model["model"])
        transcript=raw[cid]["transcript"];messages=snapshot["conversation"]["messages"]
        if len(transcript)!=len(messages) or any(any(m[k]!=src[k] for k in ("speaker","text","timestamp"))
                for m,src in zip(messages,transcript)):invalid("Source turn order or text changed.")
        summary=score(snapshot,model)
        if summary!=record["scoring"]:invalid("Stored score differs from recalculation.")
        if summary["status"]!="complete":
            results.append(failure(cid,"Есть неразрешённые критерии или итог не вычисляется."));continue
        positions={m["id"]:i for i,m in enumerate(messages,1)}
        by_id={r["criterion_id"]:r for r in model["criteria"]}
        rows=[]
        for key,code in CODES.items():
            r=by_id[key]; refs=sorted({positions[mid] for e in r["evidence"] for mid in e["message_ids"]})
            if not refs or not r["reason"].strip():invalid("Missing rationale or evidence.")
            rows.append(dict(criterion_id=code,value=r["value"],rationale=r["reason"],evidence_turns=refs))
        results.append(dict(conversation_id=cid,status="ok",criteria=rows,score=dict(final_score=summary["total"],
            critical_flag=summary["has_gross_error"])))
    if not models:invalid("No actual model identity is available for this run.")
    return dict(schema_version="1.0",team_name="EVA",model=dict(mode="local",name=", ".join(sorted(models))),results=results)
