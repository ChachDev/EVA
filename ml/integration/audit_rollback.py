"""Read the same history through the actual parent software, using a SQLite backup."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from zipfile import ZipFile

root=Path(__file__).resolve().parents[2]
receipt=json.loads(Path(sys.argv[1]).read_text(encoding="utf8"))
source=Path(sys.argv[2]).resolve()
parent="a8ee9d3c3f942422849adbf2d85087b5c9fc7f75"
target=Path(tempfile.mkdtemp(prefix="audit-rollback-",dir=root/"ml"/".tmp"))
archive=target/"source.zip"
subprocess.run(["git","archive","--format=zip","--output",str(archive),parent,"backend","contracts"],cwd=root,check=True)
with ZipFile(archive) as handle: handle.extractall(target)
db=target/"backend"/"copy.sqlite"
with sqlite3.connect(source.as_uri()+"?mode=ro",uri=True) as original,sqlite3.connect(db) as copy:
    original.backup(copy)
os.chdir(target/"backend")
sys.path.insert(0,str(target/"backend"))
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
with TestClient(create_app(Settings(database_path=db),start_workers=False)) as client:
    eid=receipt["evaluation"]
    doc=client.get("/api/v1/evaluations/"+eid).json()
    case=client.get("/api/v1/review-cases/"+eid).json()
    assert doc["id"]==eid and case["original"]==doc and case["revision"]==receipt["revision"]
    assert len(case["decisions"])==1 and len(case["appeals"])==1
    assert client.get("/api/v1/conversations/"+receipt["conversation"]).status_code==200
    assert client.get("/api/v1/evaluations/"+receipt["derived"]).status_code==200
print("PASS: prior commit reads original, derived result, conversation and criterion review on isolated DB copy")
