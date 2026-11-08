"""Persistent, leased work over an immutable imported group. No transcript in job metadata."""
import asyncio
import time
from collections import Counter
from typing import Literal
from uuid import uuid4, uuid5, NAMESPACE_URL

from fastapi import BackgroundTasks, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Column, Integer, MetaData, Table, Text, insert, or_, select, update
from sqlalchemy.exc import SQLAlchemyError

from .models import conversations
from .storage import StorageConflict

meta=MetaData()
jobs=Table("eva_batches",meta,Column("id",Text,primary_key=True),Column("group_id",Text,nullable=False),
    Column("checklist_id",Text,nullable=False),Column("version",Integer,nullable=False),Column("mode",Text,nullable=False),
    Column("revision",Text,nullable=False),
    Column("created",Integer,nullable=False))
items=Table("eva_batch_items",meta,Column("id",Text,primary_key=True),Column("job",Text,nullable=False,index=True),
    Column("cid",Text,nullable=False),Column("state",Text,nullable=False,index=True),Column("lease",Integer),
    Column("token",Text),Column("attempt",Integer,nullable=False),Column("error",Text))

class Start(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    group_id:str=Field(min_length=1,max_length=255)
    checklist_id:str=Field(min_length=1,max_length=255)
    checklist_version:int=Field(ge=1)

class Retry(BaseModel):
    model_config=ConfigDict(extra="forbid")

class BatchItem(BaseModel):
    id:str
    cid:str
    state:Literal["queued","running","done","failed"]
    attempt:int
    error:str|None

class Batch(BaseModel):
    id:str
    group_id:str
    checklist_id:str
    version:int
    mode:str
    revision:str
    created:int
    counts:dict[str,int]
    items:list[BatchItem]

def guard(c,store):
    if store.engine.dialect.name=="postgresql":
        c.exec_driver_sql("SELECT pg_advisory_xact_lock(630487201)")

class BatchStore:
    def __init__(self,store,revision="local-v1"):
        self.store=store;self.revision=revision.strip()
        if not self.revision:raise ValueError("Empty batch configuration revision")
    def initialize(self):
        from .blind import labels
        with self.store._write_connection() as c:
            guard(c,self.store)
            for table in (jobs,items,labels):table.create(c,checkfirst=True)
    def groups(self):
        with self.store.engine.connect() as c:
            return sorted(c.execute(select(conversations.c.group_id).distinct()).scalars())
    def start(self,body,mode):
        if not self.store.get_checklist(body.checklist_id,body.checklist_version):raise HTTPException(404,"Unknown checklist.")
        with self.store._write_connection() as c:
            guard(c,self.store)
            ids=list(c.execute(select(conversations.c.id).where(conversations.c.group_id==body.group_id).order_by(conversations.c.id)).scalars())
            if not ids:raise HTTPException(404,"Unknown dataset.")
            # Retrying the same launch after a lost HTTP response resumes the same manifest.
            identity=str(uuid5(NAMESPACE_URL,str((body.group_id,body.checklist_id,body.checklist_version,mode,self.revision,ids))))
            if not c.execute(select(jobs.c.id).where(jobs.c.id==identity)).first():
                c.execute(insert(jobs).values(id=identity,group_id=body.group_id,checklist_id=body.checklist_id,
                        version=body.checklist_version,mode=mode,revision=self.revision,created=int(time.time())))
                c.execute(insert(items),[dict(id=str(uuid5(NAMESPACE_URL,identity+"/"+cid)),job=identity,cid=cid,
                        state="queued",attempt=0) for cid in ids])
        return self.get(identity)
    def ids(self):
        with self.store.engine.connect() as c:
            return list(c.execute(select(jobs.c.id).order_by(jobs.c.created.desc(),jobs.c.id)).scalars())
    def get(self,jid):
        with self.store.engine.connect() as c:
            job=c.execute(select(jobs).where(jobs.c.id==jid)).mappings().first()
            if not job:raise HTTPException(404,"Unknown batch.")
            rows=[dict(r) for r in c.execute(select(items.c.id,items.c.cid,items.c.state,items.c.attempt,items.c.error)
                    .where(items.c.job==jid).order_by(items.c.cid)).mappings()]
        return dict(**job,items=rows,counts=dict(Counter(r["state"] for r in rows)))
    def claim(self,mode,ttl):
        now=int(time.time())
        with self.store._write_connection() as c:
            q=(select(items,jobs.c.checklist_id,jobs.c.version).join(jobs,jobs.c.id==items.c.job)
                .where(jobs.c.mode==mode,jobs.c.revision==self.revision,or_(items.c.state=="queued",(items.c.state=="running") & (items.c.lease<=now)))
                .order_by(jobs.c.created,items.c.cid).limit(1))
            if self.store.engine.dialect.name=="postgresql":q=q.with_for_update(skip_locked=True,of=items)
            row=c.execute(q).mappings().first()
            if not row:return None
            token=str(uuid4())
            c.execute(update(items).where(items.c.id==row["id"]).values(state="running",token=token,lease=now+ttl,attempt=row["attempt"]+1))
            return dict(row,token=token)
    @staticmethod
    def finish_in(c,claim,state,error=None):
        count=c.execute(update(items).where(items.c.id==claim["id"],items.c.state=="running",
            items.c.token==claim["token"]).values(state=state,token=None,lease=None,error=error)).rowcount
        if count!=1:raise StorageConflict("Expired batch lease")
    def finish(self,claim,state,error=None):
        with self.store._write_connection() as c:self.finish_in(c,claim,state,error)
    def retry(self,jid):
        with self.store._write_connection() as c:
            c.execute(update(items).where(items.c.job==jid,items.c.state=="failed").values(state="queued",error=None))
        return self.get(jid)

async def run_worker(queue,settings,evaluate):
    while True:
        claim=None
        try:
            claim=await run_in_threadpool(queue.claim,settings.audit_mode,int(settings.ml_timeout_seconds)+45)
            if claim is None:
                await asyncio.sleep(1);continue
            tasks=BackgroundTasks()
            try:
                response=await evaluate(claim,tasks)
            except HTTPException as exc:
                await run_in_threadpool(queue.finish,claim,"failed",f"Ошибка оценки: HTTP {exc.status_code}.")
                continue
            if not isinstance(response,dict):
                await run_in_threadpool(queue.finish,claim,"failed",f"Ошибка оценки: HTTP {response.status_code}.")
            else:
                await tasks()
        except asyncio.CancelledError:
            # Before commit the lease is reclaimable; after commit finish_in cannot reopen a done item.
            if claim:
                try:await run_in_threadpool(queue.finish,claim,"queued")
                except (StorageConflict,SQLAlchemyError):pass
            raise
        except (StorageConflict,SQLAlchemyError):
            await asyncio.sleep(1)
        except Exception:
            # An unexpected per-item error is terminal until explicitly retried.
            if claim:
                try:await run_in_threadpool(queue.finish,claim,"failed","Ошибка обработки. Нужна проверка.")
                except (StorageConflict,SQLAlchemyError):pass
            await asyncio.sleep(1)

def install(app,queue,settings,contracts):
    from .contest import export
    from .blind import install as install_blind
    install_blind(app,queue)
    @app.get("/api/v1/batch-groups")
    def groups()->list[str]:return queue.groups()
    @app.post("/api/v1/batches")
    def launch(body:Start)->Batch:return queue.start(body,settings.audit_mode)
    @app.get("/api/v1/batches")
    def batches()->list[str]:return queue.ids()
    @app.get("/api/v1/batches/{jid}")
    def batch(jid:str)->Batch:return queue.get(jid)
    @app.post("/api/v1/batches/{jid}/retry")
    def retry(jid:str,body:Retry)->Batch:return queue.retry(jid)
    @app.get("/api/v1/batches/{jid}/results")
    def results(jid:str)->dict:return export(queue,jid,contracts)
