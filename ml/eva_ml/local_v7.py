"""Run the supplied, content-pinned v7 add-on locally; exchange validated JSON only."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Timer
from time import monotonic

from benchmarks import friend_v7
from .contracts import Contracts, ContractError
from .engine import EngineError


class _Budget:
    def __init__(self, engine, limit):
        self.engine = engine
        self.model = engine.model
        self.deadline = monotonic() + limit

    def request(self, route, payload, timeout):
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise EngineError("v7 timeout")
        return self.engine.request(route, payload, timeout=min(timeout, remaining))


class LocalV7:
    def __init__(self, engine, archive):
        if engine.model != friend_v7.MODEL:
            raise ValueError("v7 requires the pinned ISSAI model")
        self.engine = engine
        self.contracts = Contracts()
        self.tmp = TemporaryDirectory(prefix="eva-v7-")
        self.target = Path(self.tmp.name)
        try:
            friend_v7.stage(self.target, archive)
        except BaseException:
            self.close()
            raise

    def close(self):
        self.tmp.cleanup()

    @contextmanager
    def worker(self):
        proc = subprocess.Popen([sys.executable, "-I", "-B", "-c", friend_v7.CHILD, str(self.target/"ml"), self.engine.model],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding="ascii",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        limit = 75
        watchdog = Timer(limit, proc.kill)
        watchdog.daemon = True
        watchdog.start()
        try:
            yield proc, _Budget(self.engine, limit)
        finally:
            watchdog.cancel()
            try:
                proc.stdin.close()
            finally:
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                proc.stdout.close()

    def refine(self, req, primary):
        try:
            with self.worker() as (proc, bounded):
                result, _ = friend_v7.refine(proc, bounded, req, primary, "v7", {})
            self.contracts.validate_response(req, result)
            if (result["model"], result["provider"]) != (primary["model"], primary["provider"]):
                raise ValueError("Unexpected v7 provider")
            return result
        except (OSError, EngineError, ContractError, RuntimeError, ValueError, KeyError, TypeError):
            result = deepcopy(primary)
            result["warnings"].append("Локальная перепроверка v7 недоступна. Сохранён первый ответ EVA.")
            return result
