"""Paired comparison of two frozen primary runs, including their common numeric conversations."""
import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import mean

from benchmarks.audit_run import CHECK
from benchmarks.share_report import same, scores, total


def compare(baseline, other):
    def index(path):
        rows=list(map(json.loads,path.read_text(encoding="utf8").splitlines()))
        by_id={row["id"]:row for row in rows}
        if len(rows)!=len(by_id) or len(rows)!={"public":10,"pilot":12,"gold16":16}[rows[0]["cohort"]]:
            raise ValueError("Incomplete or duplicate cohort")
        return by_id
    a,b=index(baseline),index(other)
    assert a.keys()==b.keys()
    counts=Counter()
    by_lang={}
    errors={k:[] for k in ("issai","plain")}
    for cid,first in a.items():
        second=b[cid]
        gold=first["gold"]
        assert (first["checklist"],first["cohort"],gold)==(second["checklist"],second["cohort"],second["gold"])
        left,right=scores(first["primary"]),scores(second["primary"])
        totals=[total(v,CHECK["criteria"]) for v in (gold,left,right)]
        counts.update(chats=1,issai_numeric=int(totals[1] is not None),plain_numeric=int(totals[2] is not None))
        if None not in totals:
            for name,t in zip(errors,totals[1:],strict=True):
                errors[name].append(abs(t-totals[0]))
        lang=by_lang.setdefault(first["language"],Counter())
        for key,target in gold.items():
            ls,rs=same(left[key],target),same(right[key],target)
            counts.update(n=1,issai=int(ls),plain=int(rs),fixed=int(rs and not ls),broken=int(ls and not rs))
            lang.update(n=1,issai=int(ls),plain=int(rs))
    return {"counts":counts,"language":by_lang,"common_n":len(errors["issai"]),
            "common_mae":{k:round(mean(x),3) if x else None for k,x in errors.items()}}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dir",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args()
    report={cohort:compare(args.dir/f"issai-{cohort}.jsonl",args.dir/f"plain-{cohort}.jsonl")
            for cohort in ("public","gold16","pilot")}
    with args.out.open("x",encoding="utf8") as f:
        json.dump(report,f,ensure_ascii=False,indent=2)


if __name__=="__main__":
    main()
