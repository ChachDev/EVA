"""Serve an EVA integration database and local model, optionally with an audio worker."""
import argparse

import os
from pathlib import Path
import sys
import uvicorn

ROOT=Path(__file__).resolve().parents[2]

def main():
    p=argparse.ArgumentParser()
    p.add_argument("part",choices=["ml","backend"])
    p.add_argument("--port",type=int,required=True)
    p.add_argument("--engine-port",type=int)
    p.add_argument("--ml-port",type=int,default=18601)
    p.add_argument("--asr-port",type=int,help="Enable the durable audio worker using this local ASR service.")
    p.add_argument("--mode",choices=["experiment","shadow","baseline"],default="experiment")
    p.add_argument("--run-dir",type=Path,required=True)
    args=p.parse_args()
    run_dir=args.run_dir.resolve()
    if not run_dir.is_relative_to((ROOT/"ml"/"data").resolve()): p.error("Use an ignored directory under ml/data")
    run_dir.mkdir(parents=True,exist_ok=True)
    if args.part=="ml":
        sys.path.insert(0,str(ROOT/"ml"))
        if args.engine_port:
            os.environ["EVA_LLAMA_WORKER_PORT"]=str(args.engine_port)
        os.environ["EVA_ML_PROVIDER"]="issai"
        from eva_ml.app import app
    else:
        sys.path.insert(0,str(ROOT/"backend"))
        from app.config import Settings
        from app.main import create_app
        db_root=ROOT/"backend"/"data"/"audit-integration"/run_dir.name
        db_root.mkdir(parents=True,exist_ok=True)
        app=create_app(Settings(database_path=db_root/"audit.sqlite",audit_mode=args.mode,cors_origins=("http://127.0.0.1:5188",),
            seed_demo=True,ml_url=f"http://127.0.0.1:{args.ml_port}",ml_timeout_seconds=110,
            media_base_url=f"http://127.0.0.1:{args.port}",
            asr_url=f"http://127.0.0.1:{args.asr_port}" if args.asr_port else None,
            asr_timeout_seconds=600,batch_worker=False),start_workers=args.asr_port is not None)
    uvicorn.run(app,host="127.0.0.1",port=args.port,access_log=False)
if __name__=="__main__":
    main()
