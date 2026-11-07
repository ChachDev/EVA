from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
import math
import os


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent


@dataclass(frozen=True)
class Settings:
    contracts_dir: Path = REPOSITORY_ROOT / "contracts"
    database_path: Path = BACKEND_ROOT / "data" / "eva.sqlite3"
    ml_url: str = field(default_factory=lambda: os.getenv("ML_URL", "http://127.0.0.1:8001"))
    ml_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("ML_TIMEOUT_SECONDS", "20")))
    cors_origins: tuple[str, ...] = field(default_factory=lambda: tuple(
        origin.strip() for origin in os.getenv(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",") if origin.strip()
    ))

    def __post_init__(self):
        url = urlsplit(self.ml_url)
        if (url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"}
                or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}):
            raise ValueError("ML_URL must be a plain HTTP loopback origin; external providers are disabled.")
        if not math.isfinite(self.ml_timeout_seconds) or self.ml_timeout_seconds <= 0:
            raise ValueError("ML_TIMEOUT_SECONDS must be finite and positive.")
        for origin in self.cors_origins:
            parsed = urlsplit(origin)
            if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                    or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password):
                raise ValueError("CORS_ORIGINS must contain explicit local HTTP origins.")

