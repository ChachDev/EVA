"""Run on the same local llama.cpp worker. Store individual outputs ONLY in ignored data/."""
import argparse
import csv
import subprocess
import hashlib
import json
import os
import time
from pathlib import Path
from eva_ml.engine import LocalEngine
from eva_ml.consultation_v11 import select_provider
from eva_ml.contracts import Contracts
from benchmarks.audit_pairs import cases

ROOT = Path(__file__).resolve().parents[1]
CHECK = json.loads((ROOT.parent/"contracts/examples/checklist.json").read_text(encoding="utf8"))

def inputs(args):
    if args.pairs:
        for c,k,v in cases():
            yield c,{k:v}
        return
    if args.reference.suffix.lower()==".csv":
        with args.reference.open(encoding="utf-8-sig",newline="") as source_file:
            reference=[{"chat":r["Чат"],"criterion":r["Критерий"],"gold":r["Разметка"]} for r in csv.DictReader(source_file)]
    else:
        reference=json.loads(args.reference.read_text(encoding="utf-8-sig"))
    source = json.loads(args.dev.read_text(encoding="utf-8-sig"))["data"]["records"]
    ids = ([r["conversation_id"] for r in source if r["channel"]=="voice call"] if args.voices else dict.fromkeys(x["chat"] for x in reference))
    for cid in ids:
        r = next(x for x in source if x["conversation_id"]==cid)
        chat = r["channel"]=="agent chat"
        c = {"id":cid,"language":r["language"],"audio_url":r["audio_url"],
             "channel":"agent_chat" if chat else "voice_call","messages":[]}
        for i,m in enumerate(r["transcript"],1):
            parts = m["timestamp"].split(":") if chat else []
            c["messages"].append({**m,"id":f"m{i:04d}","offset_seconds":int(parts[0])*60+int(parts[1]) if chat else None})
        gold={}
        for x in reference:
            if x["chat"]==cid:
                t=str(x["gold"])
                gold[x["criterion"]]={"Да":True,"Нет":False,"N/A":None}.get(t,t)
                if t.isdigit(): gold[x["criterion"]]=int(t)
        yield c,gold

class Meter(LocalEngine):
    def request(self,*args,**kwargs):
        self.calls+=1
        try:
            reply=super().request(*args,**kwargs)
        except Exception:
            self.token_known=False
            raise
        used=reply.get("usage",{}).get("total_tokens")
        if type(used) is int:self.tokens+=used
        else:self.token_known=False
        return reply

def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument("--port",type=int,required=True)
    parser.add_argument("--file-order",action="store_true",help="Historical diagnostic: do not normalize like backend storage")
    parser.add_argument("--variant",required=True)
    parser.add_argument("--profile",choices=["rubric-v1","events-v2","source-events","baseline-plus"],default="rubric-v1")
    parser.add_argument("--pairs",action="store_true")
    parser.add_argument("--voices",action="store_true",help="Technical pilot only, no human reference for these calls")
    parser.add_argument("--ablation",action="store_true",help="Fixed six paired examples across three languages")
    parser.add_argument("--reference",type=Path,default=ROOT/"data/blind15-comparison-20261108/reference.json")
    parser.add_argument("--dev",type=Path,default=ROOT/".tmp/dev-ml-repair-2111/dev.json")
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    engine=Meter(args.port,"Qwen3.5-9B-Kazakh.Q5_K_M")
    baseline=select_provider(engine,"consultation-v1.1")
    validate=Contracts()
    if args.variant!="baseline":
        from eva_ml.audit_events import select_auditor
        auditor=select_auditor(baseline,set(args.variant.split(",")),args.profile,cache_size=0)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open("x",encoding="utf8") as out:
        for index,(c,gold) in enumerate(inputs(args)):
            if args.ablation and index not in {0,1,8,9,16,17}: continue
            req={"schema_version":"1.0.0","conversation":c,"checklist":CHECK}
            if not args.file_order:
                # EvaluationStore serializes snapshots with sort_keys=True; order
                # inside the JSON sent as LLM text affects the token sequence.
                req=json.loads(json.dumps(req,ensure_ascii=False,sort_keys=True))
            validate.validate_request(req)
            engine.calls=engine.tokens=0
            engine.token_known=True
            start=time.perf_counter()
            bundle=None if args.variant=="baseline" else auditor.evaluate(req,canonical=not args.file_order)
            result=bundle["response"] if bundle else baseline.evaluate(req)
            validate.validate_response(req,result)
            record={"cid":c["id"],"language":c["language"],"gold":gold,"seconds":time.perf_counter()-start,
                    "calls":engine.calls,"tokens":engine.tokens if engine.token_known else None,"response":result,"input_order":"file" if args.file_order else "storage",
                    "input_sha256":hashlib.sha256(json.dumps(req,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()}
            if bundle: record["audit"]=bundle["audit"]
            if args.voices: record["gold"]={}
            try:
                record["gpu_mib_after"]=int(subprocess.check_output(["nvidia-smi","--query-gpu=memory.used",
                    "--format=csv,noheader,nounits"],text=True,timeout=3).splitlines()[0])
            except (OSError,ValueError,subprocess.SubprocessError):
                record["gpu_mib_after"]=None
            out.write(json.dumps(record,ensure_ascii=False)+"\n"); out.flush()
            print(c["id"],round(record["seconds"],2),flush=True)

if __name__=="__main__":
    main()
