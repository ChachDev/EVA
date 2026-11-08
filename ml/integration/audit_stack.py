"""Isolated integration host. Attach ONLY to an explicitly selected local, already-loaded worker."""
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
    p.add_argument("--mode",choices=["experiment","shadow","baseline"],default="experiment")
    p.add_argument("--run-dir",type=Path,required=True)
    args=p.parse_args()
    run_dir=args.run_dir.resolve()
    if not run_dir.is_relative_to((ROOT/"ml"/"data").resolve()): p.error("Use an ignored directory under ml/data")
    run_dir.mkdir(parents=True,exist_ok=True)
    if args.part=="ml":
        if not args.engine_port:p.error("Select a local llama.cpp port")
        sys.path.insert(0,str(ROOT/"ml"))
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
            seed_demo=True,ml_url=f"http://127.0.0.1:{args.ml_port}",ml_timeout_seconds=110),start_workers=False)
    uvicorn.run(app,host="127.0.0.1",port=args.port,access_log=False)
if __name__=="__main__":
    main()
