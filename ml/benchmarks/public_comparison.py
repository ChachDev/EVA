"""Recount all competitors' deposited outputs against the identical 80 labels."""
import argparse
import json
from pathlib import Path
from statistics import mean

from benchmarks.audit_run import CHECK
from benchmarks.ledger_trial import public_holdout
from benchmarks.share_report import scores, same, total


def foreign(artifact):
    by_code = {f"{i:02d}":r["id"] for i,r in enumerate(CHECK["criteria"],1)}
    report = artifact["report"]
    rows = report.get("dialogs_detail", report.get("dialogs"))
    out = {}
    for row in rows:
        labels={}
        for criterion in row["criteria"]:
            key=by_code[criterion["criterion_id"]]
            val=criterion["value"]
            if val is None:
                val="review"
            elif val in ("YES","NO","NA"):
                val={"YES":True,"NO":False,"NA":None}[val]
            elif isinstance(val,str):
                val=int(val)
            labels[key]=val
        assert labels.keys()==set(by_code.values())
        assert total(labels,CHECK["criteria"])==row["total"]
        out[row["conversation_id"]]=labels
    return out


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dir",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--extra",type=Path,help="Another locally measured model on the same ten")
    p.add_argument("--reader",type=Path,help="Additional per-criterion reader; diagnostic on this previously seen benchmark")
    a=p.parse_args()
    targets={req["conversation"]["id"]:g for req,g,*_ in public_holdout()}
    configs={}
    for art in json.loads((a.dir/"public-reference-runs.json").read_text(encoding="utf8")):
        configs[art["file"]]=foreign(art)
    rows=[json.loads(line) for line in (a.dir/"issai-public.jsonl").read_text(encoding="utf8").splitlines()]
    configs["EVA ISSAI primary"]={r["id"]:scores(r["primary"]) for r in rows}
    for profile in ("spans","spans_examples"):
        configs["EVA ISSAI "+profile]={r["id"]:scores(r["variants"][profile]["response"]) for r in rows}
    resolution=a.dir/"resolution-public.jsonl"
    if resolution.is_file():
        additional=[json.loads(x) for x in resolution.read_text(encoding="utf8").splitlines()]
        assert all(scores(r["primary"])==configs["EVA ISSAI primary"][r["id"]] for r in additional)
        configs["EVA ISSAI resolution-v2"]={r["id"]:scores(r["variants"]["resolution"]["response"]) for r in additional}
    if a.extra:
        extra=[json.loads(x) for x in a.extra.read_text(encoding="utf8").splitlines()]
        configs["EVA "+extra[0]["model"]]={r["id"]:scores(r["primary"]) for r in extra}
    if a.reader:
        additional=[json.loads(x) for x in a.reader.read_text(encoding="utf8").splitlines()]
        assert all(scores(r["primary"])==configs["EVA ISSAI primary"][r["id"]] for r in additional)
        configs["EVA ISSAI criterion reader"]={r["id"]:scores(r["variants"]["reader"]["response"]) for r in additional}
    assert all(rows.keys()==targets.keys() for rows in configs.values())
    totals={name:{cid:total(labels,CHECK["criteria"]) for cid,labels in rows.items()} for name,rows in configs.items()}
    common=[cid for cid in targets if all(ts[cid] is not None for ts in totals.values())]
    result={"common_chats":common,"models":{}}
    for name,rows in configs.items():
        match=sum(same(v,targets[cid][k]) for cid,labels in rows.items() for k,v in labels.items())
        complete=sum(t is not None for t in totals[name].values())
        result["models"][name]={"correct":match,"n":80,"complete":complete,"common_mae":round(mean(
            abs(totals[name][cid]-total(targets[cid],CHECK["criteria"])) for cid in common),3),
            "criteria":{k:sum(same(labels[k],targets[cid][k]) for cid,labels in rows.items()) for k in next(iter(targets.values()))}}
    with a.out.open("x",encoding="utf8") as f:
        json.dump(result,f,ensure_ascii=False,indent=2)


if __name__=="__main__":
    main()
