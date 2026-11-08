"""Optional adapter for the reviewed team-supplied EVA archive; no upstream code is redistributed."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from zipfile import ZipFile

from .contracts import Contracts, ContractError


ARCHIVE_SHA = "38a671764d2f5970101cecfbcbdac1ac4014ccf3d2ceed5cc7f91db6e0ac2a6f"
MODULES = ("__init__.py", "engine.py", "issai.py", "consultation_v11.py", "applicability.py",
           "applicability_v2.py", "applicability_v3.py", "resolution.py", "resolution_v2.py", "transcript_quality.py")


def extract_candidate(path, target):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=ARCHIVE_SHA:
        raise ValueError("EVA_SHARE_ARCHIVE must be the evaluated project archive")
    with ZipFile(BytesIO(data)) as archive:
        names = [f"ml/eva_ml/{m}" for m in MODULES] + ["ml/benchmarks/fact_rules.py", "ml/benchmarks/frozen_checklist.json"]
        for name in names:
            dest = target / name
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(archive.read("EVA/"+name))


# Keep the third-party module namespace separate. Only JSON is exchanged with this process.
CHILD = """
import json,sys,time
sys.path.insert(0,sys.argv[1])
from eva_ml.engine import LocalEngine
from eva_ml.resolution_v2 import ResolutionV2Provider
class Engine(LocalEngine):
    def request(self,route,payload=None,timeout=120):
        self.calls+=1
        return super().request(route,payload,timeout)
eng=Engine(int(sys.argv[2]),sys.argv[3])
candidate=ResolutionV2Provider(eng)
for line in sys.stdin:
    sample=json.loads(line)
    eng.calls=0
    start=time.perf_counter()
    response=candidate.refine(sample['request'],sample['primary'])
    print(json.dumps({'cid':sample['cid'],'response':response,'calls':eng.calls,
                      'seconds':time.perf_counter()-start},ensure_ascii=True),flush=True)
"""


def command(target, engine):
    url = urlsplit(engine.base)
    if url.scheme!="http" or url.hostname!="127.0.0.1" or not url.port:
        raise ValueError("Expected the existing local model worker")
    return [sys.executable,"-I","-c",CHILD,str(target/"ml"),str(url.port),engine.model]


class ArchiveReview:
    def __init__(self,primary,path):
        self.primary=primary
        self.model=primary.model
        self.name=primary.name
        self.contracts=Contracts()
        self.tmp=TemporaryDirectory(prefix="eva-resolution-")
        try:
            target=Path(self.tmp.name)
            extract_candidate(path,target)
            self.command=command(target,primary.engine)
        except BaseException:
            self.close()
            raise

    def prepare(self):
        return self.primary.prepare()

    def close(self):
        self.tmp.cleanup()

    def evaluate(self,req):
        initial=self.primary.evaluate(req)
        return self.refine(req,initial)

    def refine(self,req,initial):
        cid=req["conversation"]["id"]
        payload=json.dumps({"cid":cid,"request":req,"primary":initial},ensure_ascii=True)+"\n"
        try:
            proc=subprocess.run(self.command,input=payload,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                                timeout=120,text=True,encoding="ascii",check=True,
                                creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            item=json.loads(proc.stdout)
            if item["cid"]!=cid:raise ValueError("Wrong conversation")
            result=item["response"]
            if result["provider"]!=initial["provider"] or result["model"]!=initial["model"]:
                raise ValueError("Wrong model")
            self.contracts.validate_response(req,result)
            return result
        except (OSError,subprocess.SubprocessError,TypeError,ValueError,KeyError,ContractError):
            result=deepcopy(initial)
            result["warnings"].append("resolution-v2: перепроверка недоступна, сохранён исходный ответ.")
            return result
