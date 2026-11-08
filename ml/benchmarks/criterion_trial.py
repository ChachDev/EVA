"""Paired full evaluation on a second public synthetic suite, using the fixed rubric."""
import argparse
import json
from pathlib import Path
from time import perf_counter

from benchmarks.audit_run import ROOT
from benchmarks.ledger_trial import public_holdout
from eva_ml.consultation_v11 import select_provider
from eva_ml.contracts import Contracts
from eva_ml.criterion_reader import CriterionReader
from eva_ml.engine import LocalEngine
from eva_ml.issai import digest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port",type=int,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--suite",choices=["gold16","public"],default="gold16")
    args=p.parse_args()
    if not args.out.resolve().is_relative_to(ROOT/"data"):
        p.error("Use ml/data for outputs")
    model="Qwen3.5-9B-Kazakh.Q5_K_M"
    engine=LocalEngine(args.port,model)
    assert model in {r["id"] for r in engine.request("/v1/models")["data"]}
    primary=select_provider(engine,"consultation-v1.1")
    candidate=CriterionReader(primary)
    contracts=Contracts()
    with args.out.open("x",encoding="utf8") as out:
        suite=public_holdout("public-gold16.json",16) if args.suite=="gold16" else public_holdout()
        for n,(req,gold,_,_,_) in enumerate(suite):
            req=json.loads(json.dumps(req,sort_keys=True))
            contracts.validate_request(req)
            order=[("primary",primary),("reader",candidate)]
            outcomes={}
            for k,provider in order if n%2==0 else reversed(order):
                t=perf_counter()
                result=provider.evaluate(req)
                outcomes[k]=(result,perf_counter()-t)
                contracts.validate_response(req,result)
            row={"model":model,"id":req["conversation"]["id"],"language":req["conversation"]["language"],
                 "cohort":args.suite,"family":None,"gold":gold,"checklist":digest(req["checklist"]),
                 "primary":outcomes["primary"][0],"base_seconds":outcomes["primary"][1],"variants":{"reader":{
                     "response":outcomes["reader"][0],"seconds":outcomes["reader"][1],"independent":True,
                     "sha":candidate.version,"details":{"state":"evaluated"}}}}
            out.write(json.dumps(row,ensure_ascii=False)+"\n")
            out.flush()
            print(args.suite,n+1,flush=True)


if __name__=="__main__":
    main()
