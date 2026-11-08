"""Compare sealed outputs by conversation. No inference, no labels inferred from predictions."""
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
import argparse
import json
import math
from pathlib import Path
import statistics
from zipfile import ZipFile
from benchmarks.audit_run import CHECK, ROOT

def read(path):
    return [json.loads(x) for x in path.read_text(encoding="utf8").splitlines()]

def total(labels):
    num=Decimal(0); denominator=Decimal(0)
    for c in CHECK["criteria"]:
        k=c["id"]
        if k not in labels or labels[k]=="review": return None
        if c["type"]=="flag" or labels[k] is None: continue
        weight=Decimal(str(c["weight"]))
        ratio=Decimal(int(labels[k]))/Decimal(max(c["scale"]) if c["scale"] else 1)
        denominator+=weight;num+=weight*ratio
    return float((100*num/denominator).quantize(Decimal(".01"),rounding=ROUND_HALF_UP)) if denominator else None

def report(rows, sources):
    counts=Counter(); groups={}; errors=[]; elapsed=[]; expected_totals={}; auto={}; calls=[];tokens=[];mem=[]
    for row in rows:
        gold=row["gold"]; resp=row["response"]; known=sources[row["cid"]]
        labels={}
        for c in resp["criteria"]:
            k=c["criterion_id"]
            value="review" if c["status"]=="needs_review" else c["value"]
            labels[k]=value
            for ev in c["evidence"]:
                for mid in ev["message_ids"]:
                    counts["references"]+=1;counts["invalid_ids"]+=int(mid not in known)
            if k not in gold: continue
            truth=gold[k];correct=value==truth
            subtype=row["cid"].split("-",2)[-1] if row["cid"].startswith("pair-") and k=="gross_errors" else "unspecified"
            for group in ("all","lang/"+row["language"],"criterion/"+k,"lang-criterion/"+row["language"]+"/"+k,
                          *(["violation/"+subtype] if k=="gross_errors" else [])):
                g=groups.setdefault(group,Counter())
                g["n"]+=1;g["correct"]+=int(correct);g["automatic"]+=int(value!="review")
                g["false_na"]+=int(value is None and truth is not None);g["missed_na"]+=int(truth is None and value is not None)
                if k=="gross_errors":
                    if truth is True: g["positives"]+=1;g["tp" if value is True else "fn"]+=1
                    if truth is False and value is True:g["fp"]+=1
                    if truth is False and value is False:g["tn"]+=1
            if not correct:errors.append((row["cid"],k,truth,value))
        if len(gold)==len(CHECK["criteria"]):
            expected_totals[row["cid"]]=total(gold)
            auto[row["cid"]]=total(labels)
        if row.get("seconds") is not None: elapsed.append(row["seconds"])
        if row.get("calls") is not None: calls.append(row["calls"])
        if row.get("tokens") is not None: tokens.append(row["tokens"])
        if row.get("gpu_mib_after") is not None: mem.append(row["gpu_mib_after"])
    for key,g in groups.items():
        g["accuracy_all"]=round(g["correct"]/g["n"],4)
        g["accuracy_resolved"]=round(g["correct"]/g["automatic"],4) if g["automatic"] else None
        if "tp" in g or "fn" in g or "fp" in g or "tn" in g:
            for name,den,n in [("precision",g["tp"]+g["fp"],g["tp"]),("recall",g["tp"]+g["fn"],g["tp"]),
                               ("f1",2*g["tp"]+g["fp"]+g["fn"],2*g["tp"])]:
                g[name]=round(n/den,4) if den else None
    matched=[abs(auto[k]-expected_totals[k]) for k in auto if auto[k] is not None and expected_totals[k] is not None]
    return dict(groups=groups,errors=errors,technical=counts,numeric=len(matched),mae=round(statistics.mean(matched),3) if matched else None,
                seconds_median=round(statistics.median(elapsed),2) if elapsed else None,
                seconds_p95=round(sorted(elapsed)[math.ceil(.95*len(elapsed))-1],2) if elapsed else None,
                calls=sum(calls) if calls else None,tokens=sum(tokens) if tokens else None,
                gpu_mib_after_max=max(mem) if mem else None),auto,expected_totals

def main():
    p=argparse.ArgumentParser();p.add_argument("--candidate",required=True,type=Path)
    p.add_argument("--baseline",required=True,type=Path);p.add_argument("--dev",type=Path,default=ROOT/".tmp/dev-ml-repair-2111/dev.json")
    p.add_argument("--times",type=Path,default=ROOT/"data/consultation-v1.1/timing.jsonl")
    args=p.parse_args()
    new=read(args.candidate); ids=[r["cid"] for r in new]
    sources={}
    if all(k.startswith("pair-") for k in ids):
        from benchmarks.audit_pairs import cases
        sources={c["id"]:{m["id"] for m in c["messages"]} for c,_,_ in cases()}
    else:
        sources={r["conversation_id"]:{f"m{i:04d}" for i in range(1,len(r["transcript"])+1)}
                 for r in json.loads(args.dev.read_text(encoding="utf-8-sig"))["data"]["records"]}
    if args.baseline.suffix==".zip":
        times={r["cid"]:r["seconds"] for r in read(args.times) if r["group"]=="blind"}
        with ZipFile(args.baseline) as z:
            old=[{**r,"response":json.loads(z.read(r["cid"]+".json"))["original"],"seconds":times.get(r["cid"]),
                  "tokens":None,"calls":None,"gpu_mib_after":None,"input_sha256":None} for r in new]
    else:
        base={r["cid"]:r for r in read(args.baseline)}
        old=[base[k] for k in ids]
    if len(set(ids))!=len(ids) or any(a["gold"]!=b["gold"] for a,b in zip(new,old)): raise ValueError("Unpaired sample")
    if any(a.get("input_sha256") and b.get("input_sha256") and a["input_sha256"]!=b["input_sha256"] for a,b in zip(new,old)):
        raise ValueError("Different serialized model inputs")
    a,v,truth=report(old,sources);b,w,_=report(new,sources)
    common=[k for k in v if v[k] is not None and w[k] is not None and truth[k] is not None]
    pair_mae=[round(statistics.mean(abs(t[k]-truth[k]) for k in common),3) for t in (v,w)] if common else []
    old_errors={(e[0],e[1]) for e in a["errors"]};new_errors={(e[0],e[1]) for e in b["errors"]}
    print(json.dumps(dict(n=len(ids),baseline=a,candidate=b,common_numeric=len(common),common_mae=pair_mae,
          fixed=sorted(old_errors-new_errors),regressed=sorted(new_errors-old_errors)),ensure_ascii=False,indent=2))
if __name__=="__main__": main()
