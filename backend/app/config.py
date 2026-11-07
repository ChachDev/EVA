from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
import math
import os
import re

from sqlalchemy.engine import make_url


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent


def _environment_bool(name: str, default: str = "false") -> bool:
    value = os.getenv(name, default).lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false.")
    return value == "true"


def _local_origin(value: str, name: str) -> None:
    try:
        if not isinstance(value, str):
            raise ValueError()
        url = urlsplit(value)
        if (url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"}
                or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}
                or (url.port is not None and not 1 <= url.port <= 65535)):
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a plain HTTP loopback origin.") from None


@dataclass(frozen=True)
class Settings:
    contracts_dir: Path = REPOSITORY_ROOT / "contracts"
    database_url: str = field(default_factory=lambda: os.getenv(
        "DATABASE_URL", "postgresql+psycopg://eva:eva-local-dev@127.0.0.1:55432/eva"
    ), repr=False)
    # An explicit adapter for isolated tests and existing local SQLite databases.
    database_path: Path | None = None
    seed_demo: bool = field(default_factory=lambda: _environment_bool("SEED_DEMO"))
    ml_url: str = field(default_factory=lambda: os.getenv("ML_URL", "http://127.0.0.1:8001"))
    ml_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("ML_TIMEOUT_SECONDS", "20")))
    ml_max_concurrency: int = field(default_factory=lambda: int(os.getenv("ML_MAX_CONCURRENCY", "1")))
    evaluation_queue_capacity: int = field(default_factory=lambda: int(os.getenv("EVALUATION_QUEUE_CAPACITY", "4")))
    request_max_bytes: int = field(default_factory=lambda: int(os.getenv("REQUEST_MAX_BYTES", str(1024 * 1024))))
    ml_max_response_bytes: int = field(default_factory=lambda: int(os.getenv("ML_MAX_RESPONSE_BYTES", str(2 * 1024 * 1024))))
    media_root: Path = field(default_factory=lambda: Path(os.getenv("MEDIA_ROOT", str(BACKEND_ROOT / "data" / "audio"))), repr=False)
    audio_max_bytes: int = field(default_factory=lambda: int(os.getenv("AUDIO_MAX_BYTES", str(25 * 1024 * 1024))))
    asr_url: str | None = field(default_factory=lambda: os.getenv("ASR_URL") or None)
    asr_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("ASR_TIMEOUT_SECONDS", "300")))
    reviewer_id: str = field(default_factory=lambda: os.getenv("REVIEWER_ID", "local-reviewer"))
    cors_origins: tuple[str, ...] = field(default_factory=lambda: tuple(
        origin.strip() for origin in os.getenv(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",") if origin.strip()
    ))

    def __post_init__(self):
        if type(self.seed_demo) is not bool:
            raise ValueError("SEED_DEMO must be true or false.")
        try:
            database = make_url(self.effective_database_url)
            if database.drivername == "postgresql+psycopg":
                if database.host not in {"localhost", "127.0.0.1", "::1"} or database.query:
                    raise ValueError()
                if not database.database or not database.username:
                    raise ValueError()
                if database.port is not None and not 1 <= database.port <= 65535:
                    raise ValueError()
            elif database.drivername == "sqlite":
                if database.host or database.query or not database.database or database.database == ":memory:":
                    raise ValueError()
                if not Path(database.database).resolve().is_relative_to(BACKEND_ROOT):
                    raise ValueError()
            else:
                raise ValueError()
        except Exception:
            raise ValueError("DATABASE_URL must select local PostgreSQL or an explicit SQLite file inside backend.") from None
        _local_origin(self.ml_url, "ML_URL")
        if self.asr_url is not None:
            _local_origin(self.asr_url, "ASR_URL")
        if (type(self.ml_timeout_seconds) not in {int, float}
                or not math.isfinite(self.ml_timeout_seconds) or self.ml_timeout_seconds <= 0):
            raise ValueError("ML_TIMEOUT_SECONDS must be finite and positive.")
        if type(self.ml_max_concurrency) is not int or not 1 <= self.ml_max_concurrency <= 32:
            raise ValueError("ML_MAX_CONCURRENCY must be an integer between 1 and 32.")
        if type(self.evaluation_queue_capacity) is not int or not 0 <= self.evaluation_queue_capacity <= 100:
            raise ValueError("EVALUATION_QUEUE_CAPACITY must be an integer between 0 and 100.")
        for name, value, upper in (
            ("REQUEST_MAX_BYTES", self.request_max_bytes, 10 * 1024 * 1024),
            ("ML_MAX_RESPONSE_BYTES", self.ml_max_response_bytes, 10 * 1024 * 1024),
            ("AUDIO_MAX_BYTES", self.audio_max_bytes, 100 * 1024 * 1024),
        ):
            if type(value) is not int or not 1 <= value <= upper:
                raise ValueError(f"{name} must be a positive integer within its supported limit.")
        if (type(self.asr_timeout_seconds) not in {int, float}
                or not math.isfinite(self.asr_timeout_seconds) or not 0 < self.asr_timeout_seconds <= 3600):
            raise ValueError("ASR_TIMEOUT_SECONDS must be positive and at most 3600.")
        if not isinstance(self.media_root, Path) or not self.media_root.resolve().is_relative_to((BACKEND_ROOT / "data").resolve()):
            raise ValueError("MEDIA_ROOT must be inside backend/data.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", self.reviewer_id):
            raise ValueError("REVIEWER_ID must be a simple local reviewer identifier.")
        for origin in self.cors_origins:
            _local_origin(origin, "CORS_ORIGINS")
            parsed = urlsplit(origin)
            if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                    or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password):
                raise ValueError("CORS_ORIGINS must contain explicit local HTTP origins.")

    @property
    def effective_database_url(self) -> str:
        if self.database_path is not None:
            return "sqlite:///" + self.database_path.resolve().as_posix()
        return self.database_url
