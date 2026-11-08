"""Aggregate the measured adapter outcomes without exporting private transcript text."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

import numpy as np

from benchmarks.audit_run import CHECK
from benchmarks.share_report import scores, total
from training.closing07.data import ML, LABELS


def rows(file):
    data=[json.loads(line) for line in file.read_text(encoding="utf8").splitlines()]
    result={r["id"]:r for r in data}
    if len(result)!=len(data):
        raise ValueError("Duplicate example")
    return result


def count(actual,predicted):
    assert set(actual)==set(predicted)
    confusion=Counter((actual[k],v) for k,v in predicted.items())
    n=len(actual)
    return dict(n=n,exact=sum(actual[k]==v for k,v in predicted.items()),
                review=sum(v=="review" for v in predicted.values()),
                false_na=sum(actual[k]!="C" and v=="C" for k,v in predicted.items()),
                missed_na=sum(actual[k]=="C" and v!="C" for k,v in predicted.items()),
                confusion=[[confusion[y,p] for p in LABELS] for y in LABELS])


def evaluate(out):
    before=rows(out/"before.jsonl")
    after=rows(out/"after.jsonl")
    native_off=rows(out/"native-off.jsonl")
    receipt=json.loads((out/"fit.json").read_text(encoding="utf8"))
    assert sha256((out/"closing07-r2.gguf").read_bytes()).hexdigest()==receipt["adapter_sha256"]
    delta=np.array(receipt["optimizer_coefficients"])
    tests={k:r for k,r in before.items() if r["split"]!="train"}
    assert len(before)==80 and len(tests)==44 and tests.keys()==after.keys()==native_off.keys()
    train=[r for r in before.values() if r["split"]=="train"]
    train_logits=np.asarray([r["logp"] for r in train])
    train_labels=[LABELS.index(r["label"]) for r in train]
    changed=train_logits+(train_logits[:,:2]-train_logits[:,2:])@delta.T
    def train_stats(logits):
        return dict(exact=int(np.sum(np.argmax(logits,axis=1)==train_labels)),n=len(train_labels),
                    cross_entropy=round(float(np.mean(np.logaddexp.reduce(logits,axis=1)-logits[np.arange(len(train_labels)),train_labels])),6))
    result={}
    for split,n in (("held",18),("public10",10),("public16",16)):
        group={k:r for k,r in tests.items() if r["split"]==split}
        assert len(group)==n
        gold={k:r["label"] for k,r in group.items()}
        native={}; original={}; predicted={}; deviations=[]
        for k,r in group.items():
            assert (r["input_hash"],r["label"])==(after[k]["input_hash"],after[k]["label"])==(native_off[k]["input_hash"],native_off[k]["label"])
            z=np.array(native_off[k]["logp"])
            lifted=z+delta@(z[:2]-z[2])
            measured=np.array(after[k]["logp"])
            deviations.append(np.max(np.abs((lifted-lifted.mean())-(measured-measured.mean()))))
            predicted[k]=LABELS[int(np.argmax(lifted))]
            native[k]=LABELS[int(np.argmax(measured))]
            original[k]=LABELS[int(np.argmax(z))]
        result[split]={"before":count(gold,original),"native_after":count(gold,native),
                       "native_matches_algebra":sum(native[k]==predicted[k] for k in group),
                       "max_centered_logit_difference":round(float(max(deviations)),4),
                       "fixed":sum(original[k]!=gold[k] and native[k]==gold[k] for k in group),
                       "broken":sum(original[k]==gold[k] and native[k]!=gold[k] for k in group)}
        if split=="held":
            for lang in ("ru","kz","en"):
                ids=[k for k,r in group.items() if r["language"]==lang]
                result[split][lang]=[sum(original[k]==gold[k] for k in ids),
                                     sum(native[k]==gold[k] for k in ids),len(ids)]
            continue
        file="plain-public.jsonl" if split=="public10" else "plain-gold16.jsonl"
        history=rows(ML/"data/ledger-v1"/file)
        assert history.keys()==group.keys()
        decode={"A":True,"B":False,"C":None}
        original_hits=patched_hits=0
        original_totals=[];patched_totals=[];expected_totals=[]
        closing={}
        for k,record in history.items():
            values=scores(record["primary"])
            target=record["gold"]
            assert target["closing"]==decode[gold[k]]
            val=values["closing"]
            closing[k]=({True:"A",False:"B",None:"C"}.get(val,"review"))
            original_hits+=sum(type(v)==type(target[c]) and v==target[c] for c,v in values.items())
            original_totals.append(total(values,CHECK["criteria"]))
            values["closing"]=decode[native[k]]
            patched_hits+=sum(type(v)==type(target[c]) and v==target[c] for c,v in values.items())
            patched_totals.append(total(values,CHECK["criteria"]))
            expected_totals.append(total(target,CHECK["criteria"]))
        common=[i for i,(a,b,y) in enumerate(zip(original_totals,patched_totals,expected_totals,strict=True))
                if a is not None and b is not None and y is not None]
        def mae(totals):
            return round(sum(abs(totals[i]-expected_totals[i]) for i in common)/len(common),3) if common else None
        result[split]["production"]={"c07":count(gold,closing),"eight_exact":original_hits,
            "offline_eight_exact_if_only_c07_replaced":patched_hits,"denominator":8*n,
            "numeric_before":sum(v is not None for v in original_totals),
            "offline_numeric_after":sum(v is not None for v in patched_totals),"mae_common_chats":len(common),
            "original_mae":mae(original_totals),"offline_mae_if_replaced":mae(patched_totals)}
        result[split]["public_differences"]=[
            {"id":k,"target":gold[k],"same_prompt_before":original[k],"native_after":native[k],"production":closing[k]}
            for k in group if native[k]!=original[k] or native[k]!=closing[k]]
    return {"fit":receipt,"labels":{"A":"Да","B":"Нет","C":"N/A"},
            "train_computed":{"before":train_stats(train_logits),"after":train_stats(changed)},"results":result}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    result=evaluate(args.out)
    with (args.out/"metrics.json").open("x",encoding="utf8") as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result["results"],ensure_ascii=True))
