"""EVA's existing multipart ASR contract, served only on loopback."""
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Lock

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

MODEL_DIR = Path(os.getenv("EVA_ASR_MODEL",Path(__file__).resolve().parents[1]/"artifacts/models/whisper-small"))
MODEL_NAME = "faster-whisper-small"
LIMIT = 25*1024*1024
FORMATS = {"audio/wav":"wav","audio/x-wav":"wav","audio/mpeg":"mp3","audio/flac":"flac","audio/ogg":"ogg"}
SLOT = Lock()


@asynccontextmanager
async def lifespan(_):
    for name in ("config.json","model.bin","tokenizer.json","vocabulary.txt"):
        if not (MODEL_DIR/name).is_file():
            raise RuntimeError("Install the local ASR model with ml/asr/download_model.py")
    yield


app = FastAPI(title="EVA local speech recognition", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get("/health")
def health():
    return {"service":"eva-asr","status":"ok","model":MODEL_NAME,"device":"cpu","languages":["RU","KZ","EN"]}


@app.post("/v1/transcribe")
def transcribe(file: UploadFile=File(...), language:str=Form(...), audio_sha256:str=Form(...)):
    fmt = FORMATS.get(file.content_type)
    if not fmt:
        raise HTTPException(415,"Unsupported audio type.")
    if language not in {"RU","KZ","EN"}:
        raise HTTPException(422,"Unsupported language.")
    if not SLOT.acquire(blocking=False):
        raise HTTPException(503,"Speech recognition is busy.")
    try:
        with TemporaryDirectory(prefix="eva-asr-") as temp:
            path=Path(temp)/("recording."+fmt)
            sha=hashlib.sha256()
            with path.open("wb") as stream:
                while chunk:=file.file.read(65536):
                    if stream.tell()+len(chunk)>LIMIT:raise HTTPException(413,"Audio too large.")
                    sha.update(chunk)
                    stream.write(chunk)
            if path.stat().st_size==0 or sha.hexdigest()!=audio_sha256:
                raise HTTPException(422,"Audio does not match its checksum.")
            try:
                proc=subprocess.run([sys.executable,"-m","eva_asr.worker",str(path),fmt,language,str(MODEL_DIR)],
                    cwd=Path(__file__).resolve().parents[1],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,timeout=540,env={**os.environ,"HF_HUB_OFFLINE":"1"},
                    creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            except subprocess.TimeoutExpired:
                raise HTTPException(503,"Speech recognition timed out.") from None
            if proc.returncode==2:raise HTTPException(422,"Audio contains no readable speech or has an unsupported duration.")
            if proc.returncode:raise HTTPException(503,"Speech recognizer failed.")
            segments=json.loads(proc.stdout)
        return {"schema_version":"1.0.0","audio_sha256":audio_sha256,"language":language,
                "provider":"faster-whisper","model":MODEL_NAME,"segments":segments}
    finally:
        SLOT.release()
