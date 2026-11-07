"""EVA Review export, version comparison and audit evidence boundary."""
import asyncio
from copy import deepcopy
import hashlib
import json
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from fastapi import Body, HTTPException
from fastapi.concurrency import run_in_threadpool

from .contracts import ContractError
from .ml_client import MLInvalidResponse, MLUnavailable
from .scoring import score
from .storage import StorageConflict


def checked(contracts, request, bundle):
    try:
        contracts.validate("audit-bundle", bundle)
        response, audit = bundle["response"], bundle["audit"]
        contracts.validate_result(request, response)
        def fingerprint(value):
            document=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))
            return hashlib.sha256(document.encode()).hexdigest()
        versions=audit["versions"]
        if (versions["transcript"]!=fingerprint(request["conversation"])
                or versions["checklist"]!=fingerprint(request["checklist"]) or versions["model"]!=response["model"]
                or audit["target_operator"]!=request["conversation"].get("source",{}).get("target_operator_id")):
            raise ValueError("Audit belongs to a different source or configuration.")
        decisions={r["criterion_id"]:r for r in response["criteria"]}
        for key,detail in audit["criteria"].items():
            if key not in decisions or detail["status"]!=decisions[key]["status"] or detail["decision"]!=decisions[key]["value"]:
                raise ValueError("The explanation contradicts the decision.")
        messages = request["conversation"]["messages"]
        known = {m["id"]:(i,m) for i,m in enumerate(messages)}
        for detail in [*audit["criteria"].values(), *audit["sentinel"]]:
            for item in detail["evidence"]:
                pos,m = known[item["utterance_id"]]
                start,end=item["chars"]
                if not (0<=start<end<=len(m["text"]) and m["text"][start:end]==item["quote"]
                        and pos==item["order"] and item["timestamp"]==m["timestamp"]
                        and item["start"]==m.get("offset_seconds") and item["end"]==m.get("end_seconds")
                        and item["speaker_id"]==m.get("speaker_id")):
                    raise ValueError("Invalid evidence")
                role=m["speaker"]
                target=audit["target_operator"]
                if role=="operator" and target and m.get("speaker_id")!=target:
                    role="other_operator" if m.get("speaker_id") else "unknown"
                if role!=item["role"]: raise ValueError("Invalid speaker")
        return bundle
    except (ContractError, KeyError, IndexError, ValueError, TypeError) as exc:
        raise MLInvalidResponse("Invalid audit result") from exc


def delta(previous,current):
    old={r["id"]:r for r in previous["criteria"]}
    changes={}
    for new in current["criteria"]:
        before=old.get(new["id"])
        fields=[k for k in new if not before or before.get(k)!=new[k]]
        changes[new["id"]]={"fields":fields,"differences":{k:{"from":before.get(k) if before else None,"to":new[k]} for k in fields},
                            "evaluate":before is None or any(
            k not in {"weight","section_id"} for k in fields)}
    # Official dependent rules share system identity/disclosure facts. Conservatively
    # refresh both if either rule changes or disappears.
    pair={"identity","gross_errors"}
    if any(changes.get(k,{}).get("evaluate") or (k in old and k not in changes) for k in pair):
        for k in pair & changes.keys(): changes[k]["evaluate"]=True
    # Published policies may declare additional prerequisites. Follow the reverse
    # edges to a fixed point so transitive changes cannot reuse stale decisions.
    dirty={k for k,v in changes.items() if v["evaluate"]} | (old.keys()-changes.keys())
    while True:
        more={r["id"] for r in current["criteria"] if set(r.get("depends_on",[])) & dirty} - dirty
        if not more: break
        for key in more: changes[key]["evaluate"]=True
        dirty.update(more)
    return changes


def review_history(case):
    """Materialize before/after decisions from the immutable model output and ordered reviewer events."""
    previous={r["criterion_id"]:r for r in case["original"]["original"]["criteria"]}
    appeals={a["id"]:a for a in case["appeals"]}
    history=[]
    for decision in case["decisions"]:
        appeal=appeals[decision["appeal_id"]]
        key=appeal["criterion_id"]
        old=previous[key]
        new=decision["override"] if decision["decision"]=="accepted" else old
        history.append({"evaluation_id":case["evaluation_id"],"criterion_id":key,"decision_id":decision["id"],
                        "source":"reviewer","author":decision["reviewer_id"],"created_at":decision["created_at"],
                        "checklist_version":case["original"]["original"]["checklist_version"],
                        "before":old,"after":new,"reason":decision["reason"],"appeal_message_ids":appeal["message_ids"]})
        previous[key]=new
    return history


def install(app, contracts, store, settings):
    @app.get("/api/v1/evaluations/{eid}/audit")
    def export(eid:str) -> dict:
        if not settings.audit_review: raise HTTPException(404,"EVA Review disabled.")
        case=app.state.workflow.get_review_case(eid)
        snapshot=store.get_snapshot(eid)
        return {"format":"eva.review.v1","evaluation_id":eid,"snapshot":snapshot,
                "review":case,"history":review_history(case),"audit":store.get_audit(eid),
                "can_derive":snapshot is not None and settings.audit_mode!="baseline"}

    @app.post("/api/v1/evaluations/{eid}/versions/{version}",status_code=201)
    async def derive(eid:str,version:int, options:dict=Body(...)) -> dict:
        if options: raise HTTPException(422,"Unexpected derivation options.")
        if settings.audit_mode=="baseline" or not settings.audit_review: raise HTTPException(409,"Enable audit experiments.")
        snapshot=await run_in_threadpool(app.state.workflow.get_snapshot,eid)
        before=snapshot["checklist"]
        current=await run_in_threadpool(store.get_checklist,before["id"],version)
        if current is None: raise HTTPException(404,"Checklist version not found.")
        if version<=before["version"]: raise HTTPException(409,"Choose a later checklist version.")
        parent_review=await run_in_threadpool(app.state.workflow.get_review_case,eid)
        revision=parent_review["revision"]
        derived_id=str(uuid5(NAMESPACE_URL,f"eva:review:{eid}:revision:{revision}:version:{version}"))
        existing=await run_in_threadpool(store.get,derived_id)
        if existing is not None: return existing
        record=await run_in_threadpool(store.get,eid)
        request={**snapshot,"checklist":current}
        changes=delta(before,current)
        previous={r["criterion_id"]:r for r in record["original"]["criteria"]}
        changed=[r for r in current["criteria"] if changes[r["id"]]["evaluate"]]
        companion={"mode":"derived","parent":eid,"changes":changes,"removed":sorted(previous.keys()-changes.keys()),
                   "lineage":{k:("model" if v["evaluate"] else eid) for k,v in changes.items()},
                   "from_version":before["version"],"to_version":version}
        result=deepcopy(record["original"])
        if changed:
            subset={**request,"checklist":{**current,"criteria":changed}}
            try:
                async with asyncio.timeout(settings.ml_timeout_seconds):
                    async with app.state.ml_slots:
                        bundle=checked(contracts,subset,await app.state.audit_evaluate(subset))
            except (TimeoutError,MLUnavailable) as exc: raise HTTPException(503,"ML unavailable.") from exc
            except MLInvalidResponse as exc: raise HTTPException(502,"Invalid audit result.") from exc
            previous.update((r["criterion_id"],r) for r in bundle["response"]["criteria"])
            companion["candidate"]=bundle
            result.update({k:bundle["response"][k] for k in ("provider","model","warnings")})
        result.update(checklist_version=version,criteria=[previous[r["id"]] for r in current["criteria"]])
        contracts.validate_result(request,result)
        summary=score(request,result)
        # Preserve a reviewed projection without representing a human override as
        # an automatic label or inventing a reviewer action on the new assessment.
        reviewed={r["criterion_id"]:r for r in parent_review["final"]["criteria"]}
        projection={**result,"criteria":[previous[r["id"]] if changes[r["id"]]["evaluate"] else reviewed[r["id"]]
                                         for r in current["criteria"]]}
        contracts.validate_result(request,projection)
        original_rows={r["criterion_id"]:r for r in record["original"]["criteria"]}
        for key,change in changes.items():
            change["before_decision"]=original_rows.get(key)
            change["after_decision"]=previous[key]
        companion["review_projection"]={"parent_revision":revision,"criteria":projection["criteria"],
                                        "scoring":score(request,projection)}
        saved={"id":derived_id,"created_at":datetime.now(timezone.utc).isoformat(),"original":result,
               "scoring":summary,"status":"completed" if summary["status"]=="complete" else "needs_review"}
        contracts.validate("evaluation-record",saved)
        try:
            await run_in_threadpool(store.create,saved,request,companion)
        except StorageConflict:
            existing=await run_in_threadpool(store.get,derived_id)
            if existing is None: raise
            return existing
        return saved
