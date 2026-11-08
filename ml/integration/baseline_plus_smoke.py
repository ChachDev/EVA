"""Exercise baseline-first shadow on the isolated stack with two synthetic Freedom calls."""
import argparse
import json
from pathlib import Path
import sys
import time
import urllib.request
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from benchmarks.audit_pairs import cases


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--api",default="http://127.0.0.1:18600")
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    if not args.api.startswith("http://127.0.0.1:") or args.api.endswith(":8000"):
        raise ValueError("Use an isolated loopback backend")
    def call(path,body=None):
        req=urllib.request.Request(args.api+"/api/v1/"+path,data=None if body is None else json.dumps(body,ensure_ascii=False).encode(),
                                   headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req,timeout=120) as response:return json.load(response)
    receipts=[]
    for conv,key,expected in [list(cases())[1],list(cases())[10]]:
        conversation=call("conversations",{"operation_id":str(uuid4()),"channel":conv["channel"],"language":conv["language"],
            "messages":[{k:m[k] for k in ("speaker","timestamp","text")} for m in conv["messages"]]})
        rec=call("evaluations",{"conversation_id":conversation["id"],"checklist_id":"freedom-cqa","checklist_version":1})
        path="evaluations/"+rec["id"]
        for _ in range(120):
            export=call(path+"/audit")
            if export["audit"]["status"]!="pending":break
            time.sleep(1)
        shadow=export["audit"];assert shadow["mode"]=="shadow" and shadow["status"]=="completed"
        candidate=shadow["candidate"];audit=candidate["audit"]
        assert audit["versions"]["profile"]=="baseline-plus"
        assert audit["verification"]["baseline"]==rec["original"] and export["review"]["original"]==rec
        assert audit["stage_usage"]["baseline"]["calls"]==0 and call(path)==rec
        decision=next(c for c in candidate["response"]["criteria"] if c["criterion_id"]==key)
        before=next(c for c in rec["original"]["criteria"] if c["criterion_id"]==key)
        assert decision["status"]=="scored" and decision["value"] is expected
        receipts.append({"key":key,"conversation":conversation["id"],"evaluation":rec["id"],
           "baseline":before["value"],"verified":decision["value"],"extra_calls":audit["calls"]})
    args.out.write_text(json.dumps(receipts,indent=2),encoding="utf8")
    print(json.dumps(receipts))
if __name__=="__main__":main()
