"""Crash recovery, blind data boundary and organizer JSON on synthetic records."""
from copy import deepcopy
import time
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import update

from app.batches import BatchStore,Start,items
from app.config import Settings
from app.main import create_app
from app.ml_client import MLUnavailable
from app.storage import StorageConflict

def seed(store,contracts,group="PROD",n=10):
    check=contracts.example("checklist")
    store.seed_checklist(check)
    conv=[];raw=[]
    for i in range(n):
        c=contracts.example("conversation");c["id"]=f"test-{i:02d}"
        conv.append(c);raw.append(dict(conversation_id=c["id"],channel="agent chat",language="RU",audio_url=None,
            transcript=[{k:m[k] for k in ("speaker","text","timestamp")} for m in c["messages"]]))
    store.import_conversations(group,conv,raw_records=raw)
    return dict(group_id=group,checklist_id=check["id"],checklist_version=check["version"])

def terminal(client,url):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        b=client.get(url).json()
        if not (b["counts"].get("queued") or b["counts"].get("running")):return b
        time.sleep(.1)
    pytest.fail("Batch did not reach terminal state")

def test_expired_lease_reclaims_only_unfinished(tmp_path,contracts):
    app=create_app(Settings(database_path=tmp_path/"lease.db"),start_workers=False)
    with TestClient(app):
        queue=BatchStore(app.state.store);body=Start(**seed(queue.store,contracts,n=2))
        jid=queue.start(body,"baseline")["id"];old=queue.claim("baseline",60)
        assert queue.start(body,"baseline")["id"]==jid and queue.claim("shadow",60) is None
        with queue.store._write_connection() as c:
            c.execute(update(items).where(items.c.id==old["id"]).values(lease=0))
        new=queue.claim("baseline",60)
        assert old["id"]==new["id"] and old["token"]!=new["token"]
        with pytest.raises(StorageConflict):queue.finish(old,"done")
        queue.finish(new,"failed","test failure")
        assert queue.get(jid)["counts"]=={"failed":1,"queued":1}

def test_restart_retry_blind_export_and_stub_guard(tmp_path,contracts):
    settings=Settings(database_path=tmp_path/"roundtrip.db")
    bad={"test-01"};calls=[]
    async def evaluator(req):
        cid=req["conversation"]["id"];calls.append(cid)
        if cid in bad:raise MLUnavailable()
        r=contracts.example("evaluation-response");r.update(conversation_id=cid,provider="llama.cpp",model="test-harness-real-provider-contract")
        return r
    app=create_app(settings,evaluator,start_workers=False)
    with TestClient(app) as client:
        body=seed(app.state.store,contracts)
        b=client.post("/api/v1/batches",json=body).json();url="/api/v1/batches/"+b["id"]
        assert b["counts"]=={"queued":10} and client.get(url+"/results").status_code==409
    # Restart with a worker. Closing the client above did not discard the job.
    with TestClient(create_app(settings,evaluator)) as client:
        b=terminal(client,url)
        assert b["counts"]=={"done":9,"failed":1}
        export=client.get(url+"/results");assert export.status_code==200,export.text
        file=export.json()
        assert len(file["results"])==10 and file["results"][1]["status"]=="error"
        first=file["results"][0];assert first["criteria"][0]["evidence_turns"]==[1,2]
        assert [x["criterion_id"] for x in first["criteria"]]==["contact","discovery","identification","consultation","empathy","dialogue","closure","critical"]
        assert first["score"]["critical_flag"] is False
        eid=b["items"][0]["id"];href="/api/v1/blind/"+eid
        assert set(client.get(href).json())=={"checklist","conversation"}
        answers={r["criterion_id"]:r["value"] for r in contracts.example("evaluation-response")["criteria"]}
        label={"reviewer":"synthetic-reviewer","answers":answers}
        assert client.post(href,json=label).status_code==200 and client.post(href,json=label).status_code==200
        assert client.get(url+"/quality").json()["submissions"]==1
        assert client.post(href,json={**label,"answers":{**answers,"contact":False}}).status_code==409
        assert client.post(href,json={**label,"answers":{**answers,"contact":1}}).status_code==422
        bad.clear();assert client.post(url+"/retry",json={}).status_code==200
        assert terminal(client,url)["counts"]=={"done":10}
        assert len(calls)==11 and calls.count("test-01")==2
        assert client.post("/api/v1/batches",json=body).json()["id"]==b["id"]
        # Tampered/test provenance is blocked before returning any export.
        from app.models import evaluations
        from app.storage import _document
        store=client.app.state.store; rec=store.get(eid);rec["original"]["provider"]="fixture"
        with store._write_connection() as c:c.execute(update(evaluations).where(evaluations.c.id==eid).values(payload=_document(rec)))
        assert client.get(url+"/results").status_code==409
