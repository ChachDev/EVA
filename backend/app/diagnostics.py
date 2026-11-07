"""Safe connectivity diagnostics for the local CLI, without migrations or inference."""

import asyncio
import re
from typing import TYPE_CHECKING

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import text

from .ml_client import MLInvalidResponse

if TYPE_CHECKING:
    from .config import Settings
    from .ml_client import MLClient
    from .storage import EvaluationStore


PROBE_TIMEOUT_SECONDS = 2
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+:-]{0,199}")


def _database_ready(store: "EvaluationStore") -> bool:
    with store.engine.connect() as connection:
        return connection.execute(text("SELECT 1")).scalar_one() == 1


async def _database_status(store: "EvaluationStore") -> str:
    try:
        ready = await run_in_threadpool(_database_ready, store)
    except Exception:
        # Connection exceptions may include a DSN or SQL parameters.
        return "unavailable"
    return "ready" if ready else "unavailable"


def _valid_health(health: object) -> bool:
    if not isinstance(health, dict):
        return False
    if (health.get("status") != "ok" or health.get("service") != "eva-ml"
            or health.get("schema_version") != "1.0.0"):
        return False
    return all(isinstance(health.get(key), str) and _IDENTIFIER.fullmatch(health[key]) is not None
               and "://" not in health[key] for key in ("provider", "model"))


async def _ml_status(ml_client: "MLClient") -> tuple[str, dict[str, str]]:
    try:
        health = await ml_client.probe(timeout_seconds=PROBE_TIMEOUT_SECONDS)
    except MLInvalidResponse:
        return "invalid_response", {}
    except Exception:
        # Do not copy exception messages, response bodies or endpoint URLs.
        return "unavailable", {}
    if not _valid_health(health):
        return "invalid_response", {}
    metadata = {key: health[key] for key in ("provider", "model")}
    state = "stub" if health["provider"] == "stub" or health["model"] == "unconfigured" else "ready"
    return state, metadata


async def collect_diagnostics(settings: "Settings", store: "EvaluationStore", ml_client: "MLClient") -> dict[str, object]:
    """Report connectivity and declared ML mode; this does not verify schema or model quality."""
    database, (ml, metadata) = await asyncio.gather(_database_status(store), _ml_status(ml_client))
    return {
        "status": "ready" if database == "ready" and ml == "ready" else "degraded",
        "database": database,
        "ml": ml,
        **metadata,
        "limits": {
            "ml_timeout_seconds": settings.ml_timeout_seconds,
            "ml_max_concurrency": settings.ml_max_concurrency,
        },
    }
