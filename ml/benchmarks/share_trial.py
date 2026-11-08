"""Paired replay against the exact supplied EVA archive. Run from ml/; raw inputs stay local."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from time import perf_counter

from eva_ml.contracts import Contracts
from eva_ml.engine import DEFAULT_WEIGHTS, loaded_engine
from eva_ml.issai import digest
from eva_ml.share_resolution import command, extract_candidate

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "data"
WEIGHTS_SHA = "4c0beeaa5d24765d791fc6c0fee9b4a30b5e563d18dd6830c61b9993faf8e0b6"


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf8").splitlines()]


def prepare_inputs():
    check = json.loads((ROOT.parent/"contracts/examples/checklist.json").read_text(encoding="utf8"))
    source = json.loads((ROOT/".tmp/dev-ml-repair-2111/dev.json").read_text(encoding="utf-8-sig"))
    records = {r["conversation_id"]:r for r in source["data"]["records"]}
    saved = {r["cid"]:r for name in ("human","pilot") for r in read_rows(PRIVATE/f"baseline-plus-audit-v1/{name}.jsonl")}
    contracts = Contracts()
    seen = set()
    samples = []
    for cohort in ("develop", "reserved", "pilot"):
        for split in read_rows(PRIVATE/f"targeted-trial/{cohort}.jsonl"):
            cid = split["cid"]
            record = records[cid]
            if record["channel"] != "agent chat": raise ValueError("Expected a chat")
            messages = []
            for pos, message in enumerate(record["transcript"], 1):
                minutes, seconds = map(int, message["timestamp"].split(":"))
                messages.append(dict(message, id=f"m{pos:04d}", offset_seconds=60*minutes+seconds))
            conv = dict(id=cid, channel="agent_chat", language=record["language"], messages=messages,
                        audio_url=record["audio_url"])
            req = json.loads(json.dumps({"schema_version":"1.0.0","conversation":conv,"checklist":check},sort_keys=True))
            fingerprint = digest([(m["speaker"],m["text"].casefold().strip()) for m in messages])
            audit = saved[cid]["audit"]
            primary = audit["verification"]["baseline"]
            if (fingerprint in seen or digest(conv)!=audit["versions"]["transcript"] or
                    digest(check)!=audit["versions"]["checklist"] or
                    audit["versions"]["model_revision"]["declared"]!="sha256:"+WEIGHTS_SHA):
                raise ValueError("Duplicate or changed input/model")
            contracts.validate_request(req)
            contracts.validate_response(req, primary)
            samples.append({"cohort":cohort,"cid":cid,"request":req,"primary":primary})
            seen.add(fingerprint)
    if len(samples)!=27: raise ValueError("Expected the frozen 9 + 6 + 12 conversations")
    return samples




def run(variant, out, archive):
    if not out.resolve().is_relative_to(PRIVATE.resolve()): raise ValueError("Use private ml/data for raw predictions")
    samples = prepare_inputs()
    weights = Path(os.environ.get("EVA_ISSAI_GGUF", DEFAULT_WEIGHTS))
    with weights.open("rb") as f:
        if hashlib.file_digest(f,"sha256").hexdigest()!=WEIGHTS_SHA: raise ValueError("Weight checksum changed")
    contracts=Contracts()
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open("x",encoding="utf8") as output, tempfile.TemporaryDirectory(dir=out.parent) as temp:
        target=Path(temp)
        if variant=="zip": extract_candidate(archive,target)
        with loaded_engine() as engine:
            if variant=="zip":
                samples_file=target/"inputs.jsonl"
                samples_file.write_text("".join(json.dumps(s,ensure_ascii=True)+"\n" for s in samples),encoding="ascii")
                with samples_file.open("rb") as stream, subprocess.Popen(
                        command(target,engine),stdin=stream,stdout=subprocess.PIPE,text=True,encoding="ascii") as proc:
                    for sample,line in zip(samples,proc.stdout,strict=True):
                        item=json.loads(line)
                        if item["cid"]!=sample["cid"]:raise ValueError("Unexpected result")
                        contracts.validate_response(sample["request"],item["response"])
                        output.write(json.dumps(item,ensure_ascii=False)+"\n"); output.flush()
                    if proc.wait():raise RuntimeError("Archive evaluator failed")
            else:
                from eva_ml.stage_review import StageReview
                from eva_ml.consultation_v11 import ConsultationV11Provider
                candidate=StageReview(ConsultationV11Provider(engine))
                for s in samples:
                    start=perf_counter()
                    result,calls=candidate.refine(s["request"],s["primary"])
                    contracts.validate_response(s["request"],result)
                    output.write(json.dumps({"cid":s["cid"],"response":result,"calls":calls,
                                             "seconds":perf_counter()-start},ensure_ascii=False)+"\n");output.flush()

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--variant",choices=["zip","eva"],required=True)
    parser.add_argument("--archive",type=Path,default=PRIVATE/"share-comparison/share.zip")
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    run(args.variant,args.out,args.archive)
