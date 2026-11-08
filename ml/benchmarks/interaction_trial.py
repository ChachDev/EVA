"""Paired evaluation on fixed conversation IDs; references are consumed only by reporting."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import median
from time import perf_counter
from types import SimpleNamespace

from benchmarks.clean_dev import PRIVATE, read_json, template_candidates
from benchmarks.ledger_trial import public_holdout
from benchmarks.share_report import label, same, scores
from benchmarks.share_trial import prepare_inputs, read_rows
from eva_ml.contracts import Contracts
from eva_ml.disclosure_review import DisclosureReviewer
from eva_ml.engine import DEFAULT_WEIGHTS, LocalEngine, inference_usage
from eva_ml.interaction_guard import GuardedInteractionReviewer
from eva_ml.interaction_review import InteractionReviewer
from eva_ml.issai import digest


def inputs(group):
    if group == "corpus65":
        bundle = PRIVATE / "clean-dev-de20aae9"
        requests = read_rows(bundle / "eva-requests.jsonl")
        records = read_json(bundle / "cqa_dataset_clean.json")["records"]
        if len(requests) != 65 or [r["conversation"]["id"] for r in requests] != [r["conversation_id"] for r in records]:
            raise ValueError("Expected the complete 65-conversation DEV export")
        for req, record in zip(requests, records, strict=True):
            c = req["conversation"]
            if (c["language"].lower() != record["language"]
                    or c["channel"] != {"chat": "agent_chat", "voice": "voice_call"}[record["channel"]]
                    or [(m["speaker"], m["text"]) for m in c["messages"]] != [(t["role"], t["text"]) for t in record["turns"]]):
                raise ValueError(f"Regenerate the EVA input for {c['id']}")
            yield req, None
    elif group in {"develop", "reserved", "pilot"}:
        sealed = {r["cid"]: r for r in read_rows(PRIVATE / "clean-dev-de20aae9/live-historical-27.jsonl")}
        for s in prepare_inputs():
            if s["cohort"] == group:
                saved = sealed[s["cid"]]
                if saved["request_sha256"] != digest(s["request"]) or scores(saved["response"]) != scores(s["primary"]):
                    raise ValueError("Frozen primary differs")
                yield s["request"], saved["response"]
    else:
        source = public_holdout() if group == "public8" else public_holdout("public-gold16.json", 16)
        for req, *_ in source:
            if req["conversation"]["channel"] == "agent_chat":
                yield json.loads(json.dumps(req, sort_keys=True)), None


def predict(group, port, output, first_pass=None):
    engine = LocalEngine(port, Path(DEFAULT_WEIGHTS).stem)
    if engine.model not in [r["id"] for r in engine.request("/v1/models")["data"]]:
        raise ValueError("Unexpected worker weights")
    primary, reviewer = DisclosureReviewer(engine), InteractionReviewer(engine)
    contracts = Contracts()
    samples = list(inputs(group))
    frozen = {}
    if first_pass:
        saved = read_rows(first_pass)
        frozen = {r["cid"]: r for r in saved}
        if len(frozen) != len(saved) or set(frozen) != {req["conversation"]["id"] for req, _ in samples}:
            raise ValueError("First-pass file has different inputs")
    if not output.resolve().is_relative_to(PRIVATE.resolve()):
        raise ValueError("Keep predictions under ml/data")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf8") as out:
        for req, base in samples:
            contracts.validate_request(req)
            if frozen:
                source = frozen[req["conversation"]["id"]]
                if (source["request"] != digest(req) or source["base"]["model"] != engine.model or
                        source["base"]["provider"] != "llama.cpp" or base is not None and source["base"] != base):
                    raise ValueError("First-pass provenance mismatch")
                base = source["base"]
            if base is None:
                base = primary.evaluate(req)
            contracts.validate_response(req, base)
            start = perf_counter()
            with inference_usage() as usage:
                result, state = reviewer.review_interaction(req, base)
            contracts.validate_response(req, result)
            other = lambda res: [r for r in res["criteria"] if r["criterion_id"] not in {"needs", "dialogue"}]
            if other(base) != other(result):
                raise ValueError("Unrelated criterion changed")
            c = req["conversation"]
            row = {"cid": c["id"], "group": group, "language": c["language"], "request": digest(req),
                   "content": digest([(m["speaker"], m["text"].casefold().strip()) for m in c["messages"]]),
                   "reviewer": reviewer.interaction_hash, "state": state, "seconds": round(perf_counter()-start, 3),
                   "usage": usage, "base": base, "result": result}
            out.write(json.dumps(row, ensure_ascii=False) + "\n"); out.flush()
            print(group, row["cid"], state, row["seconds"], flush=True)


def guarded(source, output, apply="both"):
    if not output.resolve().is_relative_to(PRIVATE.resolve()):
        raise ValueError("Keep predictions under ml/data")
    rows = read_rows(source)
    model = GuardedInteractionReviewer(SimpleNamespace(model=Path(DEFAULT_WEIGHTS).stem), apply=apply)
    contracts = Contracts()
    requests = {req["conversation"]["id"]: req for req, _ in inputs(rows[0]["group"])}
    if len(rows) != len(requests) or set(requests) != {r["cid"] for r in rows}:
        raise ValueError("Incomplete parent file")
    with output.open("x", encoding="utf8") as out:
        for r in rows:
            req = requests[r["cid"]]
            if r["request"] != digest(req) or r["reviewer"] != model.interaction_hash:
                raise ValueError("Parent reviewer or input changed")
            start = perf_counter()
            result = model.gate(req, r["base"], r["result"]) if r["state"] == "checked" else r["result"]
            contracts.validate_response(req, result)
            out.write(json.dumps({**r, "result": result, "reviewer": model.guarded_hash,
                "seconds": r["seconds"] + perf_counter() - start}, ensure_ascii=False) + "\n")


def references():
    book = read_json(PRIVATE / "blind15-comparison-20261108/reference.json")
    gold = defaultdict(dict)
    for r in book:
        gold[r["chat"]][r["criterion"]] = label(r["gold"])
    for r in read_rows(PRIVATE / "baseline-plus-audit-v1/pilot.jsonl"):
        gold[r["cid"]] = r["gold"]
    for suite in (public_holdout(), public_holdout("public-gold16.json", 16)):
        for req, values, *_ in suite:
            cid = req["conversation"]["id"]
            if cid in gold:
                raise ValueError("Repeated reference id")
            gold[cid] = values
    return gold


def report(rows):
    if any(r["group"] == "corpus65" for r in rows):
        raise ValueError("The 65-conversation corpus has no complete reference; use the labelled cohorts for accuracy")
    gold = references()
    groups, changes, seen = defaultdict(lambda: defaultdict(Counter)), [], set()
    clean = read_json(PRIVATE / "clean-dev-de20aae9/cqa_dataset_clean.json")["records"]
    book_ids = {s["cid"] for s in prepare_inputs() if s["cohort"] != "pilot"}
    related = {cid for group in template_candidates(clean) if book_ids.intersection(group) for cid in group}
    for group in {r["group"] for r in rows}:
        expected = {req["conversation"]["id"]: digest(req) for req, _ in inputs(group)}
        subset = [r for r in rows if r["group"] == group]
        if len(subset) != len(expected) or {r["cid"]:r["request"] for r in subset} != expected:
            raise ValueError("Incomplete cohort or changed input")
    if len({r["reviewer"] for r in rows}) != 1:
        raise ValueError("Mixed reviewer revisions")
    for r in rows:
        if r["content"] in seen:
            raise ValueError("Repeated transcript in comparison")
        seen.add(r["content"])
        cid, group = r["cid"], r["group"]
        buckets = [group]
        if group in {"develop", "reserved"}: buckets.append("book")
        if group in {"public8", "public14"}: buckets.append("external")
        if group == "pilot" and cid not in related: buckets.append("pilot_different_timeline")
        base, after = scores(r["base"]), scores(r["result"])
        for key, y in gold[cid].items():
            b, a = same(base[key], y), same(after[key], y)
            for bucket in buckets:
                for column in (key, "all", f"{r['language']}/all"):
                    groups[bucket][column].update(n=1, base=int(b), after=int(a), fixed=int(a and not b),
                        worse=int(b and not a), covered_before=int(base[key] != "review"), covered_after=int(after[key] != "review"),
                        na_error_before=int((base[key] is None) != (y is None)), na_error_after=int((after[key] is None) != (y is None)))
            if not same(base[key], after[key]):
                changes.append({"group": group, "cid": cid, "key": key, "base": base[key], "after": after[key], "reference": y})
    return {"groups": groups, "changes": changes, "seconds_median": median(r["seconds"] for r in rows),
            "states": Counter(r["state"] for r in rows), "calls": sum(r["usage"]["calls"] for r in rows)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=["develop", "reserved", "pilot", "public8", "public14", "corpus65"])
    parser.add_argument("--port", type=int, default=59174)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--report", nargs="+", type=Path)
    parser.add_argument("--first-pass", type=Path, help="Reuse only verified first-pass responses from a completed cohort")
    parser.add_argument("--guard", type=Path, help="Apply B to a validated A file without another model call")
    parser.add_argument("--apply", choices=["both", "needs", "dialogue"], default="both",
                        help="Select which verified corrections to apply with --guard")
    args = parser.parse_args()
    if args.report:
        summary = report([r for path in args.report for r in read_rows(path)])
        with args.out.open("x", encoding="utf8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
    elif args.guard:
        guarded(args.guard, args.out, args.apply)
    elif args.group:
        predict(args.group, args.port, args.out, args.first_pass)
    else:
        parser.error("Choose --group or --report")
