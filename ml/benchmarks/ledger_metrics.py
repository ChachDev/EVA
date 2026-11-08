"""Summarize frozen paired runs. Never treat a review as a correct label."""
import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from math import ceil
from pathlib import Path
from statistics import mean, median

from benchmarks.audit_run import CHECK
from benchmarks.share_report import scores, same, total


def summary(rows):
    names = ["primary", *rows[0]["variants"]]
    numeric = defaultdict(list)
    timings = defaultdict(list)
    extra = defaultdict(list)
    pairs = defaultdict(lambda: defaultdict(list))
    counts = {name:Counter() for name in names}
    by_criterion = {name:defaultdict(Counter) for name in names}
    by_lang = {name:defaultdict(Counter) for name in names}
    changes = []
    for row in rows:
        gold = row["gold"]
        results = {"primary":scores(row["primary"]), **{k:scores(v["response"]) for k,v in row["variants"].items()}}
        totals = {k:total(v,CHECK["criteria"]) for k,v in results.items()}
        expected = total(gold,CHECK["criteria"]) if len(gold)==8 else None
        for name, labels in results.items():
            stat = counts[name]
            stat["chats"] += 1
            stat["complete"] += totals[name] is not None
            stat["review_all"] += sum(v=="review" for v in labels.values())
            if expected is not None and all(t is not None for t in totals.values()):
                numeric[name].append(abs(expected-totals[name]))
            elapsed = 0.
            if name!="primary":
                variant = row["variants"][name]
                elapsed = variant["seconds"]
                extra[name].append(elapsed)
                stat["read_"+variant["details"]["state"]] += 1
            if row["base_seconds"] is not None:
                standalone = name != "primary" and row["variants"][name].get("independent")
                timings[name].append(elapsed if standalone else row["base_seconds"]+elapsed)
            for key, target in gold.items():
                pred, base = labels[key], results["primary"][key]
                correct = same(pred,target)
                stat.update(n=1,correct=int(correct),fixed=int(correct and not same(base,target)),
                            broken=int(not correct and same(base,target)),false_na=int(pred is None and target is not None),
                            missed_na=int(pred is not None and target is None))
                by_criterion[name][key].update(correct=int(correct),n=1)
                by_lang[name][row["language"]].update(correct=int(correct),n=1)
                if key=="consultation":
                    stat.update(false_max=int(pred==20 and target!=20), missed_max=int(target==20 and pred!=20),
                                target_low=int(isinstance(target,int) and target<20),
                                correct_low=int(isinstance(target,int) and target<20 and correct))
                    if row["family"] is not None:
                        pairs[name][(row["language"],row["family"])].append(correct)
                    if not same(pred,base):
                        changes.append(dict(id=row["id"],variant=name,gold=target,base=base,candidate=pred))
    for name,stat in counts.items():
        if pairs[name]:
            assert all(len(side)==2 for side in pairs[name].values())
            stat["pair_ok"] = sum(all(side) for side in pairs[name].values())
            stat["pairs"] = len(pairs[name])
    def timing(ts):
        return {"median":round(median(ts),2),"p95":round(sorted(ts)[ceil(.95*len(ts))-1],2)} if ts else None
    return dict(counts=counts,by_criterion=by_criterion,by_language=by_lang,changes=changes,
                mae={n:{"n":len(numeric[n]),"mae":round(mean(numeric[n]),3) if numeric[n] else None} for n in names},
                warm_total_s={n:timing(timings[n]) for n in names},extra_s={n:timing(extra[n]) for n in names})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", type=Path, nargs="+", required=True)
    p.add_argument("--out", type=Path, required=True)
    args=p.parse_args()
    report = {}
    for path in args.run:
        rows=[json.loads(line) for line in path.read_text(encoding="utf8").splitlines()]
        cohort=rows[0]["cohort"]
        assert len({r["id"] for r in rows})==len(rows)=={"synthetic":24,"public":10,"pilot":12,"gold16":16}[cohort]
        assert len({(r["model"],r["cohort"],r["checklist"]) for r in rows})==1
        profiles={k:{r["variants"][k]["sha"] for r in rows} for k in rows[0]["variants"]}
        assert all(len(h)==1 for h in profiles.values())
        report[path.name] = {"sha256":sha256(path.read_bytes()).hexdigest(),"model":rows[0]["model"],
                             "version":{k:next(iter(h)) for k,h in profiles.items()},**summary(rows)}
    with args.out.open("x",encoding="utf8") as out:
        json.dump(report,out,ensure_ascii=False,indent=2)
        out.write("\n")


if __name__ == "__main__":
    main()
