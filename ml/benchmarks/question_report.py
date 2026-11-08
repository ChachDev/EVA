"""Verify real bundles at the backend boundary, then report only anonymous paired counts."""
import argparse,json,math,sys
from collections import Counter,defaultdict
from statistics import median,mean
from types import SimpleNamespace
from benchmarks.audit_compare import read,total
from benchmarks.audit_run import ROOT,CHECK,inputs
from benchmarks.question_trial import examples
from benchmarks.targeted_report import labels
sys.path.insert(0,str(ROOT.parent/"backend"))
from app.audit import checked
from app.contracts import Contracts

def compile_results(path):
    rows=read(path);size=Counter(x["cohort"] for x in rows)
    if size!={"develop":9,"reserved":6,"pilot":12,"synthetic":6} or len({r["cid"] for r in rows})!=33:
        raise ValueError("Incomplete paired trial")
    if len({r["audit"]["versions"]["code"] for r in rows})!=1 or len({r["audit"]["versions"]["model_revision"]["declared"] for r in rows})!=1:
        raise ValueError("Mixed verifier or weight revisions")
    dev=ROOT/".tmp/dev-ml-repair-2111/dev.json"
    conv={c["id"]:c for ref in [ROOT/"data/blind15-comparison-20261108/reference.json",ROOT/"data/consultation-v1.1/pilot-96.csv"]
        for c,_ in inputs(SimpleNamespace(pairs=False,voices=False,reference=ref,dev=dev))}
    conv.update({c["id"]:c for c,_ in examples()})
    actual=[conv[r["cid"]] for r in rows if r["cohort"]!="synthetic"]
    texts={tuple((m["speaker"]," ".join(m["text"].casefold().split())) for m in c["messages"]) for c in actual}
    if len(texts)!=len(actual):raise ValueError("Duplicate transcripts in annotated subsets")
    old={r["cid"]:r for name in ("human","pilot") for r in read(ROOT/f"data/baseline-plus-audit-v1/{name}.jsonl")}
    targeted={r["cid"]:r for name in ("develop","reserved","pilot") for r in read(ROOT/f"data/targeted-trial/{name}.jsonl")}
    contracts=Contracts(ROOT.parent/"contracts");output={}
    for cohort in size:
        counts=Counter(); bykey=defaultdict(Counter); bylang=defaultdict(Counter)
        states=Counter();costs=[];abs_error=[[],[]]; marks=[]
        for row in (r for r in rows if r["cohort"]==cohort):
            req={"schema_version":"1.0.0","conversation":conv[row["cid"]],"checklist":CHECK}
            audit=row["audit"]; result=row["response"]
            checked(contracts,req,{"response":result,"audit":audit})
            base=audit["verification"]["baseline"];prev=old.get(row["cid"])
            if prev and (base!=prev["audit"]["verification"]["baseline"] or row["gold"]!=prev["gold"]):
                raise ValueError("Control or annotations changed")
            b,n=labels(base),labels(result)
            earlier=labels(prev["response"]) if prev else None
            target=labels(targeted[row["cid"]]["response"]) if prev else None
            for k,y in row["gold"].items():
                x,z=b[k],n[k];correct=lambda val:type(val)==type(y) and val==y
                for stats in (counts,bykey[k],bylang[row["language"]]):
                    stats.update(n=1,base=int(correct(x)),new=int(correct(z)),fixed=int(correct(z) and not correct(x)),
                        regressed=int(correct(x) and not correct(z)),resolved_before=int(x!="review"),resolved_after=int(z!="review"),
                        false_na_before=int(x is None and y is not None),false_na_after=int(z is None and y is not None),
                        missed_na_before=int(y is None and x is not None),missed_na_after=int(y is None and z is not None))
                    if earlier is not None:stats.update(previous_plus=int(correct(earlier[k])),previous_targeted=int(correct(target[k])))
                if k=="gross_errors":
                    counts.update(positive=int(y is True),fp=int(y is False and z is True),fn=int(y is True and z is not True))
                if correct(x)!=correct(z):marks.append(dict(language=row["language"],criterion=k,old=x,new=z,gold=y))
            g=total(row["gold"]); v,w=total(b),total(n)
            if None not in (v,w,g):
                abs_error[0].append(abs(v-g));abs_error[1].append(abs(w-g))
            states[audit["verification"]["state"]]+=1
            qm=audit.get("question_map",{}); stage=audit["stage_usage"].get("questions")
            counts.update(map_attempts=int(stage is not None),validated_maps=int(bool(qm)),
                map_amendments=len(qm.get("applied",[])),model_calls=audit["calls"] or 0,model_tokens=audit["tokens"] or 0)
            if stage:costs.append(stage["seconds"])
        output[cohort]=dict(chats=size[cohort],counts=counts,languages=bylang,criteria=bykey,
            changes=marks,states=states,mae=[round(mean(a),3) if a else None for a in abs_error],mae_n=len(abs_error[0]),
            map_seconds_median=round(median(costs),2) if costs else None,
            map_seconds_p95=round(sorted(costs)[math.ceil(.95*len(costs))-1],2) if costs else None)
    return {"revision":rows[0]["audit"]["versions"]["code"],"model_revision":rows[0]["audit"]["versions"]["model_revision"]["declared"],
            "cohorts":output}

if __name__=="__main__":
    from pathlib import Path
    p=argparse.ArgumentParser();p.add_argument("run",type=Path);p.add_argument("out",type=Path);a=p.parse_args()
    a.out.write_text(json.dumps(compile_results(a.run),ensure_ascii=False,indent=2)+"\n",encoding="utf8")
