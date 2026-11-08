"""Aggregate paired v6/v7 scores. Export no transcript or model explanation."""
from collections import Counter
import json
from statistics import mean

from benchmarks.audit_run import CHECK
from benchmarks.friend_v7 import OUT, PIN
from benchmarks.share_report import same, total


def report(rows,cohort):
    groups={"all":lambda r,k:True}
    if cohort=="dev15":
        groups.update(unanimous=lambda r,k:k in r["unanimous"],disputed=lambda r,k:k not in r["unanimous"])
    variants=("base","v6","v7")
    stats={s:{n:Counter() for n in variants} for s in groups}
    by_criterion={n:{} for n in variants}
    by_language={n:{} for n in variants}
    numeric={n:0 for n in variants}
    errors={n:[] for n in variants}
    differences=[]
    for row in rows:
        gold=row["gold"]
        for key,target in gold.items():
            for n in variants:
                pred=row["pred"][n][key]
                correct=same(pred,target)
                for split,applies in groups.items():
                    if applies(row,key):
                        stats[split][n].update(n=1,correct=int(correct),review=int(pred=="review"),
                            false_na=int(pred is None and target is not None),missed_na=int(target is None and pred is not None))
                for table,k in [(by_criterion,key),(by_language,row["language"])]:
                    table[n].setdefault(k,Counter()).update(n=1,correct=int(correct))
            b,v6,v7=(row["pred"][v][key] for v in variants)
            if not same(b,v7) or not same(v6,v7):
                differences.append(dict(id=row["id"],criterion=key,gold=target,base=b,v6=v6,v7=v7))
        if len(gold)==8:
            y=total(gold,CHECK["criteria"])
            t={n:total(row["pred"][n],CHECK["criteria"]) for n in variants}
            for n in variants:
                numeric[n]+=int(t[n] is not None)
            if y is not None and all(v is not None for v in t.values()):
                for n in variants:
                    errors[n].append(abs(t[n]-y))
    return dict(counts=stats,criteria=by_criterion,languages=by_language,changed=differences,numeric=numeric,
                mae={n:{"n":len(errors[n]),"value":round(mean(errors[n]),3) if errors[n] else None} for n in variants},
                calls={v:sum(r["cost"][v][0] for r in rows) for v in variants[1:]},
                distinct_calls=sum(r["distinct_inference"] for r in rows))


if __name__=="__main__":
    result={"archive_sha256":PIN,"cohorts":{}}
    for cohort,count in (("sequence",12),("public10",10),("public16",16),("dev15",15)):
        rows=list(map(json.loads,(OUT/(cohort+".jsonl")).read_text(encoding="utf8").splitlines()))
        if len({r["id"] for r in rows})!=len(rows) or len(rows)!=count:
            raise ValueError(f"Incomplete {cohort}")
        result["cohorts"][cohort]=report(rows,cohort)
    with (OUT/"paired-metrics.json").open("x",encoding="utf8") as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
