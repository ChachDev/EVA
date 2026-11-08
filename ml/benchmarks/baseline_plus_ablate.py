"""Replay frozen synthetic inputs against individual verification switches."""
import argparse
import json
from pathlib import Path

from benchmarks.audit_compare import read
from benchmarks.audit_pairs import cases
from benchmarks.audit_run import CHECK, Meter
from eva_ml.audit import COMPONENTS
from eva_ml.audit_events import select_auditor
from eva_ml.consultation_v11 import select_provider
from eva_ml.contracts import Contracts
from eva_ml.issai import digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    primary = {r["cid"]: r for r in read(args.pairs)}
    engine = Meter(args.port, "Qwen3.5-9B-Kazakh.Q5_K_M")
    provider = select_provider(engine, "consultation-v1.1")
    contracts = Contracts()
    selected = [c for i, c in enumerate(cases()) if i in {0, 1, 8, 9, 16, 17}]
    results = {}
    for label, enabled in [("full", COMPONENTS), ("without_score", COMPONENTS - {"score"}),
                          ("without_sentinel", COMPONENTS - {"sentinel"}), ("without_policy", COMPONENTS - {"policy"}),
                          ("without_trace", COMPONENTS - {"trace"}), ("disabled", set())]:
        auditor = select_auditor(provider, enabled, "baseline-plus", cache_size=0)
        correct = anchors = calls = automatic = 0
        for conversation, key, expected in selected:
            record = primary[conversation["id"]]
            if record["audit"]["versions"]["code"] != auditor.revision:
                raise ValueError("The pair run used a different evaluator")
            if record["audit"]["versions"]["transcript"] != digest(conversation):
                raise ValueError("Different synthetic transcript")
            req = {"schema_version":"1.0.0", "conversation":conversation, "checklist":CHECK}
            engine.calls = engine.tokens = 0
            engine.token_known = True
            base = record["audit"]["verification"]["baseline"]
            contracts.validate_response(req, base)
            bundle = auditor.evaluate(req, saved_baseline=base)
            response = bundle["response"]
            contracts.validate_response(req, response)
            if label == "full" and response != record["response"]:
                raise ValueError("Full replay did not reproduce the frozen decision and evidence")
            if not {"trace","policy"} <= enabled and base != response:
                raise ValueError("A disabled prerequisite changed the baseline")
            row = next(c for c in response["criteria"] if c["criterion_id"] == key)
            resolved = row["status"] == "scored"
            match = resolved and row["value"] is expected
            required = {m["id"] for m in conversation["messages"]
                        if m["speaker"] == "operator" or (key == "identity" and m["speaker"] == "system")}
            ids = {mid for group in row["evidence"] for mid in group["message_ids"]}
            correct += match
            automatic += resolved
            anchors += match and required <= ids
            calls += engine.calls
        results[label] = {"n":len(selected), "correct":correct, "automatic":automatic,
                          "correct_with_required_evidence":anchors, "supplemental_calls":calls}
    args.out.write_text(json.dumps(results, indent=2)+"\n", encoding="utf8")


if __name__ == "__main__":
    main()
