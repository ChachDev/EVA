"""Round trip a reviewed evaluation + blind label across the previous backend on a private DB copy."""
import json,sqlite3,subprocess,sys,tempfile
from contextlib import closing
from pathlib import Path
from zipfile import ZipFile
ROOT=Path(__file__).resolve().parents[2]
BEFORE="2bb527e"
def main(receipt,source):
    sys.path.insert(0,str(ROOT/"backend"))
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Settings
    with tempfile.TemporaryDirectory(prefix="batch-rollback-",dir=ROOT/"backend/data") as tmp:
        tmp=Path(tmp)
        subprocess.run(["git","archive","--format=zip","--output",str(tmp/"before.zip"),BEFORE,"backend","contracts"],cwd=ROOT,check=True)
        with ZipFile(tmp/"before.zip") as z:z.extractall(tmp)
        db=tmp/"backend/copy.sqlite";eid=receipt["evaluation"]
        with closing(sqlite3.connect(source.resolve().as_uri()+"?mode=ro",uri=True)) as src,closing(sqlite3.connect(db)) as dest:src.backup(dest)
        settings=Settings(database_path=db,batch_worker=False,audit_mode="baseline")
        with TestClient(create_app(settings),raise_server_exceptions=True) as client:
            case=client.get("/api/v1/review-cases/"+eid).json()
            assert case["revision"]==receipt["revision"] and len(case["decisions"])==1
            group=client.get("/api/v1/batch-groups").json()[0]
            req=client.get("/api/v1/blind/"+eid).json()
            b=client.post("/api/v1/batches",json={"group_id":group,"checklist_id":req["checklist"]["id"],
                "checklist_version":req["checklist"]["version"]}).json()
            assert b["counts"]=={"queued":len(b["items"])}
            vote={"reviewer":"rollback-synthetic","answers":{k["id"]:"unknown" for k in req["checklist"]["criteria"]}}
            assert client.post("/api/v1/blind/"+eid,json=vote).status_code==200
        old="""from fastapi.testclient import TestClient
from pathlib import Path
from app.config import Settings
from app.main import create_app
import sys
with TestClient(create_app(Settings(database_path=Path(sys.argv[1])),start_workers=False)) as c:
    case=c.get('/api/v1/review-cases/'+sys.argv[2]).json()
    assert case['revision']==int(sys.argv[3]) and len(case['decisions'])==1
    assert c.get('/api/v1/evaluations/'+sys.argv[4]).status_code==200
    assert c.get('/api/v1/conversations/'+sys.argv[5]).status_code==200
"""
        subprocess.run([sys.executable,"-c",old,str(db),eid,str(receipt["revision"]),receipt["derived"],
                receipt["conversation"]],cwd=tmp/"backend",check=True)
        with TestClient(create_app(settings)) as c:
            assert c.get("/api/v1/batches/"+b["id"]).json()==b
            assert c.get("/api/v1/review-cases/"+eid).json()==case
            assert c.post("/api/v1/blind/"+eid,json={**vote,"answers":{**vote["answers"],"contact":True}}).status_code==409
        print("PASS: old backend reads original, derived score and appeal; batch manifest and blind vote survive return")
if __name__=="__main__":main(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig")),Path(sys.argv[2]))
