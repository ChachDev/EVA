"""Build a shareable aggregate report from private experiment files. No transcript/IDs are exported."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
from zipfile import ZipFile
from benchmarks.audit_compare import report, read
from benchmarks.audit_run import ROOT
from benchmarks.audit_pairs import cases

def summarize(base,new,sources):
    rows0={r["cid"]:r for r in base}
    old=[rows0[r["cid"]] for r in new]
    if len({r["cid"] for r in new})!=len(new) or any(a["gold"]!=b["gold"] for a,b in zip(old,new)):
        raise ValueError("Unpaired inputs")
    if any(a.get("input_sha256") and b.get("input_sha256") and a["input_sha256"]!=b["input_sha256"] for a,b in zip(old,new)):
        raise ValueError("Different serialized model inputs")
    def clean(rows):
        m,totals,truth=report(rows,sources)
        errors={(cid,key) for cid,key,_,_ in m.pop("errors")}
        return m,totals,truth,errors
    a,at,truth,ae=clean(old);b,bt,_,be=clean(new)
    common=[k for k in at if at[k] is not None and bt[k] is not None and truth[k] is not None]
    from collections import Counter
    return {"n":len(new),"baseline":a,"current":b,"common_numeric":len(common),
            "common_mae":[round(statistics.mean(abs(t[k]-truth[k]) for k in common),3) for t in (at,bt)] if common else [],
            "fixed_by_criterion":dict(Counter(k for _,k in ae-be)),
            "regressed_by_criterion":dict(Counter(k for _,k in be-ae))}

def synthetic_anchors(rows):
    by_id={c["id"]:c for c,_,_ in cases()}
    correct=0
    for row in rows:
        c=by_id[row["cid"]];k,expected=next(iter(row["gold"].items()))
        required={m["id"] for m in c["messages"] if m["speaker"]=="operator" or (k=="identity" and m["speaker"]=="system")}
        d=next(d for d in row["response"]["criteria"] if d["criterion_id"]==k)
        ids={mid for e in d["evidence"] for mid in e["message_ids"]}
        correct+=int(d["status"]=="scored" and d["value"] is expected and required<=ids)
    return {"n":len(rows),"correct_label_and_required_acts":correct}


def main():
    p=argparse.ArgumentParser();p.add_argument("--run-dir",type=Path,default=ROOT/"data/audit-components")
    p.add_argument("--dev",type=Path,default=ROOT/".tmp/dev-ml-repair-2111/dev.json")
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args();r=args.run_dir
    sources={row["conversation_id"]:{f"m{i:04d}" for i in range(1,len(row["transcript"])+1)}
             for row in json.loads(args.dev.read_text(encoding="utf-8-sig"))["data"]["records"]}
    sources.update({c["id"]:{m["id"] for m in c["messages"]} for c,_,_ in cases()})
    pairs=read(r/"pairs-baseline.jsonl") if (r/"pairs-baseline.jsonl").exists() else []
    human=read(r/"wire-human-baseline.jsonl")
    frozen=ROOT/"data/consultation-v1.1/sealed/blind15-consultation-v1.1.zip"
    timing=ROOT/"data/consultation-v1.1/timing.jsonl"
    times={row["cid"]:row["seconds"] for row in read(timing) if row["group"]=="blind"} if timing.exists() else {}
    archive=[]
    if frozen.exists():
        with ZipFile(frozen) as z:
            archive=[{**row,"response":json.loads(z.read(row["cid"]+".json"))["original"],"seconds":times.get(row["cid"]),
                      "calls":None,"tokens":None,"gpu_mib_after":None,"input_sha256":None} for row in human]
    result={"human":{},"synthetic":{},"ablation":{},"voice":{}}
    for name,pa,hu in [("rubric-v1","pairs-experiment","human-experiment"),
                       ("events-v2","v2-pairs","v2-human"),("source-events","final-pairs","final-human")]:
        for label,filename,base,n in [("synthetic",pa,pairs,18),("human",hu,archive,15)]:
            if not base or not (r/(filename+".jsonl")).exists():continue
            rows=read(r/(filename+".jsonl"))
            if len(rows)!=n:raise ValueError(f"Incomplete {filename}")
            result[label][name]=summarize(base,rows,sources)
    for f in sorted(r.glob("*ablation-*.jsonl")):
        rows=read(f)
        if len(rows)!=6:raise ValueError(f"Incomplete {f.name}")
        controls=read(r/"wire-pairs-baseline.jsonl") if f.stem.startswith("wire-ablation") else pairs
        m=summarize(controls,rows,sources)
        result["ablation"][f.stem]={**m["current"]["groups"]["all"],"seconds_median":m["current"]["seconds_median"],
                                    "calls":m["current"]["calls"],"tokens":m["current"]["tokens"]}
    for f in [r/"voice-baseline.jsonl",r/"voice-trace-policy-score-sentinel.jsonl",r/"v2-voice.jsonl",r/"final-voice.jsonl"]:
        if not f.exists():continue
        rows=read(f)
        if len(rows)!=5:raise ValueError(f"Incomplete {f.name}")
        result["voice"][f.stem]={"n":len(rows),"seconds_median":round(statistics.median(x["seconds"] for x in rows),2),
              "seconds_max":round(max(x["seconds"] for x in rows),2),
              "resolved":sum(c["status"]=="scored" for x in rows for c in x["response"]["criteria"]),
              "total_rows":sum(len(x["response"]["criteria"]) for x in rows)}
    result["production_order"]={}
    for domain,n in [("human",15),("pairs",18),("voice",5)]:
        control=read(r/f"wire-{domain}-baseline.jsonl");candidate=read(r/f"wire-{domain}-final.jsonl")
        if len(control)!=n or len(candidate)!=n:raise ValueError(f"Incomplete wire {domain}")
        result["production_order"][domain]=summarize(control,candidate,sources)
        if domain=="human" and archive:
            archived={row["cid"]:row["response"]["criteria"] for row in archive}
            comparison=[(a,b) for row in control for a,b in zip(archived[row["cid"]],row["response"]["criteria"])]
            result["archive_vs_wire"]={"n":len(comparison),"same_labels":sum((a["status"],a["value"])==(b["status"],b["value"]) for a,b in comparison),
                                        "same_evidence":sum(a["evidence"]==b["evidence"] for a,b in comparison)}
        elif domain=="pairs":
            result["production_order"][domain]["required_anchors"]=[synthetic_anchors(v) for v in (control,candidate)]
        elif domain=="voice":
            result["production_order"][domain]["resolved"]=[sum(c["status"]=="scored" for x in rows for c in x["response"]["criteria"])
                                                          for rows in (control,candidate)]
    if (r/"wire-resources.jsonl").exists():
        telemetry=read(r/"wire-resources.jsonl")
        result["resources"]={key:[min(v),max(v)] for key in telemetry[0] if (v:=[s[key] for s in telemetry if s[key] is not None])}
    if (r/"stage-usage.json").exists():
        result["example_stage_usage"]=json.loads((r/"stage-usage.json").read_text(encoding="utf8"))
    result["fingerprints"]={"dev_envelope":hashlib.sha256(args.dev.read_bytes()).hexdigest()}
    if frozen.exists():
        result["fingerprints"]["archived_control"]=hashlib.sha256(frozen.read_bytes()).hexdigest()
    for name in ("verified-challenges.json","challenges.json"):
        if (r/name).exists():
            result["challenges"]=json.loads((r/name).read_text(encoding="utf8"));break
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
if __name__=="__main__":main()
