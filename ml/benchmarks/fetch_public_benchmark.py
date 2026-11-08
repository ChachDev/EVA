"""Explicit opt-in download of only the two public synthetic suites and four published result files."""
import argparse
from base64 import b64decode
from hashlib import sha1
import json
from pathlib import Path
from urllib.request import Request, urlopen

from benchmarks.audit_run import ROOT

REPO = "useing123/d00mathon"
REV = "39f9cc1f2c39ec97d864fa99c7505392c2d29b28"
REPORTS = ("results/kz_models/issai_9b_kk_holdout.json","results/kz_models/q38_27b_fp8_holdout.json",
           "results/gold_holdout_v13.json","results/oss/jev3_holdout.json")


def github(path):
    uri=f"https://api.github.com/repos/{REPO}/contents/{path}?ref={REV}"
    with urlopen(Request(uri,headers={"User-Agent":"EVA-public-benchmark"}),timeout=30) as response:
        return json.load(response)


def retrieve(path):
    item=github(path)
    raw=b64decode(item["content"])
    if sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()!=item["sha"]:
        raise ValueError("Git blob mismatch")
    return item["sha"],json.loads(raw.decode("utf-8-sig"))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",type=Path,required=True)
    out=p.parse_args().out.resolve()
    if not out.is_relative_to(ROOT/"data"):
        p.error("Use local ml/data")
    out.mkdir(parents=True,exist_ok=True)
    for folder,count,dest in [("gold_holdout",10,"public-holdout.json"),("gold",16,"public-gold16.json")]:
        files=[]
        locations=sorted(e["name"] for e in github("examples/"+folder) if e["type"]=="file" and e["name"].endswith(".json"))
        if len(locations)!=count:
            raise ValueError("Unexpected suite size")
        for name in locations:
            blob,data=retrieve("examples/"+folder+"/"+name)
            if data.get("synthetic") is not True:
                raise ValueError("Not declared synthetic")
            files.append({"file":name,"git_blob":blob,"data":data})
        with (out/dest).open("x",encoding="utf8") as f:
            json.dump({"repository":REPO,"commit":REV,"files":files},f,ensure_ascii=False)
    references=[]
    for path in REPORTS:
        blob,data=retrieve(path)
        references.append({"file":path,"git_blob":blob,"report":data})
    with (out/"public-reference-runs.json").open("x",encoding="utf8") as f:
        json.dump(references,f,ensure_ascii=False)


if __name__=="__main__":
    main()
