"""Independently validate one EVA submission against the organizer's input envelope."""
import argparse
import json
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


WEIGHTS = {"contact":10,"discovery":15,"identification":20,"consultation":20,
           "empathy":15,"dialogue":15,"closure":5}
SCALES = {"consultation":(0,5,10,15,20), "dialogue":(0,4,7,10)}
ALLOW_NA = {"discovery","identification","consultation","closure"}
CRITERIA = set(WEIGHTS) | {"critical"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path):
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            require(key not in obj, f"Duplicate JSON key: {key}")
            obj[key] = value
        return obj
    return json.loads(path.read_text(encoding="utf8"), object_pairs_hook=unique, parse_float=Decimal)


def validate(doc, source, group):
    require(set(doc) == {"schema_version","team_name","model","results"}, "Unexpected root fields")
    require(doc["schema_version"] == "1.0" and doc["team_name"] == "EVA", "Wrong schema version or team")
    require(doc["model"] == {"mode":"local","name":"Qwen3.5-9B-Kazakh.Q5_K_M"}, "Unexpected evaluated model")
    count = 65 if group == "DEV" else 10
    require(source["isSuccess"] is True and source["data"]["total_records"] == count, "Incomplete source")
    records = source["data"]["records"]
    turns = {r["conversation_id"]:len(r["transcript"]) for r in records}
    expected = {"agent chat":60,"voice call":5} if group == "DEV" else {"agent chat":10}
    require(len(records) == len(turns) == count and Counter(r["channel"] for r in records) == expected,
            "Source IDs or channel counts differ from the appendix")
    ids = [r["conversation_id"] for r in doc["results"]]
    require(len(ids) == count and set(ids) == set(turns), "Missing, extra or duplicate result IDs")
    totals = Counter()
    for r in doc["results"]:
        cid = r["conversation_id"]
        fields = {"conversation_id","status","criteria","score"}
        if r["status"] == "error":
            require(set(r) == fields | {"error"} and r["criteria"] == [] and r["score"] is None
                    and isinstance(r["error"],str) and r["error"].strip(), f"{cid}: invalid error record")
            totals["error"] += 1
            continue
        require(r["status"] == "ok" and set(r) == fields, f"{cid}: invalid result fields")
        criteria = r["criteria"]
        require(len(criteria) == 8 and {c["criterion_id"] for c in criteria} == CRITERIA, f"{cid}: criteria")
        points, available = Decimal(0), 0
        for c in criteria:
            key, value, evidence = c["criterion_id"], c["value"], c["evidence_turns"]
            require(set(c) == {"criterion_id","value","rationale","evidence_turns"}
                    and isinstance(c["rationale"],str) and c["rationale"].strip(), f"{cid}/{key}: rationale")
            require(type(evidence) is list and evidence and all(type(n) is int and 1 <= n <= turns[cid]
                    for n in evidence) and len(set(evidence)) == len(evidence), f"{cid}/{key}: evidence")
            if value is None:
                require(key in ALLOW_NA, f"{cid}/{key}: N/A forbidden")
                continue
            if key in SCALES:
                require(type(value) is int and value in SCALES[key], f"{cid}/{key}: invalid scale value")
                ratio = Decimal(value) / max(SCALES[key])
            else:
                require(type(value) is bool, f"{cid}/{key}: expected boolean")
                ratio = Decimal(int(value))
            if key == "critical":
                require(r["score"]["critical_flag"] is value, f"{cid}: critical flag")
            else:
                points += ratio * WEIGHTS[key]
                available += WEIGHTS[key]
        require(set(r["score"]) == {"final_score","critical_flag"}, f"{cid}: score fields")
        score = r["score"]["final_score"]
        require(type(score) in {int, Decimal} and score == (points * 100 / available).quantize(
                Decimal(".01"), rounding=ROUND_HALF_UP), f"{cid}: weighted score or rounding")
        totals["ok"] += 1
    return dict(totals)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", choices=["DEV","PROD"])
    parser.add_argument("source", type=Path)
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    print(args.group, validate(load(args.result), load(args.source), args.group))
