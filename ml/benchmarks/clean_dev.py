"""Compare a cleaned Freedom DEV bundle with its original export and prepare local EVA inputs."""
import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from jsonschema import Draft7Validator

from eva_ml.contracts import Contracts


ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "data"
CHANNELS = {"chat": "agent chat", "voice": "voice call"}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def unique(records):
    result = {r["conversation_id"]: r for r in records}
    if len(result) != len(records):
        raise ValueError("Duplicate conversation_id")
    return result


def clock_time(channel, timestamp):
    if channel == "voice":
        return datetime.fromisoformat(timestamp)
    minutes, seconds = map(int, timestamp.split(":"))
    return minutes * 60 + seconds


def compare_record(clean, original):
    turns, old = clean["turns"], original["transcript"]
    changes = Counter()
    changes["channel"] += CHANNELS[clean["channel"]] != original["channel"]
    changes["language"] += clean["language"].upper() != original["language"]
    changes["audio_reference"] += clean.get("audio_url") != original.get("audio_url")
    changes["turn_count"] += len(turns) != len(old) or len(turns) != clean["turn_count"]
    changes["unpaired_turns"] += abs(len(turns) - len(old))
    for idx, (turn, previous) in enumerate(zip(turns, old)):
        changes["turn_index"] += turn["turn_idx"] != idx
        changes["text_at_position"] += turn["text"] != previous["text"]
        changes["speaker_at_position"] += turn["role"] != previous["speaker"]
        delta = clock_time(clean["channel"], previous["timestamp"]) - clock_time(clean["channel"], old[0]["timestamp"])
        seconds = delta.total_seconds() if hasattr(delta, "total_seconds") else delta
        changes["relative_time"] += not math.isclose(turn["t_sec"], seconds, abs_tol=.001, rel_tol=0)
        changes["time_order"] += idx > 0 and turn["t_sec"] < turns[idx - 1]["t_sec"]
    if turns:
        duration = turns[-1]["t_sec"] - turns[0]["t_sec"]
        changes["duration"] += clean.get("duration_sec") is None or not math.isclose(
            clean["duration_sec"], duration, abs_tol=.001, rel_tol=0)
    return +changes


def eva_request(record, original, checklist):
    """Call only on a matched pair. Never send the signed URL or heuristic answers to the scorer."""
    messages = []
    for turn, source in zip(record["turns"], original["transcript"], strict=True):
        timestamp = source["timestamp"]
        messages.append({
            "id": f"m{turn['turn_idx'] + 1:04d}", "speaker": turn["role"], "text": turn["text"],
            "timestamp": timestamp,
            "offset_seconds": clock_time("chat", timestamp) if record["channel"] == "chat" else None,
        })
    return {"schema_version": "1.0.0", "conversation": {
        "id": record["conversation_id"], "language": record["language"].upper(),
        "channel": CHANNELS[record["channel"]].replace(" ", "_"), "messages": messages, "audio_url": None,
    }, "checklist": checklist}


def compare_views(bundle, records):
    """CSV is a summary and JSONL is a turn projection. Compare against the same canonical records."""
    with (bundle / "cqa_conversations.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = unique(list(csv.DictReader(stream)))
    clean = unique(records)
    csv_differences = len(rows.keys() ^ clean.keys())
    for cid in rows.keys() & clean.keys():
        expected = {k: ";".join(v) if isinstance(v, list) else str(v) for k, v in clean[cid].items()
                    if k not in {"turns", "audio_url", "audio_url_is_signed_trigger"}}
        csv_differences += expected != rows[cid]
    with (bundle / "cqa_turns.jsonl").open(encoding="utf-8-sig") as stream:
        actual = [json.loads(line) for line in stream]
    expected_turns = [{**{key: r[key] for key in ("conversation_id", "channel", "language")}, **turn}
                      for r in records for turn in r["turns"]]
    jsonl_differences = abs(len(actual) - len(expected_turns))
    jsonl_differences += sum(a != b for a, b in zip(actual, expected_turns))
    return {"csv_differing_records": csv_differences, "jsonl_differing_turns": jsonl_differences}


def template_candidates(records):
    """An identical turn timeline suggests a shared template; content still needs human review."""
    groups = defaultdict(list)
    for r in records:
        if r["channel"] == "chat":
            key = tuple((t["role"], t["t_sec"]) for t in r["turns"])
            groups[key].append(r)
    return [[r["conversation_id"] for r in group] for group in groups.values()
            if len({r["language"] for r in group}) > 1]


def audit(bundle, raw):
    clean_file = bundle / "cqa_dataset_clean.json"
    dataset = read_json(clean_file)
    records = dataset["records"]
    clean = unique(records)
    original = unique(read_json(raw)["data"]["records"])
    schema = Draft7Validator(read_json(bundle / "schema.json"))
    checklist = read_json(ROOT.parent / "contracts/examples/checklist.json")
    contracts = Contracts()
    requests, differences = [], Counter()
    counts = Counter()
    for r in records:
        cid = r["conversation_id"]
        issue = next(schema.iter_errors(r), None)
        if issue:
            raise ValueError(f"Clean schema failed for {cid} at {list(issue.absolute_path)}")
        counts[f"{r['channel']}/{r['language']}"] += 1
        if cid in original:
            errors = compare_record(r, original[cid])
            differences.update(errors)
            if not errors:
                request = eva_request(r, original[cid], checklist)
                contracts.validate_request(request)
                requests.append(request)
    summary = {
        "raw_sha256": sha256(raw.read_bytes()).hexdigest(),
        "clean_sha256": sha256(clean_file.read_bytes()).hexdigest(),
        "original_conversations": len(original), "clean_conversations": len(clean),
        "original_turns": sum(len(r["transcript"]) for r in original.values()),
        "clean_turns": sum(len(r["turns"]) for r in records), "channel_language": counts,
        "only_original": sorted(original.keys() - clean.keys()), "only_clean": sorted(clean.keys() - original.keys()),
        "id_order_matches": list(original) == list(clean),
        "declared_total_matches": dataset["total_records"] == len(clean),
        "paired_differences": differences, **compare_views(bundle, records),
        "validated_requests": len(requests),
        "cross_language_timeline_candidates": template_candidates(records),
        "flags": Counter(flag for r in records for flag in r["quality_flags"]),
    }
    return summary, requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--requests", type=Path, help="New private JSONL under ml/data, matched corpus only")
    args = parser.parse_args()
    summary, requests = audit(args.bundle, args.raw)
    print(json.dumps(summary, indent=2))
    if (len(requests) != max(summary["original_conversations"], summary["clean_conversations"])
            or not summary["declared_total_matches"] or summary["csv_differing_records"] or summary["jsonl_differing_turns"]):
        raise SystemExit("Datasets differ; inspect the report before using them as interchangeable.")
    if args.requests:
        if not args.requests.resolve().is_relative_to(PRIVATE.resolve()):
            parser.error("Raw requests must stay under ml/data")
        args.requests.parent.mkdir(parents=True, exist_ok=True)
        with args.requests.open("x", encoding="utf8") as output:
            for req in requests:
                output.write(json.dumps(req, ensure_ascii=False, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
