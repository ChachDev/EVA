"""Publish only aggregate paired quality metrics; no transcripts or evidence are exported."""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
from statistics import mean, median

from benchmarks.audit_compare import read, total
from benchmarks.audit_run import ROOT


def labels(response):
    return {c["criterion_id"]: c["value"] if c["status"] == "scored" else "review" for c in response["criteria"]}


def cohort(rows, archived):
    counts = Counter()
    criteria = defaultdict(Counter)
    languages = defaultdict(Counter)
    results = [Counter(), Counter()]
    mae = [[], []]
    elapsed = []
    states = Counter()
    for row in rows:
        audit = row["audit"]
        original = audit["verification"]["baseline"]
        historical = archived[row["cid"]]
        if historical["audit"]["verification"]["baseline"] != original or historical["gold"] != row["gold"]:
            raise ValueError("Unpaired control or changed gold")
        old, new = labels(original), labels(row["response"])
        earlier = labels(historical["response"])
        if any(earlier[k] != new[k] for k in ("gross_errors","contact","empathy")):
            raise ValueError("Non-target result differs from baseline-plus")
        gold = row["gold"]
        delta = 0
        for k,y in gold.items():
            old_ok, new_ok = old[k] == y, new[k] == y
            counts["previous_profile_correct"] += int(earlier[k] == y)
            delta += int(new_ok) - int(old_ok)
            for stats in (criteria[k], languages[row["language"]], counts):
                stats.update(n=1, baseline=int(old_ok), candidate=int(new_ok),
                             fixed=int(new_ok and not old_ok), regressed=int(old_ok and not new_ok))
            for stats,v in zip(results,(old[k],new[k])):
                stats.update(scored=int(v != "review"), false_na=int(v is None and y is not None),
                             missed_na=int(y is None and v is not None))
                if k == "gross_errors":
                    stats.update(gross_positive=int(y is True), false_accusations=int(v is True and y is False),
                                 missed_violations=int(v is not True and y is True))
        counts["chats_up" if delta > 0 else "chats_down" if delta < 0 else "chats_same"] += 1
        expectation = total(gold)
        for stats,vals in zip(results,(old,new)):
            stats["complete"] += int(total(vals) is not None)
        if expectation is not None and total(old) is not None and total(new) is not None:
            for errors,vals in zip(mae,(old,new)):
                errors.append(abs(expectation-total(vals)))
        counts["supplemental_calls"] += row["calls"]
        if row["tokens"] is not None:
            counts["supplemental_tokens"] += row["tokens"]
        elapsed.append(row["seconds"])
        states[audit["verification"]["state"]] += 1
    return {"conversations":len(rows), "decisions":dict(counts), "criteria":dict(criteria), "languages":dict(languages),
            "baseline":results[0], "candidate":results[1], "common_numeric":len(mae[0]),
            "common_mae":[round(mean(e),3) if e else None for e in mae],
            "verification_median_seconds":round(median(elapsed),2),
            "verification_p95_seconds":round(sorted(elapsed)[math.ceil(.95*len(rows))-1],2), "verification_states":states}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--archive",type=Path,default=ROOT/"data/baseline-plus-audit-v1")
    args = p.parse_args()
    archives = {r["cid"]:r for n in ("human","pilot","pairs") for r in read(args.archive/(n+".jsonl"))}
    out = {}
    revisions = set()
    for name,n in [("develop",9),("reserved",6),("pilot",12),("pairs",18)]:
        rows = read(args.run/(name+".jsonl"))
        if len(rows) != n or len({r["cid"] for r in rows}) != n:
            raise ValueError("Incomplete cohort "+name)
        revisions.update(r["audit"]["versions"]["code"] for r in rows)
        out[name] = cohort(rows,archives)
    if len(revisions) != 1:
        raise ValueError("Different verifier revisions")
    out["revision"] = revisions.pop()
    args.out.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n", encoding="utf8")


if __name__ == "__main__":
    main()
