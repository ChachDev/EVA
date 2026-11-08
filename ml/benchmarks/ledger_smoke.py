"""Exercise the real HTTP endpoint and truth-source boundary with synthetic data only."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from benchmarks.ledger_cases import synthetic
from eva_ml.consultation_v11 import select_provider
from eva_ml.contracts import Contracts
from eva_ml.engine import LocalEngine
from eva_ml.issai import digest
from eva_ml.ledger_knowledge import ProductVerifier


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker",type=int,required=True)
    parser.add_argument("--service",type=int,required=True)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    req,_,_=next(synthetic())
    opener=build_opener(ProxyHandler({}))
    def post(payload):
        request=Request(f"http://127.0.0.1:{args.service}/v1/evaluate",
                        data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
        with opener.open(request,timeout=180) as response:
            assert response.status==200
            result=json.load(response)
        Contracts().validate_response(payload,result)
        return result
    result=post(req)
    assert any(w.startswith("request-ledger ") for w in result["warnings"])
    changed=deepcopy(req)
    c=next(c for c in changed["checklist"]["criteria"] if c["id"]=="consultation")
    changed["checklist"]["version"]+=1
    c.update(title="Представление",instruction="2 — оператор назвал имя, иначе 0.",scale=[0,2],
             allow_na=False,na_condition=None)
    other=post(changed)
    assert not any(w.startswith("request-ledger ") for w in other["warnings"])
    assert len(result["criteria"])==len(other["criteria"])==8
    worker=LocalEngine(args.worker,"Qwen3.5-9B-Kazakh.Q5_K_M")
    provider=select_provider(worker,"consultation-v1.1")
    probes=[]
    for disposition,answer in (("conflict","Доступно девять экспортов в день."),
                               ("consistent","Доступно три экспорта в день.")):
        case=deepcopy(req)
        conv=case["conversation"]
        conv["id"]="synthetic-product-"+disposition
        conv["messages"][0]["text"]="Сколько экспортов в день доступно в учебном тарифе Z?"
        conv["messages"][1]["text"]=answer
        scope=dict(entity="demo",product="Z",jurisdiction="lab",approved_by="synthetic fixture",
                   date_evidence="fixture date",conversation_date="2026-11-08",transcript_sha256=digest(conv))
        fact=dict(id="fixture-limit",entity="demo",product="Z",jurisdiction="lab",source="synthetic specification",
                  locator="§1",period_evidence="test fixture",review="expert_approved",approved_by="synthetic fixture",
                  text="В учебном тарифе Z доступно три экспорта в день.",**{"from":"2026-11-01","to":"2026-11-30"})
        catalog={"contexts":{conv["id"]:scope},"facts":[fact]}
        initial=provider.evaluate(case)
        updated=ProductVerifier(provider,catalog).refine(case,initial)
        Contracts().validate_response(case,updated)
        flag=next(w for w in updated["warnings"] if w.startswith("product-facts "))
        mismatch=ProductVerifier(provider,{**catalog,"facts":[{**fact,"product":"other"}]}).refine(case,initial)
        assert mismatch==initial
        probes.append({"case":disposition,"notice":flag,"value":next(r["value"] for r in updated["criteria"]
                        if r["criterion_id"]=="consultation"),"other_product_excluded":True})
    receipt=dict(http=200,official_rows=len(result["criteria"]),changed_rows=len(other["criteria"]),
                 same_id_new_rubric_skipped_map=True,synthetic_knowledge=probes)
    with args.out.open("x",encoding="utf8") as out:
        json.dump(receipt,out,ensure_ascii=False,indent=2)


if __name__=="__main__":
    main()
