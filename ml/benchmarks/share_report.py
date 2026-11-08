"""Score sealed outputs against all four annotators and the fixed arbitration rule."""
import argparse
from collections import Counter,defaultdict
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from statistics import mean,median
import json
from pathlib import Path

from .share_trial import PRIVATE,ROOT,prepare_inputs,read_rows

def label(value):
    if value in ("Да","Нет","N/A"):return {"Да":True,"Нет":False,"N/A":None}[value]
    return int(value)

def scores(result):
    return {r["criterion_id"]:r["value"] if r["status"]=="scored" else "review" for r in result["criteria"]}

def same(a,b):
    return isinstance(a,bool)==isinstance(b,bool) and a==b

def total(values, checklist):
    active=[(r,values[r["id"]]) for r in checklist if r["type"]!="flag"]
    if "review" in values.values():return None
    weight=sum(r["weight"] for r,v in active if v is not None)
    if not weight:return None
    pts=sum(Decimal(r["weight"])*Decimal(int(v) if type(v) is bool else str(v)) /
            Decimal(max(r["scale"]) if r["scale"] else 1) for r,v in active if v is not None)
    return float((100*pts/weight).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP))

def summarize(sources,predictions):
    reference=json.loads((PRIVATE/"blind15-comparison-20261108/reference.json").read_text("utf8"))
    ballots={(r["chat"],r["criterion"]):[label(r[str(i)]) for i in range(1,5)] for r in reference}
    originals={(r["chat"],r["criterion"]):label(r["gold"]) for r in reference}
    candidates=defaultdict(lambda:defaultdict(Counter))
    criteria=defaultdict(lambda:defaultdict(Counter))
    language=defaultdict(lambda:defaultdict(Counter))
    metrics=defaultdict(lambda:defaultdict(Counter))
    errors=defaultdict(lambda:defaultdict(list))
    changes=defaultdict(list)
    gold_by_id={r["cid"]:r["gold"] for r in read_rows(PRIVATE/"baseline-plus-audit-v1/pilot.jsonl")}
    expected_ids={s["cid"] for s in sources}
    if any(set(rows)!=expected_ids for rows in predictions.values()):raise ValueError("Incomplete predictions")
    for sample in sources:
        cid=sample["cid"]; cohort=sample["cohort"];rubric=sample["request"]["checklist"]["criteria"]
        groups=[cohort] if cohort=="pilot" else ["book",cohort]
        base=scores(sample["primary"])
        gold=gold_by_id[cid] if cohort=="pilot" else {k:originals[(cid,k)] for k in base}
        vals={"base":base,**{n:scores(rows[cid]["response"]) for n,rows in predictions.items()}}
        numeric={n:total(v,rubric) for n,v in vals.items()}
        expected=total(gold,rubric)
        for name,values in vals.items():
            for group in groups:
                stats=metrics[group][name]
                stats["chats"]+=1;stats["full"]+=int(numeric[name] is not None)
                if expected is not None and all(v is not None for v in numeric.values()):
                    errors[group][name].append(abs(numeric[name]-expected))
            for k,y in gold.items():
                pred=values[k]
                correct=same(pred,y)
                correct_base=same(base[k],y)
                for group in groups:
                    stats=metrics[group][name]
                    stats.update(n=1, correct=int(correct), fixed=int(correct and not correct_base),
                        worse=int(not correct and correct_base),scored=int(pred!="review"),
                        false_na=int(pred is None and y is not None),missed_na=int(y is None and pred is not None))
                    if k=="gross_errors":
                        stats.update(positives=int(y is True),fp=int(pred is True and y is False),fn=int(y is True and pred is not True))
                    criteria[group+"/"+k][name].update(n=1,correct=int(correct))
                    language[group+"/"+sample["request"]["conversation"]["language"]][name].update(n=1,correct=int(correct))
                if cohort!="pilot":
                    votes=ballots[(cid,k)]
                    bucket="unanimous" if all(same(x,votes[0]) for x in votes) else "disputed"
                    candidates[bucket][name].update(n=1,correct=int(correct))
                    for source,v in zip(("Gemini","ChatGPT","Grok","DeepSeek"),votes,strict=True):
                        candidates[source][name].update(n=1,correct=int(same(pred,v)))
                if name!="base" and not same(pred,base[k]):
                    changes[name].append(dict(cohort=cohort,language=sample["request"]["conversation"]["language"],
                                         criterion=k,before=base[k],after=pred,reference=y))
    numeric={group:{n:{"n":len(a),"mae":round(mean(a),3) if a else None} for n,a in sides.items()} for group,sides in errors.items()}
    timing={n:{"calls":sum(r["calls"] for r in rows.values()),"median_seconds":round(median(r["seconds"] for r in rows.values()),3),
               "max_seconds":round(max(r["seconds"] for r in rows.values()),3)} for n,rows in predictions.items()}
    return dict(groups=metrics,criteria=criteria,languages=language,annotation=candidates,numeric=numeric,timing=timing,changes=changes)

def main():
    p=argparse.ArgumentParser();p.add_argument("--zip",required=True,type=Path);p.add_argument("--eva",type=Path);p.add_argument("--out",required=True,type=Path)
    args=p.parse_args()
    preds={}
    for name,path in (("zip",args.zip),("eva",args.eva)):
        if path:
            rows=read_rows(path);preds[name]={r["cid"]:r for r in rows}
            if len(rows)!=len(preds[name]):raise ValueError("Duplicate prediction")
    report=summarize(prepare_inputs(),preds)
    report["prediction_hashes"]={name:sha256(path.read_bytes()).hexdigest()
                                  for name,path in (("zip",args.zip),("eva",args.eva)) if path}
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
if __name__=="__main__":main()
