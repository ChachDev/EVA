from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
import math
import os

from sqlalchemy.engine import make_url


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent


def _environment_bool(name: str, default: str = "false") -> bool:
    value = os.getenv(name, default).lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false.")
    return value == "true"


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
    ml_max_concurrency: int = field(default_factory=lambda: int(os.getenv("ML_MAX_CONCURRENCY", "2")))
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
        url = urlsplit(self.ml_url)
        if (url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"}
                or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}):
            raise ValueError("ML_URL must be a plain HTTP loopback origin; external providers are disabled.")
        if (type(self.ml_timeout_seconds) not in {int, float}
                or not math.isfinite(self.ml_timeout_seconds) or self.ml_timeout_seconds <= 0):
            raise ValueError("ML_TIMEOUT_SECONDS must be finite and positive.")
        if type(self.ml_max_concurrency) is not int or not 1 <= self.ml_max_concurrency <= 32:
            raise ValueError("ML_MAX_CONCURRENCY must be an integer between 1 and 32.")
        for origin in self.cors_origins:
            parsed = urlsplit(origin)
            if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                    or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password):
                raise ValueError("CORS_ORIGINS must contain explicit local HTTP origins.")

    @property
    def effective_database_url(self) -> str:
        if self.database_path is not None:
            return "sqlite:///" + self.database_path.resolve().as_posix()
        return self.database_url
