"""Paired offline replay. References are loaded only after predictions are written."""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from time import perf_counter

from benchmarks.friend_v7 import ARCHIVE, MODEL
from benchmarks.interaction_trial import inputs, references
from benchmarks.share_report import scores, same
from benchmarks.share_trial import PRIVATE, read_rows
from eva_ml.consultation_v11 import ConsultationV11Provider
from eva_ml.contracts import Contracts
from eva_ml.engine import LocalEngine, inference_usage
from eva_ml.issai import digest
from eva_ml.v7_fusion import V7Fusion
from eva_ml.v7_grounded import V7Grounded


GROUPS = ("develop","reserved","pilot","public8","public14")


def predict(group, port, out, cached=None, guarded=False):
    if not out.resolve().is_relative_to(PRIVATE.resolve()):
        raise ValueError("Use ml/data for individual results")
    out.parent.mkdir(parents=True, exist_ok=True)
    engine = LocalEngine(port, MODEL)
    if MODEL not in [r["id"] for r in engine.request("/v1/models")["data"]]:
        raise ValueError("Unexpected local model")
    cls = V7Grounded if guarded else V7Fusion
    provider = cls(ConsultationV11Provider(engine), ARCHIVE)
    contracts = Contracts()
    try:
        requests = dict((req["conversation"]["id"],req) for req,_ in inputs(group))
        parents = read_rows(PRIVATE/f"interaction-phase/release-a-{group}.jsonl")
        if set(requests) != {r["cid"] for r in parents} or len(requests) != len(parents):
            raise ValueError("Incomplete primary/interaction cohort")
        saved = read_rows(cached) if cached else []
        archives = {r["cid"]:r for r in saved}
        if cached and (len(archives) != len(saved) or set(archives) != set(requests)):
            raise ValueError("Incomplete archive cohort")
        with out.open("x",encoding="utf8") as f:
            for a in parents:
                cid=a["cid"]; req=requests[cid]
                if digest(req)!=a["request"] or a["reviewer"]!=provider.interaction.interaction_hash:
                    raise ValueError("Different input or interaction version")
                base=a["base"]
                if cached and archives[cid]["request"] != a["request"]:
                    raise ValueError("Archive input mismatch")
                v7=archives[cid]["v7"] if cached else provider.archive.refine(req, base)
                for r in (base,v7,a["result"]):
                    contracts.validate_response(req,r)
                    if r["model"]!=MODEL or r["provider"]!="llama.cpp": raise ValueError("Model mismatch")
                if any("v7 недоступна" in w for w in v7["warnings"]): raise RuntimeError("Archive verification failed")
                start=perf_counter()
                with inference_usage() as usage:
                    result=provider.combine(req,base,v7,a["result"])
                contracts.validate_response(req,result)
                item={"cid":cid,"group":group,"request":a["request"],"content":a["content"],"revision":provider.revision,
                      "base":base,"v7":v7,"fusion":result,"extra_seconds":round(perf_counter()-start,3),"usage":usage}
                f.write(json.dumps(item,ensure_ascii=False)+"\n");f.flush()
                print(group,cid,item["extra_seconds"],flush=True)
    finally:
        provider.close()


def report(paths):
    gold=references()
    ballot=read_json_ballot()
    counts=defaultdict(lambda:defaultdict(lambda:defaultdict(Counter))); seen=set(); versions=set()
    changes=[]; errors=[]
    for path in paths:
        rows=read_rows(path); group=rows[0]["group"]
        expected={r["conversation"]["id"]:digest(r) for r,_ in inputs(group)}
        if len(rows)!=len(expected) or {r["cid"]:r["request"] for r in rows}!=expected:
            raise ValueError("Missing or changed evaluation input")
        for r in rows:
            if r["content"] in seen:raise ValueError("Repeated input")
            seen.add(r["content"]);versions.add(r["revision"]);cid=r["cid"]
            values={v:scores(r[v]) for v in ("base","v7","fusion")}
            for key,y in gold[cid].items():
                groups=[group]
                if group in ("develop","reserved"):
                    groups.extend(["book","unanimous" if ballot[cid,key] else "disputed"])
                if group.startswith("public"):groups.append("public")
                if group in ("pilot","public8","public14"):groups.append("other")
                for g in groups:
                    for v,judgments in values.items():
                        value=judgments[key]
                        for col in ("all",key):
                            counts[g][v][col].update(n=1,correct=int(same(value,y)),covered=int(value!="review"),
                                false_na=int(value is None and y is not None),missed_na=int(y is None and value is not None))
                if not same(values["v7"][key],values["fusion"][key]):
                    changes.append({"cid":cid,"group":group,"key":key,"v7":values["v7"][key],"fusion":values["fusion"][key],"reference":y})
                if not same(values["fusion"][key],y):
                    decision=next(item for item in r["fusion"]["criteria"] if item["criterion_id"]==key)
                    errors.append({"cid":cid,"group":group,"criterion":key,"expected":y,"actual":values["fusion"][key],
                                   "v7":values["v7"][key],"request_sha256":r["request"],"evidence":decision["evidence"]})
    if len(versions)!=1:raise ValueError("Mixed scoring revisions")
    return {"revision":next(iter(versions)),"counts":counts,"changes":changes,"errors":errors}


def read_json_ballot():
    records=json.loads((PRIVATE/"blind15-comparison-20261108/reference.json").read_text("utf8"))
    return {(r["chat"],r["criterion"]):len({r[str(i)] for i in range(1,5)})==1 for r in records}


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",type=Path,required=True);p.add_argument("--port",type=int,default=59174)
    p.add_argument("--group",choices=GROUPS);p.add_argument("--cached-v7",type=Path);p.add_argument("--report",nargs="+",type=Path)
    p.add_argument("--guarded",action="store_true")
    args=p.parse_args()
    if args.report:
        with args.out.open("x",encoding="utf8") as f:json.dump(report(args.report),f,ensure_ascii=False,indent=2)
    elif args.group:predict(args.group,args.port,args.out,args.cached_v7,args.guarded)
    else:p.error("Choose --group or --report")
