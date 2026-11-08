"""Paired variants, synthetic diagnostics and previously used pilot only."""
import argparse
import json
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

from benchmarks.audit_run import CHECK, ROOT, inputs
from benchmarks.ledger_cases import synthetic
from eva_ml.consultation_v11 import select_provider
from eva_ml.contracts import Contracts
from eva_ml.engine import LocalEngine
from eva_ml.issai import digest
from eva_ml.request_ledger import RequestLedgerProvider, TEACHING
from eva_ml.ledger_spans import SpanLedgerProvider


def public_holdout(filename="public-holdout.json", size=10):
    source = json.loads((ROOT/"data/ledger-v1"/filename).read_text(encoding="utf8"))
    assert source["commit"] == "39f9cc1f2c39ec97d864fa99c7505392c2d29b28" and len(source["files"]) == size
    keys = [c["id"] for c in CHECK["criteria"]]
    for entry in source["files"]:
        item = entry["data"]
        assert item["synthetic"] is True
        dialog = item["dialog"]
        chat = dialog["channel"] == "agent chat"
        turns = []
        for i, turn in enumerate(dialog["transcript"],1):
            seconds = None
            if chat:
                minutes, sec = map(int, turn["timestamp"].split(":"))
                seconds = 60*minutes + sec
            turns.append({**turn, "id":f"m{i:04d}", "offset_seconds": seconds})
        conv = {"id":dialog["conversation_id"], "channel":"agent_chat" if chat else "voice_call",
                "language":dialog["language"], "messages":turns, "audio_url":None}
        gold = {k:{"YES":True,"NO":False,"NA":None}.get(str(v),v)
                for k,v in zip(keys,(item["expected"][f"{i:02}"] for i in range(1,9)),strict=True)}
        for k in ("consultation", "dialogue"):
            if gold[k] is not None:
                gold[k] = int(gold[k])
        yield {"schema_version":"1.0.0","checklist":CHECK,"conversation":conv}, gold, None, None, None


def pilot():
    reference = ROOT / "data/consultation-v1.1/pilot-96.csv"
    sources = inputs(SimpleNamespace(reference=reference, dev=ROOT/".tmp/dev-ml-repair-2111/dev.json", pairs=False, voices=False))
    saved = {r["cid"]: r for r in map(json.loads, (ROOT/"data/baseline-plus-audit-v1/pilot.jsonl").read_text(encoding="utf8").splitlines())}
    for conv, gold in sources:
        record = saved[conv["id"]]
        audit = record["audit"]
        assert digest(conv) == audit["versions"]["transcript"] and digest(CHECK) == audit["versions"]["checklist"]
        yield {"schema_version": "1.0.0", "conversation": conv, "checklist": CHECK}, gold, None, audit["verification"]["baseline"], record["seconds"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--model", default="Qwen3.5-9B-Kazakh.Q5_K_M")
    parser.add_argument("--cohort", choices=["pilot", "synthetic", "public", "gold16"], required=True)
    parser.add_argument("--base-only", action="store_true", help="Run the unmodified full evaluator for an additional model.")
    parser.add_argument("--spans", action="store_true", help="Use the separately versioned citation experiment.")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.out.resolve().is_relative_to(ROOT/"data"):
        parser.error("Store raw results in ml/data")
    engine = LocalEngine(args.port, args.model)
    assert args.model in {entry["id"] for entry in engine.request("/v1/models")["data"]}
    primary = select_provider(engine, "consultation-v1.1")
    variants = {"ledger": RequestLedgerProvider(primary), "examples": RequestLedgerProvider(primary, TEACHING)}
    if args.spans:
        variants = {"spans":SpanLedgerProvider(primary), "spans_examples":SpanLedgerProvider(primary, TEACHING)}
    contracts = Contracts()
    if args.base_only:
        variants = {}
    if args.cohort == "gold16":
        cohort = public_holdout("public-gold16.json",16)
    elif args.cohort == "public":
        cohort = public_holdout()
    elif args.cohort == "pilot":
        cohort = pilot()
    else:
        cohort = ((*r, None, None) for r in synthetic())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf8") as output:
        for i, (req, gold, family, saved, recorded) in enumerate(cohort):
            req = json.loads(json.dumps(req, sort_keys=True))
            contracts.validate_request(req)
            if saved and saved["model"] != args.model and args.base_only:
                saved, recorded = None, None
            start = perf_counter()
            base = saved or primary.evaluate(req)
            if saved is not None and saved["model"] != args.model:
                raise ValueError("Cached pilot requires the original model")
            contracts.validate_response(req, base)
            cid = req["conversation"]["id"]
            row = {"id": cid, "language": req["conversation"]["language"], "family": family, "gold": gold,
                   "checklist": digest(CHECK), "model": args.model, "cohort": args.cohort,
                   "base_seconds": None if saved else perf_counter()-start, "historical_seconds": recorded,
                   "primary": base, "variants": {}}
            order = list(variants) if i % 2 == 0 else list(reversed(variants))
            for name in order:
                start = perf_counter()
                response, details = variants[name].refine(req, base)
                elapsed = perf_counter()-start
                contracts.validate_response(req, response)
                assert [r for r in response["criteria"] if r["criterion_id"] != "consultation"] == [
                    r for r in base["criteria"] if r["criterion_id"] != "consultation"]
                row["variants"][name] = {"response": response, "details": details, "seconds": elapsed, "sha": variants[name].version}
            output.write(json.dumps(row, ensure_ascii=False)+"\n")
            output.flush()
            print(args.cohort, i+1, flush=True)


if __name__ == "__main__":
    main()
