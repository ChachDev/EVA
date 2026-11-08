"""Compare the pinned V6/V7 verification stages on the exact same local primary response."""
import argparse
import ast
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from zipfile import ZipFile

from benchmarks.ledger_trial import public_holdout
from benchmarks.share_report import label, scores
from eva_ml.consultation_v11 import select_provider
from eva_ml.contracts import Contracts
from eva_ml.engine import LocalEngine


ML=Path(__file__).resolve().parents[1]
OUT=ML/"data/compare-friend-v7"
ARCHIVE=OUT/"submitted.zip"
PIN="35ca10e6e6c2a2f0eefe0b3d4472c64439159a9894fc967263cf6c4b823417f7"
MODEL="Qwen3.5-9B-Kazakh.Q5_K_M"
MODULES=("issai","consultation_v11","applicability","applicability_v2","applicability_v3","resolution",
         "resolution_v2","resolution_v3","resolution_v3_scope","resolution_v5","resolution_v6",
         "resolution_v7","banking_events","transcript_quality")


def stage(target):
    if sha256(ARCHIVE.read_bytes()).hexdigest()!=PIN:
        raise ValueError("Unexpected archive")
    with ZipFile(ARCHIVE) as z:
        manifest=json.loads(z.read("EVA/TRANSFER_MANIFEST.json"))
        paths=[f"ml/eva_ml/{m}.py" for m in MODULES]+["ml/benchmarks/fact_rules.py","ml/benchmarks/frozen_checklist.json",
               "contracts/examples/checklist.json"]
        for name in paths:
            b=z.read("EVA/"+name)
            if sha256(b).hexdigest()!=manifest[name]:raise ValueError("Manifest mismatch")
            dst=target/name
            dst.parent.mkdir(parents=True,exist_ok=True)
            dst.write_bytes(b)
    for area in ("ml/eva_ml","ml/benchmarks"):
        (target/area/"__init__.py").write_bytes(b"")


# Evaluation logic is read from the pinned allowlist. All model calls are delegated to the parent.
CHILD="""
import sys,json,types
import jsonschema
sys.dont_write_bytecode=True
sys.path.insert(0,sys.argv[1])
def guard(event,args):
    if event.startswith(('socket.','subprocess.')) or event in ('os.system','os.spawn','os.exec','os.remove','os.rename'):
        raise PermissionError(event)
    if event=='open' and len(args)>1 and isinstance(args[1],str) and any(x in args[1] for x in 'wax+'):
        raise PermissionError('write')
sys.addaudithook(guard)
bridge=types.ModuleType('eva_ml.engine')
class EngineError(RuntimeError):pass
bridge.EngineError=EngineError
bridge.ContextLimitError=EngineError
sys.modules['eva_ml.engine']=bridge
from eva_ml.resolution_v6 import ResolutionV6Provider
from eva_ml.resolution_v7 import ResolutionV7Provider
class Engine:
    model=sys.argv[2]
    def request(self,route,payload=None,timeout=120):
        print(json.dumps({'query':payload,'route':route},ensure_ascii=True),flush=True)
        reply=json.loads(sys.stdin.readline())
        if 'failure' in reply:raise EngineError('Inference failed')
        return reply['answer']
variants={'v6':ResolutionV6Provider(Engine()),'v7':ResolutionV7Provider(Engine())}
for line in sys.stdin:
    req=json.loads(line)
    result=variants[req['variant']].refine(req['input'],req['primary'])
    print(json.dumps({'out':result},ensure_ascii=True),flush=True)
"""


def source_rows(cohort):
    if cohort in {"public10","public16"}:
        ten=cohort=="public10"
        old="issai-public.jsonl" if ten else "issai-gold16.jsonl"
        saved={r["id"]:r for r in map(json.loads,(ML/"data/ledger-v1"/old).read_text(encoding="utf8").splitlines())}
        for req,gold,*_ in (public_holdout() if ten else public_holdout("public-gold16.json",16)):
            cid=req["conversation"]["id"]
            assert gold==saved[cid]["gold"]
            yield req,gold,None,saved[cid]["primary"]
    elif cohort=="dev15":
        ballot=json.loads((ML/"data/blind15-comparison-20261108/reference.json").read_text(encoding="utf8"))
        sources={r["conversation"]["id"]:r for r in map(json.loads,
                 (ML/"data/clean-dev-de20aae9/eva-requests.jsonl").read_text(encoding="utf8").splitlines())}
        pending={r["conversation_id"] for r in map(json.loads,(ML/"data/share-comparison/blind-next.jsonl").read_text(encoding="utf8").splitlines())}
        ids=sorted({r["chat"] for r in ballot})
        assert len(ids)==15 and pending.isdisjoint(ids)
        for cid in ids:
            group=[r for r in ballot if r["chat"]==cid]
            ref={r["criterion"]:label(r["gold"]) for r in group}
            unanimous={r["criterion"] for r in group if len({r[str(i)] for i in range(1,5)})==1}
            yield sources[cid],ref,sorted(unanimous),None
    elif cohort=="sequence":
        with ZipFile(ARCHIVE) as z:
            txt=z.read("EVA/ml/benchmarks/identity_sequence_validation.py").decode("utf8")
            base=json.loads(z.read("EVA/contracts/examples/evaluation-request.json"))
        node=next(n for n in ast.parse(txt).body if isinstance(n,ast.Assign) and n.targets[0].id=="CASES")
        for i,(lang,value,lines) in enumerate(ast.literal_eval(node.value),1):
            req=deepcopy(base)
            req["checklist"]["criteria"]=[c for c in req["checklist"]["criteria"] if c["id"]=="identity"]
            req["conversation"].update(id=f"identity-sequence-v7-{i}",language=lang,audio_url=None,
                messages=[dict(id=f"m{j:04}",speaker=r,text=t,timestamp=f"00:{j:02}",offset_seconds=j) for j,(r,t) in enumerate(lines,1)])
            if value=="unknown":req["conversation"]["source"]={"complete_start":False}
            yield req,{"identity":"review" if value=="unknown" else value},None,None


def send(proc,value):
    proc.stdin.write(json.dumps(value,ensure_ascii=True)+"\n")
    proc.stdin.flush()


def refine(proc, engine, request, base, variant, memo):
    send(proc,{"input":request,"primary":base,"variant":variant})
    calls=0
    while True:
        line=proc.stdout.readline()
        if not line:raise RuntimeError("Isolated evaluation process exited")
        item=json.loads(line)
        if "out" in item:return item["out"],calls
        payload=item["query"]
        if item["route"]!="/v1/chat/completions" or payload.get("model")!=MODEL:
            raise ValueError("Unexpected model request")
        key=sha256(json.dumps(payload,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
        calls+=1
        if key not in memo:
            memo[key]=engine.request(item["route"],payload,timeout=200)
        send(proc,{"answer":memo[key]})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port",type=int,required=True)
    p.add_argument("--cohorts",nargs="+",choices=("sequence","public10","public16","dev15"),required=True)
    args=p.parse_args()
    engine=LocalEngine(args.port,MODEL)
    assert MODEL in {x["id"] for x in engine.request("/v1/models")["data"]}
    primary=select_provider(engine,"consultation-v1.1")
    contracts=Contracts()
    with TemporaryDirectory(dir=ML/".tmp") as tmp:
        target=Path(tmp)
        stage(target)
        with subprocess.Popen([sys.executable,"-I","-B","-c",CHILD,str(target/"ml"),MODEL],stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE,encoding="ascii",text=True,
                              creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)) as proc:
            try:
                for cohort in args.cohorts:
                    samples=list(source_rows(cohort))
                    out=OUT/(cohort+".jsonl")
                    saved=[json.loads(l) for l in out.read_text(encoding="utf8").splitlines()] if out.exists() else []
                    if [r["id"] for r in saved]!=[s[0]["conversation"]["id"] for s in samples[:len(saved)]]:
                        raise ValueError("Different saved cohort")
                    with out.open("a",encoding="utf8") as f:
                        for i,(req,gold,unanimous,base) in enumerate(samples):
                            if i<len(saved):continue
                            req=json.loads(json.dumps(req,ensure_ascii=False,sort_keys=True))
                            contracts.validate_request(req)
                            base=base or primary.evaluate(req)
                            contracts.validate_response(req,base)
                            memo={}
                            preds={"base":scores(base)}
                            cost={}
                            for name in (("v6","v7") if i%2==0 else ("v7","v6")):
                                t=perf_counter()
                                result,n=refine(proc,engine,req,base,name,memo)
                                contracts.validate_response(req,result)
                                preds[name]=scores(result)
                                cost[name]=(n,round(perf_counter()-t,3))
                            row=dict(id=req["conversation"]["id"],language=req["conversation"]["language"],gold=gold,
                                     unanimous=unanimous,pred=preds,cost=cost,distinct_inference=len(memo))
                            f.write(json.dumps(row,ensure_ascii=False)+"\n")
                            f.flush()
                            print(cohort,i+1,flush=True)
            finally:
                proc.stdin.close()


if __name__=="__main__":
    main()
