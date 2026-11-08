"""Make independent local ballots or score agreement from completed human ballots."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "data"


def load(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def write(path, rows):
    if not path.resolve().is_relative_to(PRIVATE.resolve()):
        raise ValueError("Private ballots must stay in ml/data")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False)+"\n")


def materialize(source):
    expected = {}
    for conv in source:
        turns = {f"m{t['turn']:04d}":(t["turn"],t.get("speaker")) for t in conv["transcript"]}
        for criterion in conv["annotation"]:
            key = (conv["conversation_id"], criterion["criterion_id"])
            if key in expected or criterion["human_status"] != "unlabeled":
                raise ValueError("Expected unique unlabelled source")
            expected[key] = (criterion, turns)
    return expected


def blank(source, out):
    expected = materialize(source)
    for name in ("A", "B"):
        write(out/f"annotator-{name}.jsonl", [
            dict(chat=chat, criterion=key, allowed=entry["allowed"], rule=entry["rule"],
                 na_condition=entry["na_condition"], status="unlabeled", value=None,
                 evidence_ids=[], reason="", request_facts=[] if key=="consultation" else None)
            for (chat, key), (entry, _) in expected.items()])
    write(out/"transcripts.jsonl", [
        {"chat": conv["conversation_id"], "language": conv["language"], "family": conv["group"],
         "turns": [{"id": f"m{r['turn']:04d}", "speaker": r["speaker"],
                    "timestamp": r["timestamp"], "text": r["text"]} for r in conv["transcript"]]}
        for conv in source])


def package(source, out):
    if not out.resolve().is_relative_to(PRIVATE.resolve()):
        raise ValueError("Use the private ballots directory")
    expected = materialize(source)
    instructions = ("Откройте editor.html. Выберите свой бланк annotator и transcripts.jsonl. "
                    "Для каждого критерия отметьте оценку, причину и ID реплик. "
                    "Кнопка «Скачать мой бланк» сохраняет работу. Продолжить можно из сохранённого файла. "
                    "Верните заполненный бланк координатору разметки.")
    transcript = out/"transcripts.jsonl"
    if {r["chat"] for r in load(transcript)} != {chat for chat,_ in expected}:
        raise ValueError("Transcript set differs from the ballots")
    for name in ("A","B"):
        ballot=out/f"annotator-{name}.jsonl"
        rows=load(ballot)
        if ({(r["chat"],r["criterion"]) for r in rows}!=expected.keys() or len(rows)!=len(expected)
                or any(r["status"]!="unlabeled" or r["value"] is not None or r["reason"] or
                       r["request_facts"] or r["evidence_ids"] for r in rows)):
            raise ValueError("Delivery requires a blank ballot")
        with ZipFile(out/f"annotator-{name}.zip","x",compression=ZIP_DEFLATED) as z:
            z.write(ROOT/"audit/annotate.html","editor.html")
            z.write(ballot,ballot.name)
            z.write(transcript,transcript.name)
            z.writestr("README.txt",instructions)


def validated(rows, expected, partial=False):
    indexed = {}
    for r in rows:
        key = (r["chat"], r["criterion"])
        if key in indexed or key not in expected:
            raise ValueError(f"Unknown or duplicate {key}")
        entry, turns = expected[key]
        v = r["value"]
        if (r["status"] != "scored" or not any(type(candidate) is type(v) and candidate == v for candidate in entry["allowed"])
                or not r.get("reason","").strip() or not r["evidence_ids"]
                or not set(r["evidence_ids"]) <= turns.keys()):
            raise ValueError(f"Missing decision, invalid value or evidence at {key}")
        facts = r.get("request_facts")
        if facts:
            if key[1] != "consultation" or not isinstance(facts,list):
                raise ValueError(f"Unexpected request facts at {key}")
            for fact in facts:
                request_id, reply_id = fact["client_id"], fact.get("reply_id")
                state = fact["state"]
                if (request_id not in turns or turns[request_id][1]!="client" or not fact["goal"].strip()
                        or fact["importance"] not in {"essential","secondary"}
                        or state not in {"closed","partial","generic","open","withdrawn","unknown"}
                        or state in {"closed","partial","generic","withdrawn"} and not reply_id
                        or state=="open" and reply_id):
                    raise ValueError(f"Invalid request at {key}")
                if reply_id:
                    role = "client" if state=="withdrawn" else "operator"
                    if reply_id not in turns or turns[reply_id][1]!=role or turns[reply_id][0]<=turns[request_id][0]:
                        raise ValueError(f"Invalid answer source at {key}")
        indexed[key] = v
    if not partial and indexed.keys() != expected.keys():
        raise ValueError("Ballot has missing rows")
    return indexed


def reconcile(source, first, second, third):
    expected = materialize(source)
    a, b = validated(first, expected), validated(second, expected)
    adjudicated = validated(third, expected, partial=True)
    agreed, disagreements, gold = Counter(), [], []
    for key in expected:
        if a[key] == b[key]:
            agreed[key[1]] += 1
            value, origin = a[key], "agreement"
        else:
            disagreements.append({"chat":key[0], "criterion":key[1], "a":a[key], "b":b[key],
                                  "adjudicated":key in adjudicated})
            if key not in adjudicated:
                continue
            value, origin = adjudicated[key], "third"
        gold.append({"chat":key[0], "criterion":key[1], "value":value, "origin":origin})
    return dict(total=len(expected), agreed=dict(agreed), disagreements=disagreements,
                excluded=len(expected)-len(gold), gold=gold)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["prepare", "package", "reconcile"])
    p.add_argument("--source", type=Path, default=PRIVATE/"share-comparison/blind-next.jsonl")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--a", type=Path)
    p.add_argument("--b", type=Path)
    p.add_argument("--third", type=Path)
    args=p.parse_args()
    source = load(args.source)
    if args.action == "prepare":
        blank(source, args.out)
    elif args.action == "package":
        package(source, args.out)
    else:
        if not args.a or not args.b:
            p.error("Both independent ballots are required")
        report = reconcile(source,load(args.a),load(args.b),load(args.third) if args.third else [])
        report["sha256"] = {name:sha256(path.read_bytes()).hexdigest() for name,path in
                            (("source",args.source),("A",args.a),("B",args.b),("third",args.third)) if path}
        write(args.out,[report])


if __name__ == "__main__":
    main()
