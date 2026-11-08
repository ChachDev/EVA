"""Synthetic checks of the optional C03 verifier through the real ML HTTP handler."""
import argparse
from copy import deepcopy
import json
import os

from fastapi.testclient import TestClient

from benchmarks.audit_run import CHECK
from benchmarks.friend_v7 import OUT
from eva_ml.contracts import Contracts
from eva_ml.ledger_service import app


def request(name,order,complete):
    turns={"ask":("client","Проверьте статус моей заявки."),
           "handoff":("system","Чат передан оператору."),"verified":("system","Личность клиента подтверждена."),
           "action":("operator","Я проверил вашу заявку в системе: она одобрена."),
           "unknown":("system","Внешняя операция была обработана.")}
    checklist=deepcopy(CHECK)
    if name!="late":
        checklist["criteria"]=[c for c in checklist["criteria"] if c["id"]=="identity"]
    conv={"id":"synthetic-order-"+name,"channel":"agent_chat","language":"RU","audio_url":None,
          "source":{"complete_start":complete,"complete_end":True},
          "messages":[dict(id=f"m{i:04d}",speaker=turns[event][0],text=turns[event][1],timestamp=f"00:{i:02}",
                           offset_seconds=i) for i,event in enumerate(order,1)]}
    return {"schema_version":"1.0.0","checklist":checklist,"conversation":conv}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port",type=int,required=True)
    args=p.parse_args()
    os.environ.update(EVA_ML_PROVIDER="issai",EVA_CONSULTATION_VARIANT="consultation-v1.1",
                      EVA_LEDGER_MODE="identity-order",EVA_LLAMA_WORKER_PORT=str(args.port))
    cases=[("early",["ask","handoff","verified","action"],True,True),
           ("late",["ask","handoff","action","verified"],True,False),
           ("unknown",["ask","unknown","action","verified"],True,None),
           ("partial",["ask","handoff","action","verified"],False,None)]
    records=[]
    with TestClient(app) as client:
        for name,order,complete,expected in cases:
            req=request(name,order,complete)
            base=app.state.provider.primary.evaluate(req)
            resp=client.post("/v1/evaluate",json=req)
            assert resp.status_code==200
            result=resp.json()
            Contracts().validate_response(req,result)
            row=next(c for c in result["criteria"] if c["criterion_id"]=="identity")
            initial=next(c for c in base["criteria"] if c["criterion_id"]=="identity")
            applied=any(w.startswith("identity-order ") for w in result["warnings"])
            assert [c for c in base["criteria"] if c["criterion_id"]!="identity"]==[
                c for c in result["criteria"] if c["criterion_id"]!="identity"]
            passed=(row==initial and not applied) if expected is None else (row["value"] is expected and applied)
            records.append(dict(case=name,base=[initial["status"],initial["value"]],actual=[row["status"],row["value"]],
                                evidence=[e["message_ids"] for e in row["evidence"]],applied=applied,passed=passed))
    with (OUT/"our-identity-order.json").open("x",encoding="utf8") as f:
        json.dump(records,f,ensure_ascii=False,indent=2)
    print(records)


if __name__=="__main__":
    main()
