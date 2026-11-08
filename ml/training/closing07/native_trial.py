"""Run the matched scale-zero/scale-one experiment on a private llama.cpp worker."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import subprocess
import time

from eva_ml.engine import LocalEngine, EngineError
from training.closing07.data import ML
from training.closing07.evaluate import evaluate
from training.closing07.experiment import MODEL, read_features


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--port",type=int,required=True)
    p.add_argument("--weights",type=Path,required=True)
    p.add_argument("--server",type=Path,required=True)
    p.add_argument("--gpu-layers",type=int,default=16)
    args=p.parse_args()
    out=args.out.resolve()
    if not out.is_relative_to(ML/"data"):p.error("Use private ml/data")
    adapter=out/"closing07-r2.gguf"
    fit=json.loads((out/"fit.json").read_text(encoding="utf8"))
    if fit["adapter_sha256"]!=sha256(adapter.read_bytes()).hexdigest():raise ValueError("Adapter hash differs")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1",args.port))
    engine=LocalEngine(args.port,MODEL)
    command=[str(args.server),"--host","127.0.0.1","--port",str(args.port),"-m",str(args.weights),
             "--alias",MODEL,"--offline","--no-webui","--no-warmup","--jinja","--reasoning","off",
             "-c","3072","-np","1","-ngl",str(args.gpu_layers),"--fit","off","--load-mode","none",
             "--cache-ram","0","--lora",str(adapter),"--lora-init-without-apply"]
    with (out/"startup-adapter.log").open("ab") as log:
        proc=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                              creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),
                              env={**os.environ,"HF_HUB_OFFLINE":"1","HF_HUB_DISABLE_TELEMETRY":"1"})
        try:
            until=time.monotonic()+1200
            while time.monotonic()<until:
                if proc.poll() is not None:raise RuntimeError("Private worker exited during startup")
                try:
                    if engine.request("/health",timeout=3)["status"]=="ok":break
                except EngineError:
                    pass
                time.sleep(2)
            else:raise RuntimeError("Worker did not start")
            adapters=engine.request("/lora-adapters")
            if len(adapters)!=1 or adapters[0]["id"]!=0 or Path(adapters[0]["path"]).resolve()!=adapter:
                raise RuntimeError("Different active adapter")
            print("Verified native LoRA identity.",flush=True)
            for scale in (0,1):
                read_features(args.port,out,scale)
            result=evaluate(out)
            with (out/"metrics.json").open("x",encoding="utf8") as f:
                json.dump(result,f,ensure_ascii=False,indent=2)
            print({k:(v["before"]["exact"],v["native_after"]["exact"]) for k,v in result["results"].items()})
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


if __name__=="__main__":
    main()
