"""Fetch only the four pinned public model files. Runtime ASR works offline."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import urllib.request

MODEL_DIR = Path(__file__).resolve().parents[1] / "artifacts/models/whisper-small"
REVISION = "536b0662742c02347bc0e980a01041f333bce120"
FILES = {
    "config.json": (2370, "e5047537059bd8f182d9ca64c470201585015187"),
    "model.bin": (483546902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
    "tokenizer.json": (2203239, "7818adb6de9fa3064d3ff81226fdd675be1f6344"),
    "vocabulary.txt": (459861, "c9074644d9d1205686f16d411564729461324b75"),
}

def check(path, size, expected):
    if path.stat().st_size != size:
        raise ValueError(f"Unexpected file size: {path.name}")
    with path.open("rb") as stream:
        if len(expected)==64:
            signature=hashlib.file_digest(stream,"sha256")
        else:
            signature=hashlib.sha1(f"blob {size}\0".encode())
            signature.update(stream.read())
    if signature.hexdigest()!=expected:
        raise ValueError(f"Checksum mismatch: {path.name}")

def download(name):
    size,expected=FILES[name]
    target=MODEL_DIR/name
    if target.is_file():
        check(target,size,expected)
        return
    url=f"https://huggingface.co/Systran/faster-whisper-small/resolve/{REVISION}/{name}"
    partial=target.with_suffix(target.suffix+".partial")
    req=urllib.request.Request(url,headers={"User-Agent":"EVA-ASR/1.0"})
    with urllib.request.urlopen(req,timeout=120) as response, partial.open("wb") as stream:
        while chunk:=response.read(1048576):
            stream.write(chunk)
            if stream.tell()>size:raise ValueError(f"Oversized download: {name}")
    check(partial,size,expected)
    partial.replace(target)
    print("Verified",name,size,flush=True)

if __name__=="__main__":
    MODEL_DIR.mkdir(parents=True,exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(download,FILES))
