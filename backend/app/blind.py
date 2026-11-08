"""Independent blind labels, separate from reviews and model prompts."""
import json
from collections import Counter,defaultdict
from time import time
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import Column,Integer,Table,Text,insert,select

from .batches import guard,items,meta

labels=Table("eva_blind_labels",meta,Column("eid",Text,primary_key=True),Column("author",Text,primary_key=True),
    Column("answers",Text,nullable=False),Column("created",Integer,nullable=False))

class Submission(BaseModel):
    model_config=ConfigDict(strict=True,extra="forbid")
    reviewer:str=Field(min_length=1,max_length=120)
    answers:dict[str,bool|int|None|str]

def allowed(rule,value):
    if value=="unknown":return True
    if value is None:return rule["allow_na"]
    if rule["type"]=="scale":return type(value) is int and value in rule["scale"]
    return type(value) is bool

def install(app,queue):
    store=queue.store
    def snapshot(eid):
        snap=store.get_snapshot(eid)
        if not snap:raise HTTPException(404,"Snapshot not found.")
        return snap
    @app.get("/api/v1/blind/{eid}")
    def task(eid:str)->dict:
        snap=snapshot(eid)
        return {k:snap[k] for k in ("conversation","checklist")}
    @app.post("/api/v1/blind/{eid}")
    def submit(eid:str,body:Submission)->dict:
        snap=snapshot(eid); rules={r["id"]:r for r in snap["checklist"]["criteria"]}
        if rules.keys()!=body.answers.keys() or any(not allowed(r,body.answers[k]) for k,r in rules.items()):
            raise HTTPException(422,"Provide a valid human answer for every criterion.")
        author=body.reviewer.strip()
        if not author:raise HTTPException(422,"Reviewer required.")
        encoded=json.dumps(body.answers,sort_keys=True,ensure_ascii=False)
        with store._write_connection() as c:
            guard(c,store)
            old=c.execute(select(labels.c.answers).where(labels.c.eid==eid,labels.c.author==author)).scalar_one_or_none()
            if old is None:c.execute(insert(labels).values(eid=eid,author=author,answers=encoded,created=int(time())))
            elif old!=encoded:raise HTTPException(409,"Blind answers were already submitted.")
        base=store.get(eid)["original"]
        return dict(rows=[dict(criterion=r["criterion_id"],human=body.answers[r["criterion_id"]],
            auto=r["value"] if r["status"]=="scored" else "unknown") for r in base["criteria"]])
    @app.get("/api/v1/batches/{jid}/quality")
    def quality(jid:str)->dict:
        batch=queue.get(jid)
        with store.engine.connect() as c:
            votes=list(c.execute(select(labels.c.eid,labels.c.answers).join(items,items.c.id==labels.c.eid)
                .where(items.c.job==jid)))
        buckets=defaultdict(Counter)
        for eid,encoded in votes:
            human=json.loads(encoded);snap=store.get_snapshot(eid)
            for row in store.get(eid)["original"]["criteria"]:
                k=row["criterion_id"];h=human[k];a=row["value"] if row["status"]=="scored" else "unknown"
                b=buckets[(snap["conversation"]["language"],k)];b["labels"]+=1
                if h=="unknown":b["human_unknown"]+=1;continue
                b["denominator"]+=1;b["matched"]+=int(type(a)==type(h) and a==h);b["unresolved"]+=int(a=="unknown")
                b["false_na"]+=int(a is None and h is not None);b["missed_na"]+=int(h is None and a is not None)
                if k=="gross_errors":
                    b["false_accusations"]+=int(a is True and h is False);b["missed_positive"]+=int(h is True and a is not True)
        return dict(total=len(batch["items"]),labeled=len({eid for eid,_ in votes}),submissions=len(votes),
            rows=[dict(language=lang,criterion=k,**vals) for (lang,k),vals in sorted(buckets.items())])
