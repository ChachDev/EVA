"""An offline llama.cpp worker owned by the ML service lifespan."""
from contextlib import contextmanager
from contextvars import ContextVar
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
CONTEXT_TOKENS = 16384


_usage = ContextVar("eva_request_usage",default=None)

@contextmanager
def inference_usage():
    measured={"calls":0,"tokens":0,"has_usage":True}
    token=_usage.set(measured)
    try:
        yield measured
    finally:
        _usage.reset(token)


class EngineError(RuntimeError):
    pass


class ContextLimitError(EngineError):
    def __init__(self, message, prompt_tokens=None, context_tokens=None):
        super().__init__(message)
        self.prompt_tokens = prompt_tokens if type(prompt_tokens) is int else None
        self.context_tokens = context_tokens if type(context_tokens) is int else None


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
        meter=_usage.get() if route=="/v1/chat/completions" else None
        if meter is not None: meter["calls"]+=1
        try:
            with self.client.open(req, timeout=timeout) as reply:
                result=json.load(reply)
                if meter is not None:
                    usage=result.get("usage",{}).get("total_tokens")
                    if type(usage) is int: meter["tokens"]+=usage
                    else: meter["has_usage"]=False
                return result
        except urllib.error.HTTPError as error:
            if meter is not None: meter["has_usage"]=False
            if error.code == 400:
                try:
                    detail = json.loads(error.read(8192))
                except (OSError, ValueError):
                    detail = None
                fields = detail.get("error") if isinstance(detail, dict) else None
                if isinstance(fields, dict) and fields.get("type") == "exceed_context_size_error":
                    raise ContextLimitError("Input exceeds local model context",
                        fields.get("n_prompt_tokens"), fields.get("n_ctx")) from error
            raise EngineError("Local inference request failed") from error
        except (OSError, ValueError) as error:
            raise EngineError("Local inference request failed") from error


@contextmanager
def loaded_engine():
    # An explicitly supplied loopback worker belongs to the operator, not to this service.
    configured_port = os.getenv("EVA_LLAMA_WORKER_PORT")
    if configured_port is not None:
        if not (1 <= len(configured_port) <= 5 and configured_port.isascii()
                and configured_port.isdecimal() and 1 <= int(configured_port) <= 65535):
            raise EngineError("EVA_LLAMA_WORKER_PORT must be a TCP port.")
        model = Path(os.environ.get("EVA_ISSAI_GGUF", DEFAULT_WEIGHTS)).stem
        worker = LocalEngine(int(configured_port), model)
        listing = worker.request("/v1/models", timeout=5)
        models = listing.get("data") if isinstance(listing, dict) else None
        if not isinstance(models, list) or not any(isinstance(item, dict) and item.get("id") == model for item in models):
            raise EngineError("Configured local worker does not serve the requested model.")
        yield worker
        return
    executable = Path(os.getenv("EVA_LLAMA_SERVER", DEFAULT_ENGINE)).resolve(strict=True)
    weights = Path(os.getenv("EVA_ISSAI_GGUF", DEFAULT_WEIGHTS)).resolve(strict=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    worker = LocalEngine(port, weights.stem)
    args = [str(executable), "-m", str(weights), "--alias", worker.model, "--host", "127.0.0.1",
            "--port", str(port), "--offline", "--no-webui", "--jinja", "--reasoning", "off",
            "-c", str(CONTEXT_TOKENS), "-np", "1", "-ngl", "99", "--load-mode", "none",
            "--cache-ram", "0"]
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
