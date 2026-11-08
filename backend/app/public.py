"""Same-origin frontend and anonymous, persistent visitor workspaces on a single host."""
import asyncio
import hashlib
import hmac
import os
import secrets
import shutil
import sqlite3
import time
from contextlib import AsyncExitStack, asynccontextmanager, closing, suppress
from dataclasses import dataclass
from pathlib import Path
from collections import deque
from urllib.parse import urlsplit

from sqlalchemy import select
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from .config import BACKEND_ROOT, Settings
from .contracts import Contracts
from .main import create_app
from .media import transcription_jobs
from .batches import items

COOKIE = "__Host-eva_workspace"
MAX_WORKSPACES = 128
MAX_OPEN = 24
WORKSPACE_BYTES = 128 * 1024 * 1024


@dataclass
class Visitor:
    app: object
    stack: AsyncExitStack
    used: float
    active: int = 0
    reserved_bytes: int = 0

    def pending(self):
        with self.app.state.store.engine.connect() as db:
            return any(db.execute(select(table.c.id).where(state.in_(("queued", "running"))).limit(1)).first()
                       for table, state in ((transcription_jobs, transcription_jobs.c.status), (items, items.c.state)))


def signing_key(folder):
    path = folder / "session.key"
    try:
        with path.open("xb") as stream:
            stream.write(secrets.token_bytes(32))
    except FileExistsError:
        pass
    key = path.read_bytes()
    if len(key) != 32:
        raise RuntimeError("Invalid public session key.")
    return key


def accepts_write(path, size):
    used = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                used += entry.stat().st_size
        except FileNotFoundError:
            continue  # SQLite journals and audio upload temporaries are short-lived.
    return used + size <= WORKSPACE_BYTES and shutil.disk_usage(path).free > 512 * 1024 * 1024


def unfinished_workspaces(root):
    pending = []
    for path in root.glob("*/workspace.sqlite"):
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            for table, state in (("transcription_jobs", "status"), ("eva_batch_items", "state")):
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    if db.execute(f"SELECT 1 FROM {table} WHERE {state} IN ('queued','running') LIMIT 1").fetchone():
                        pending.append(path.parent.name)
                        break
    return pending


class Workspaces:
    def __init__(self, root, dist, ml_url, asr_url):
        self.root = root
        self.static = StaticFiles(directory=dist, html=True)
        self.ml_url, self.asr_url = ml_url, asr_url
        self.visitors = {}
        self.created = deque(maxlen=MAX_WORKSPACES)
        self.lock = asyncio.Lock()
        self.model_slots = asyncio.Semaphore(1)
        self.model_queue = asyncio.Queue(maxsize=5)
        self.checklist = Contracts(BACKEND_ROOT.parent / "contracts").example("checklist")

    def sign(self, value):
        return hmac.new(self.key, value.encode("ascii"), hashlib.sha256).hexdigest()

    def session_id(self, cookie):
        value, _, signature = (cookie or "").partition(".")
        if len(value) == 48 and all(c in "0123456789abcdef" for c in value):
            if len(signature) == 64 and signature.isascii() and hmac.compare_digest(self.sign(value), signature):
                return value
        return None

    async def recover(self, pending):
        while pending:
            identifier = pending.pop(0)
            visitor = await self.open(identifier, "recovery")
            if visitor:
                visitor.active -= 1
            else:
                pending.append(identifier)
                await asyncio.sleep(5)

    async def open(self, identifier, source):
        async with self.lock:
            visitor = self.visitors.get(identifier)
            if visitor is None:
                folder = self.root / identifier
                if not folder.exists():
                    now = time.monotonic()
                    while self.created and self.created[0][0] < now - 600:
                        self.created.popleft()
                    if sum(ip == source for _, ip in self.created) >= 16 or sum(p.is_dir() for p in self.root.iterdir()) >= MAX_WORKSPACES:
                        return None
                    self.created.append((now, source))
                if len(self.visitors) >= MAX_OPEN:
                    for key, candidate in sorted(self.visitors.items(), key=lambda entry: entry[1].used):
                        if not candidate.active and not await run_in_threadpool(candidate.pending):
                            await candidate.stack.aclose()
                            del self.visitors[key]
                            break
                    else:
                        return None
                settings = Settings(database_path=folder / "workspace.sqlite", media_root=folder / "audio",
                    seed_demo=False, ml_url=self.ml_url, asr_url=self.asr_url, media_base_url="/",
                    ml_timeout_seconds=110, asr_timeout_seconds=600, cors_origins=(), audit_mode="shadow",
                    batch_worker=True, batch_revision="public-v7-evidence")
                backend = create_app(settings)
                stack = AsyncExitStack()
                try:
                    await stack.enter_async_context(backend.router.lifespan_context(backend))
                    await run_in_threadpool(backend.state.store.seed_checklist, self.checklist)
                except BaseException:
                    await stack.aclose()
                    raise
                backend.state.ml_slots = self.model_slots
                backend.state.evaluation_requests = self.model_queue
                visitor = self.visitors[identifier] = Visitor(backend, stack, time.monotonic())
            visitor.active += 1
            visitor.used = time.monotonic()
            return visitor

    async def __call__(self, scope, receive, send):
        request = Request(scope)
        path = request.url.path
        async def error(status, msg):
            await JSONResponse({"detail": msg}, status, headers={"Cache-Control":"no-store"})(scope, receive, send)

        if path == "/health":
            return await JSONResponse({"status":"ok","service":"backend"})(scope, receive, send)
        identifier = self.session_id(request.cookies.get(COOKIE))
        if not path.startswith("/api/"):
            async def static_send(event):
                if event["type"] == "http.response.start":
                    headers = event.setdefault("headers", [])
                    if any(k == b"content-type" and b"text/html" in v for k, v in headers):
                        headers.extend([(b"cache-control", b"private, no-store"), (b"vary", b"Cookie")])
                        if not identifier:
                            key = secrets.token_hex(24)
                            token = f"{COOKIE}={key}.{self.sign(key)}; Path=/; Max-Age=2592000; Secure; HttpOnly; SameSite=Lax"
                            headers.append((b"set-cookie", token.encode("ascii")))
                await send(event)
            return await self.static(scope, receive, static_send)
        if not identifier:
            return await error(401, "Open the EVA homepage to start your workspace.")
        write = request.method not in ("GET", "HEAD", "OPTIONS")
        if write:
            origin = request.headers.get("origin")
            if request.headers.get("sec-fetch-site") == "cross-site" or (origin and urlsplit(origin).netloc != request.headers.get("host")):
                return await error(403, "Cross-site request denied.")
        visitor = await self.open(identifier, request.headers.get("cf-connecting-ip") or request.client.host)
        if not visitor:
            return await error(503, "Public workspace capacity is reached.")
        async def private_send(event):
            if event["type"] == "http.response.start":
                event.setdefault("headers", []).append((b"cache-control", b"private, no-store"))
            await send(event)
        reserved = 0
        try:
            if write and request.method != "DELETE":
                size = request.headers.get("content-length")
                if not size or not size.isdecimal() or len(size) > 10:
                    return await error(411, "Content length required.")
                reserved = int(size)
                visitor.reserved_bytes += reserved
                if not await run_in_threadpool(accepts_write, self.root / identifier, visitor.reserved_bytes):
                    return await error(413, "Workspace storage limit reached.")
            await visitor.app(scope, receive, private_send)
        finally:
            visitor.reserved_bytes -= reserved
            visitor.active -= 1


def public_app(root=None, dist=None, ml_url=None, asr_url=None):
    gateway = Workspaces(root or BACKEND_ROOT / "data" / "public-workspaces",
        dist or BACKEND_ROOT.parent / "frontend" / "dist",
        ml_url or os.getenv("PUBLIC_ML_URL", "http://127.0.0.1:18721"),
        asr_url or os.getenv("PUBLIC_ASR_URL", "http://127.0.0.1:18602"))

    @asynccontextmanager
    async def lifespan(app):
        gateway.root.mkdir(parents=True, exist_ok=True)
        gateway.key = signing_key(gateway.root)
        pending = await run_in_threadpool(unfinished_workspaces, gateway.root)
        recovery = asyncio.create_task(gateway.recover(pending))
        try:
            yield
        finally:
            recovery.cancel()
            with suppress(asyncio.CancelledError):
                await recovery
            for visitor in gateway.visitors.values():
                await visitor.stack.aclose()
    return Starlette(routes=[Mount("/", app=gateway)], lifespan=lifespan)
