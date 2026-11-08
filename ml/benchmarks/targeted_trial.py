"""Paired trial of a frozen rubric verifier. Per-conversation outputs stay in ignored data/."""
import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from benchmarks.audit_compare import read
from benchmarks.audit_run import CHECK, ROOT, Meter, inputs
from eva_ml.audit import COMPONENTS
from eva_ml.audit_events import select_auditor
from eva_ml.consultation_v11 import select_provider
from eva_ml.contracts import Contracts
from eva_ml.issai import digest


def family(cid):
    return cid.split("-")[-1]


def split(human, pilot):
    families = {family(r["cid"]) for r in pilot}
    held = {r["cid"] for r in human if family(r["cid"]) in families}
    for lang in ("RU","KZ","EN"):
        slots = 2 - sum(r["language"] == lang and r["cid"] in held for r in human)
        options = [r["cid"] for r in human if r["language"] == lang and r["cid"] not in held and
                   sum(family(other["cid"]) == family(r["cid"]) for other in human) == 1]
        ordered = sorted(options, key=lambda cid: hashlib.sha256(("eva-targeted-v1/"+cid).encode()).digest())
        if slots < 0 or slots > len(options):
            raise ValueError("Cannot form the preregistered groups")
        held.update(ordered[:slots])
    groups = {"develop":[r for r in human if r["cid"] not in held],
              "reserved":[r for r in human if r["cid"] in held], "pilot":pilot}
    if [len(v) for v in groups.values()] != [9,6,12]:
        raise ValueError("Unexpected evaluation groups")
    train_families = {family(r["cid"]) for r in groups["develop"]}
    if train_families & ({family(k) for k in held} | families):
        raise ValueError("A possible translated family crossed the split")
    return groups


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cohort", choices=["develop","reserved","pilot","pairs"], required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--archive", type=Path, default=ROOT/"data/baseline-plus-audit-v1")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--revision", help="Required frozen implementation hash outside the development run")
    args = p.parse_args()
    if not args.out.resolve().is_relative_to((ROOT/"data").resolve()):
        p.error("Raw results belong in ignored ml/data/")
    meter = Meter(args.port, "Qwen3.5-9B-Kazakh.Q5_K_M")
    base = select_provider(meter,"consultation-v1.1")
    auditor = select_auditor(base, COMPONENTS, "baseline-plus-targeted", cache_size=0)
    if args.revision != auditor.revision and (args.revision or args.cohort != "develop"):
        p.error("Evaluation requires the exact pinned verifier revision")
    groups = split(read(args.archive/"human.jsonl"), read(args.archive/"pilot.jsonl"))
    groups["pairs"] = read(args.archive/"pairs.jsonl")
    dev = ROOT/".tmp/dev-ml-repair-2111/dev.json"
    conversations = {}
    for ref, pairs in [(ROOT/"data/blind15-comparison-20261108/reference.json",False),
                       (ROOT/"data/consultation-v1.1/pilot-96.csv",False), (None,True)]:
        for conv, _ in inputs(SimpleNamespace(pairs=pairs, voices=False, reference=ref, dev=dev)):
            conversations[conv["id"]] = conv
    seen = set()
    for group in ("develop","reserved","pilot"):
        for record in groups[group]:
            conv = conversations[record["cid"]]
            fp = digest([(m["speaker"], " ".join(m["text"].casefold().split())) for m in conv["messages"]])
            if fp in seen:
                raise ValueError("Duplicate conversation across the evaluation groups")
            seen.add(fp)
    contracts = Contracts()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf8") as out:
        for record in groups[args.cohort]:
            req = {"schema_version":"1.0.0","conversation":conversations[record["cid"]],"checklist":CHECK}
            previous = record["audit"]
            if digest(req["conversation"]) != previous["versions"]["transcript"] or digest(CHECK) != previous["versions"]["checklist"]:
                raise ValueError("Input differs from the frozen source")
            primary = previous["verification"]["baseline"]
            if ((primary["provider"], primary["model"]) != (base.name, base.model)
                    or previous["versions"]["baseline_variant"] != base.experiment_hash
                    or not auditor.model_revision["declared"]
                    or previous["versions"]["model_revision"]["declared"] != auditor.model_revision["declared"]):
                raise ValueError("Primary provider, prompt or pinned weight revision has changed")
            contracts.validate_response(req, primary)
            meter.calls = meter.tokens = 0
            meter.token_known = True
            result = auditor.evaluate(req, saved_baseline=primary)
            contracts.validate_response(req, result["response"])
            if result["audit"]["verification"]["baseline"] != primary or result["audit"]["stage_usage"]["baseline"]["calls"]:
                raise ValueError("Verification altered or re-ran the primary answer")
            out.write(json.dumps({**record,"response":result["response"],"audit":result["audit"],
                "seconds":result["audit"]["seconds"],"calls":meter.calls,"tokens":meter.tokens if meter.token_known else None},
                ensure_ascii=False)+"\n")
            out.flush()
    print(f"{args.cohort}: {len(groups[args.cohort])} conversations verified, revision {auditor.revision}")


if __name__ == "__main__":
    main()
