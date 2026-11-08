"""Bind earlier proxy labels to identical EVA inputs and prepare a separate blind annotation pool."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from time import perf_counter
from urllib import request as http

from benchmarks.clean_dev import PRIVATE, read_json, template_candidates
from benchmarks.human_ballots import load, write
from benchmarks.share_report import label, same, scores
from benchmarks.share_trial import prepare_inputs
from eva_ml.contracts import Contracts
from eva_ml.issai import digest


GROSS_SIGNALS = {
    "operator_fraud_attempt", "operator_unauthorized_paid_advice",
    "operator_rude_language", "account_info_disclosed_without_id_check",
}


def blank_source(request):
    conv = request["conversation"]
    checklist = request["checklist"]
    annotation = []
    for c in checklist["criteria"]:
        allowed = list(c["scale"]) if c["type"] == "scale" else [True, False]
        if c["allow_na"]:
            allowed.append(None)
        annotation.append({"criterion_id": c["id"], "rule": c["instruction"], "na_condition": c["na_condition"],
                           "allowed": allowed, "human_status": "unlabeled", "human_value": None,
                           "evidence_turns": [], "rationale": None})
    return {"conversation_id": conv["id"], "language": conv["language"], "channel": conv["channel"],
            "group": None, "checklist_version": checklist["version"],
            "transcript": [{"turn": idx, "speaker": m["speaker"], "timestamp": m["timestamp"], "text": m["text"]}
                           for idx, m in enumerate(conv["messages"], 1)], "annotation": annotation}


def prepare(requests_file, bundle, live_file=None):
    requests = load(requests_file)
    by_id = {r["conversation"]["id"]: r for r in requests}
    clean = {r["conversation_id"]: r for r in read_json(bundle / "cqa_dataset_clean.json")["records"]}
    if len(requests) != len(by_id) or set(by_id) != set(clean):
        raise ValueError("Expected one request per cleaned conversation")
    references = read_json(PRIVATE / "blind15-comparison-20261108/reference.json")
    book = {(r["chat"], r["criterion"]): label(r["gold"]) for r in references}
    pilot = {r["cid"]: r["gold"] for r in load(PRIVATE / "baseline-plus-audit-v1/pilot.jsonl")}
    history = prepare_inputs()
    live = {}
    if live_file:
        live_rows = load(live_file)
        live = {r["cid"]: r for r in live_rows}
        if len(live) != len(live_rows) or set(live) != {r["cid"] for r in history}:
            raise ValueError("Expected exactly one live result per historical chat")
    validator = Contracts()
    stats, errors = defaultdict(Counter), Counter()
    silver, seen = [], set()
    for sample in history:
        cid = sample["cid"]
        if cid in seen or sample["request"] != by_id[cid]:
            raise ValueError(f"Historical scorer input mismatch at {cid}")
        seen.add(cid)
        cohort = sample["cohort"]
        group = "pilot" if cohort == "pilot" else "book"
        predicted = scores(sample["primary"])
        reference = pilot[cid] if cohort == "pilot" else {k: book[cid, k] for k in predicted}
        stats[group].update(conversations=1, decisions=len(reference),
                            baseline_matches=sum(same(v, reference[k]) for k, v in predicted.items()))
        if live:
            entry = live[cid]
            if entry["request_sha256"] != digest(by_id[cid]):
                raise ValueError(f"Live input differs at {cid}")
            validator.validate_response(by_id[cid], entry["response"])
            actual = scores(entry["response"])
            stats[group]["live_matches"] += sum(same(v, reference[k]) for k, v in actual.items())
            stats[group]["changed_from_baseline"] += sum(not same(v, predicted[k]) for k, v in actual.items())
        r = clean[cid]
        stats[group]["resolved_guess_equals_consultation_max"] += r["resolved_guess"] == (reference["consultation"] == 20)
        if r["resolved_guess"] != (predicted["consultation"] == 20):
            stats[group]["consultation_disagreements"] += 1
            stats[group]["baseline_errors_among_disagreements"] += not same(predicted["consultation"], reference["consultation"])
        if r["ended_abruptly"]:
            stats[group][f"abrupt_with_closing_{reference['closing']}"] += 1
        signal = bool(GROSS_SIGNALS.intersection(r["quality_flags"]))
        stats[group]["gross_" + {(True, True): "TP", (True, False): "FP", (False, True): "FN",
                                 (False, False): "TN"}[signal, reference["gross_errors"]]] += 1
        if group == "book":
            errors.update(k for k, v in predicted.items() if not same(v, reference[k]))
        if cohort == "develop":
            silver.append({"conversation_id": cid, "request_sha256": digest(by_id[cid]),
                           "label_source": "historical_ai_ballots_arbitrated", "cohort": cohort,
                           "checklist_version": by_id[cid]["checklist"]["version"], "decisions": reference})
    blind = [blank_source(r) for r in requests if r["conversation"]["id"] not in seen]
    shared = {cid for group in template_candidates(clean.values()) if seen.intersection(group)
              for cid in group if cid not in seen}
    return {"cohorts": stats, "historical_book_errors": errors, "silver_dev": len(silver),
            "pending_expert_conversations": len(blind),
            "pending_sharing_a_timeline_with_history": sorted(shared),
            "pending_expert_decisions": sum(len(r["annotation"]) for r in blind)}, silver, blind


def replay(requests_file, output, port):
    """Send the cleaned inputs to the live evaluator; score the saved answers separately."""
    if not output.resolve().is_relative_to(PRIVATE.resolve()) or not 1 <= port <= 65535:
        raise ValueError("Use a loopback port and a new output under ml/data")
    rows = {r["conversation"]["id"]: r for r in load(requests_file)}
    cohorts = prepare_inputs()
    validator = Contracts()
    client = http.build_opener(http.ProxyHandler({}))
    base = f"http://127.0.0.1:{port}"
    with client.open(base + "/health", timeout=5) as reply:
        health = json.load(reply)
    if health["provider"] != "llama.cpp":
        raise ValueError("A real local evaluator is required")
    with output.open("x", encoding="utf8") as f:
        for sample in cohorts:
            cid = sample["cid"]
            req = rows[cid]
            if req != sample["request"]:
                raise ValueError(f"Historical input differs at {cid}")
            start = perf_counter()
            payload = json.dumps(req, ensure_ascii=False, sort_keys=True).encode("utf8")
            post = http.Request(base + "/v1/evaluate", data=payload, headers={"Content-Type": "application/json"})
            with client.open(post, timeout=300) as reply:
                result = json.load(reply)
            validator.validate_response(req, result)
            if (result["provider"], result["model"]) != (health["provider"], health["model"]):
                raise ValueError("Serving configuration changed during replay")
            row = {"cid": cid, "cohort": sample["cohort"], "request_sha256": digest(req),
                   "seconds": round(perf_counter() - start, 2), "response": result}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            print(cid, row["seconds"], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="New directory under ml/data for the two distinct label sets")
    replay_options = parser.add_mutually_exclusive_group()
    replay_options.add_argument("--live-output", type=Path, help="Replay the historical chats on local EVA into a new JSONL")
    replay_options.add_argument("--read-live", type=Path, help="Compare a completed replay without calling the model")
    parser.add_argument("--port", type=int, default=18601)
    args = parser.parse_args()
    report, silver, blind = prepare(args.requests, args.bundle, args.read_live)
    if args.out:
        if not args.out.resolve().is_relative_to(PRIVATE.resolve()):
            parser.error("Private labels must stay under ml/data")
        args.out.mkdir(parents=True, exist_ok=False)
        write(args.out / "silver-development.jsonl", silver)
        write(args.out / "blind-source.jsonl", blind)
    print(json.dumps(report, indent=2))
    if args.live_output:
        replay(args.requests, args.live_output, args.port)
        report, _, _ = prepare(args.requests, args.bundle, args.live_output)
        print(json.dumps(report["cohorts"], indent=2))
