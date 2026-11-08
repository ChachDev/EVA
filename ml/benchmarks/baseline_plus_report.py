"""Aggregate the frozen baseline-first trial. No transcripts, quotes or conversation IDs are exported."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics

from benchmarks.audit_compare import read, report, total
from benchmarks.audit_pairs import cases
from benchmarks.audit_release_report import synthetic_anchors
from benchmarks.audit_run import ROOT


def summarize(name,rows,sources,controls):
    comparisons=[]
    baseline=[]
    matches=Counter()
    checks=Counter()
    states=Counter()
    for r in rows:
        a=r["audit"]; v=a["verification"]; b=v["baseline"]
        costs=a["stage_usage"]["baseline"]
        baseline.append({**r,"response":b,"seconds":costs["seconds"],"calls":costs["calls"],"tokens":costs["tokens"]})
        states[v["state"]]+=1
        for k,check in v["checks"].items():
            checks[(k,check["outcome"])]+=1
        if controls is not None:
            control=controls[r["cid"]]
            if control.get("input_sha256") and control["input_sha256"]!=r["input_sha256"]:
                raise ValueError("Mismatched serialized input")
            matches["conversations"]+=1
            matches["same_response"]+=int(control["response"]==b)
            for old,new in zip(control["response"]["criteria"],b["criteria"],strict=True):
                matches["total_rows"]+=1
                if old["criterion_id"] != new["criterion_id"]:
                    raise ValueError("Control criteria have a different order")
                matches["same_label"]+=int((old["status"],old["value"])==(new["status"],new["value"]))
                matches["same_evidence"]+=int(old["evidence"]==new["evidence"])
    for group in (baseline,rows):
        summary,totals,truth=report(group,sources)
        errors={(cid,crit) for cid,crit,_,_ in summary.pop("errors")}
        summary["resolved_all_rows"]=sum(c["status"]=="scored" for r in group for c in r["response"]["criteria"])
        summary["complete_scores"] = sum(
            total({c["criterion_id"]: c["value"] if c["status"]=="scored" else "review"
                   for c in r["response"]["criteria"]}) is not None for r in group)
        comparisons.append((summary,totals,truth,errors))
    (a,a_total,truth,a_errors),(b,b_total,_,b_errors)=comparisons
    common=[k for k in a_total if a_total[k] is not None and b_total[k] is not None and truth[k] is not None]
    out={"n":len(rows),"baseline":a,"verified":b,"historical_control":dict(matches),
         "fixed":dict(Counter(k for _,k in a_errors-b_errors)),"regressed":dict(Counter(k for _,k in b_errors-a_errors)),
         "common_numeric":len(common),
         "common_mae":[round(statistics.mean(abs(t[k]-truth[k]) for k in common),3) for t in (a_total,b_total)] if common else [],
         "supplemental_states":dict(states),
         "checks":{k:dict((state,checks[k,state]) for crit,state in checks if crit==k) for k,_ in checks},
         "extra_calls":sum(r["audit"]["stage_usage"]["verification"]["calls"] for r in rows),
         "extra_seconds":round(sum(r["audit"]["stage_usage"]["verification"]["seconds"] for r in rows),2)}
    if name=="pairs":
        out["required_anchors"]=[synthetic_anchors(g) for g in (baseline,rows)]
    return out


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--run",required=True,type=Path)
    p.add_argument("--controls",type=Path,default=ROOT/"data/audit-components")
    p.add_argument("--dev",type=Path,default=ROOT/".tmp/dev-ml-repair-2111/dev.json")
    p.add_argument("--out",required=True,type=Path)
    args=p.parse_args()
    sources={r["conversation_id"]:{f"m{i:04d}" for i in range(1,len(r["transcript"])+1)}
             for r in json.loads(args.dev.read_text(encoding="utf-8-sig"))["data"]["records"]}
    sources.update({c["id"]:{m["id"] for m in c["messages"]} for c,_,_ in cases()})
    result={}
    ids={}
    manifest = None
    expected_labels = {"human":8, "pilot":8, "pairs":1, "voice":0}
    for cohort,count in [("human",15),("pilot",12),("pairs",18),("voice",5)]:
        rows=read(args.run/(cohort+".jsonl"))
        ids[cohort]={r["cid"] for r in rows}
        if len(rows)!=count or len(ids[cohort])!=count: raise ValueError("Incomplete "+cohort)
        for row in rows:
            versions = row["audit"]["versions"]
            shared = {k: versions[k] for k in ("model","model_revision","baseline_variant","profile","prompt",
                                                "code","policy_version","generation","components","checklist","rules")}
            if manifest is None:
                manifest = shared
            if shared != manifest or versions["profile"] != "baseline-plus":
                raise ValueError("Different evaluator configurations in the same run")
            if len(row["gold"]) != expected_labels[cohort]:
                raise ValueError("Wrong reference coverage in "+cohort)
        if cohort == "pilot":
            control = {}
            for cid in ids[cohort]:
                original = json.loads((ROOT/"data/consultation-v1.1/pilot"/f"pilot-1-{cid}.json").read_text(encoding="utf-8-sig"))["original"]
                control[cid] = {"response": original}
        else:
            control={r["cid"]:r for r in read(args.controls/("wire-"+cohort+"-baseline.jsonl"))}
        result[cohort]=summarize(cohort,rows,sources,control)
    if ids["human"] & ids["pilot"]:raise ValueError("Development and evaluation groups overlap")
    result["manifest"] = manifest
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
if __name__=="__main__": main()
