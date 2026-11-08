"""Rehearse submission from a sealed evaluation archive and its matching stored input snapshots."""
import argparse,json
from pathlib import Path
from zipfile import ZipFile

from .config import BACKEND_ROOT,REPOSITORY_ROOT
from .contracts import Contracts
from .contest import export_manifest
from .storage import EvaluationStore

def main():
    p=argparse.ArgumentParser();p.add_argument("--archive",required=True,type=Path);p.add_argument("--database",required=True,type=Path)
    p.add_argument("--group",choices=["DEV","PROD"],required=True);p.add_argument("--out",required=True,type=Path)
    a=p.parse_args()
    if not a.out.resolve().is_relative_to((BACKEND_ROOT/"data").resolve()):p.error("Export only to private backend/data")
    if not a.database.is_file():p.error("Missing snapshot database")
    store=EvaluationStore(a.database)
    try:
        batch=dict(group_id=a.group,checklist_id="freedom-cqa",version=1,items=[])
        with ZipFile(a.archive) as archive:
            for member in archive.infolist():
                r=json.loads(archive.read(member));cid=r["original"]["conversation_id"]
                if member.filename!=cid+".json" or r!=store.get(r["id"]):raise ValueError("Archive and saved evaluation differ")
                batch["items"].append(dict(id=r["id"],cid=cid,state="done",error=None))
        doc=export_manifest(store,batch,Contracts(REPOSITORY_ROOT/"contracts"))
        a.out.parent.mkdir(parents=True,exist_ok=True)
        with a.out.open("x",encoding="utf8") as f:json.dump(doc,f,ensure_ascii=False,indent=2,allow_nan=False)
        print("records:",len(doc["results"]),"ok:",sum(r["status"]=="ok" for r in doc["results"]))
    finally:store.close()

if __name__=="__main__":main()
