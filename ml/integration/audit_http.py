"""End-to-end check against the isolated audit_stack.py deployment; synthetic records only."""
from copy import deepcopy
import json
import urllib.request
from uuid import uuid4
from pathlib import Path
import sys

def main(base,out):
    base=base.rstrip("/")
    if not base.startswith("http://127.0.0.1:") or base.endswith(":8000"):
        raise ValueError("Choose an isolated loopback backend port")
    def call(path,body=None):
        req=urllib.request.Request(base+"/api/v1/"+path,data=None if body is None else json.dumps(body,ensure_ascii=False).encode(),
                                   headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req,timeout=120) as reply:return json.load(reply)
    c=call("conversations",{"operation_id":str(uuid4()),"language":"KZ","channel":"agent_chat",
      "source":{"target_operator_id":"A","complete_start":True,"complete_end":True},
      "messages":[{"timestamp":None,"speaker":"client","language":"mixed","text":"Сәлем. Можно узнать баланс?"},
                  {"timestamp":None,"speaker":"system","text":"Проверка личности подтверждена."},
                  {"timestamp":None,"speaker":"operator","speaker_id":"A","language":"mixed","text":"Менің атым Алия. Баланс вашего счёта — сто теңге."}]})
    original=next(x for x in call("checklists") if x["id"]=="freedom-cqa" and x["version"]==1)
    key="audit-test-"+uuid4().hex[:10]
    check=call("checklists/"+key+"/versions",{"base_version":0,"name":"Synthetic audit","criteria":original["criteria"]})
    ev=call("evaluations",{"conversation_id":c["id"],"checklist_id":key,"checklist_version":1})
    path="evaluations/"+ev["id"]
    bundle=call(path+"/audit");audit=bundle["audit"]["candidate"]["audit"]
    assert len(audit["sentinel"])==3 and audit["target_operator"]=="A" and audit["run_id"]
    assert all(p["start"] is None for f in audit["sentinel"] for p in f["evidence"])
    appeal=call(path+"/appeals",{"operation_id":str(uuid4()),"expected_revision":0,"criterion_id":"contact",
                "reason":"Synthetic reviewer checks opening.","message_ids":[c["messages"][2]["id"]]})
    override={"criterion_id":"contact","status":"scored","value":False,"reason":"Greeting was missing.",
              "evidence":[{"kind":"missing_action","message_ids":[c["messages"][2]["id"]]}]}
    decision={"operation_id":str(uuid4()),"expected_revision":1,"decision":"accepted","reason":"Reviewed opening.",
              "override":override}
    decision_path="appeals/"+appeal["appeals"][0]["id"]+"/decisions"
    reviewed=call(decision_path,decision)
    assert call(decision_path,decision)==reviewed and reviewed["revision"]==2
    assert reviewed["original"]==ev and reviewed["final"]["criteria"][0]==override
    assert reviewed["final"]["criteria"][1:]==ev["original"]["criteria"][1:]
    weights=deepcopy(check["criteria"]);weights[0]["weight"],weights[2]["weight"]=weights[2]["weight"],weights[0]["weight"]
    call("checklists/"+key+"/versions",{"base_version":1,"name":"Synthetic reweight","criteria":weights})
    derived=call(path+"/versions/2",{}); assert call(path+"/versions/2",{})["id"]==derived["id"]
    details=call("evaluations/"+derived["id"]+"/audit")["audit"]
    assert not any(v["evaluate"] for v in details["changes"].values())
    assert derived["original"]["criteria"]==ev["original"]["criteria"] and call(path)==ev
    assert call(path+"/audit")["review"]["final"]==reviewed["final"]
    receipt={"conversation":c["id"],"evaluation":ev["id"],"derived":derived["id"],"revision":reviewed["revision"],
             "seconds":audit["seconds"],"calls":audit["calls"],"tokens":audit["tokens"],"run_id":audit["run_id"]}
    Path(out).write_text(json.dumps(receipt,indent=2),encoding="utf8")
    print(json.dumps(receipt))
if __name__=="__main__": main(sys.argv[1],sys.argv[2])
