"""Evaluate the team's previously reviewed, pinned resolution-v2 on public synthetic chats."""
import argparse
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from time import perf_counter

from benchmarks.audit_run import ROOT
from benchmarks.ledger_trial import public_holdout
from eva_ml.contracts import Contracts
from eva_ml.engine import LocalEngine
from eva_ml.share_resolution import ARCHIVE_SHA, command, extract_candidate


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port",type=int,required=True)
    p.add_argument("--source",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args()
    if not args.out.resolve().is_relative_to(ROOT/"data"):
        p.error("Use private ml/data")
    rows=[json.loads(x) for x in args.source.read_text(encoding="utf8").splitlines()]
    suite=rows[0]["cohort"]
    if suite not in {"gold16","public"}:
        p.error("Expected a public suite")
    inputs=public_holdout("public-gold16.json",16) if suite=="gold16" else public_holdout()
    worker=LocalEngine(args.port,rows[0]["model"])
    assert rows[0]["model"] in {x["id"] for x in worker.request("/v1/models")["data"]}
    contracts=Contracts()
    with TemporaryDirectory(dir=args.out.parent) as tmp, args.out.open("x",encoding="utf8") as out:
        target=Path(tmp)
        extract_candidate(ROOT/"data/share-comparison/share.zip",target)
        with subprocess.Popen(command(target,worker),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                text=True,encoding="ascii",creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)) as proc:
            try:
                for row,(req,gold,*_) in zip(rows,inputs,strict=True):
                    assert row["id"]==req["conversation"]["id"] and row["gold"]==gold
                    req=json.loads(json.dumps(req,sort_keys=True))
                    payload={"cid":row["id"],"request":req,"primary":row["primary"]}
                    start=perf_counter()
                    proc.stdin.write(json.dumps(payload,ensure_ascii=True)+"\n")
                    proc.stdin.flush()
                    reply=json.loads(proc.stdout.readline())
                    assert reply["cid"]==row["id"]
                    contracts.validate_response(req,reply["response"])
                    row["variants"]={"resolution":{"response":reply["response"],"sha":ARCHIVE_SHA,
                        "details":{"state":"evaluated","calls":reply["calls"]},"seconds":perf_counter()-start}}
                    out.write(json.dumps(row,ensure_ascii=False)+"\n")
                    out.flush()
            finally:
                proc.stdin.close()


if __name__=="__main__":
    main()
