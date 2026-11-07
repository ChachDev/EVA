"""An offline llama.cpp worker owned by the ML service lifespan."""
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.request


DEFAULT_ENGINE = "D:/EVA-ML/runtime/b12186/llama-server.exe"
DEFAULT_WEIGHTS = "D:/EVA-ML/models/Qwen3.5-9B-Kazakh.Q5_K_M.gguf"


class EngineError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise EngineError("Unexpected inference redirect")


class LocalEngine:
    def __init__(self, port, model):
        self.base = f"http://127.0.0.1:{port}"
        self.model = model
        self.client = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, route, payload=None, timeout=120):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base + route, data=body, headers={"Content-Type":"application/json"})
        try:
            with self.client.open(req, timeout=timeout) as reply:
                return json.load(reply)
        except (OSError, ValueError) as error:
            raise EngineError("Local inference request failed") from error


@contextmanager
def loaded_engine():
    executable = Path(os.getenv("EVA_LLAMA_SERVER", DEFAULT_ENGINE)).resolve(strict=True)
    weights = Path(os.getenv("EVA_ISSAI_GGUF", DEFAULT_WEIGHTS)).resolve(strict=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    worker = LocalEngine(port, weights.stem)
    args = [str(executable), "-m", str(weights), "--alias", worker.model, "--host", "127.0.0.1",
            "--port", str(port), "--offline", "--no-webui", "--jinja", "--reasoning", "off",
            "-c", "8192", "-np", "1", "-ngl", "99", "--load-mode", "none"]
    started = time.monotonic()
    proc = subprocess.Popen(args, cwd=executable.parent, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                            env={**os.environ, "HF_HUB_OFFLINE":"1", "HF_HUB_DISABLE_TELEMETRY":"1"})
    try:
        while time.monotonic() - started < 900:
            if proc.poll() is not None:
                raise EngineError(f"Local llama.cpp exited during startup ({proc.returncode})")
            try:
                if worker.request("/health", timeout=2).get("status") == "ok":
                    models = worker.request("/v1/models", timeout=2)
                    if any(m["id"] == worker.model for m in models["data"]):
                        break
            except EngineError:
                pass
            time.sleep(0.5)
        else:
            raise EngineError("Local model startup timed out")
        logging.getLogger("uvicorn.error").info("ISSAI worker ready in %.3f s", time.monotonic()-started)
        yield worker
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
