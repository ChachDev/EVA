"""TEST ONLY: isolated Backend for synthetic browser workflow checks.

This is not a model, a quality benchmark, or a production entry point. Never
send real client data here. Every score is a deliberately generated fixture.
Run from backend with: .venv/Scripts/python.exe tests/browser_server.py --port 58123

Each invocation keeps a new SQLite file under tests/.tmp/ and audio storage
under backend/data/browser-tests/. Nothing is deleted on shutdown. While this
server is running, use another pytest --basetemp rather than tests/.tmp/.
ML and ASR are never called. Special synthetic text markers exercise errors:
CONTEXT_LIMIT_TEST -> 422; UNAVAILABLE_TEST -> 503.
"""

import argparse
from pathlib import Path
import sys
from uuid import uuid4


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.ml_client import MLContextLimitExceeded, MLUnavailable


WARNING = "SYNTHETIC BROWSER TEST ONLY: fixture values; no model inference or quality assessment."


async def fixture_evaluator(request: dict) -> dict:
    """Return deterministic fixture decisions for the exact requested checklist."""
    messages = request["conversation"]["messages"]
    if any("CONTEXT_LIMIT_TEST" in message["text"] for message in messages):
        raise MLContextLimitExceeded("Synthetic browser context-limit test.")
    if any("UNAVAILABLE_TEST" in message["text"] for message in messages):
        raise MLUnavailable("Synthetic browser unavailable test.")
    first = next((message for message in messages if message["speaker"] == "operator"), messages[0])
    criteria = []
    for criterion in request["checklist"]["criteria"]:
        value = (max(criterion["scale"]) if criterion["type"] == "scale"
                 else criterion["type"] != "flag")
        criteria.append({
            "criterion_id": criterion["id"], "status": "scored", "value": value,
            "reason": WARNING, "evidence": [{"kind": "message", "message_ids": [first["id"]]}],
        })
    return {
        "schema_version": request["schema_version"],
        "conversation_id": request["conversation"]["id"],
        "checklist_id": request["checklist"]["id"],
        "checklist_version": request["checklist"]["version"],
        "provider": "fixture", "model": "browser-test-only",
        "criteria": criteria, "warnings": [WARNING],
    }


def create_browser_app():
    """Create a new local test workspace; the production app is never served."""
    from app.config import Settings
    from app.main import create_app

    identifier = uuid4().hex
    directory = BACKEND_ROOT / "tests" / ".tmp" / ("browser-" + identifier)
    directory.mkdir(parents=True, exist_ok=False)
    database = directory / "synthetic.sqlite3"
    media = BACKEND_ROOT / "data" / "browser-tests" / identifier
    settings = Settings(
        database_path=database, seed_demo=True,
        ml_url="http://127.0.0.1:1", ml_timeout_seconds=20,
        ml_max_concurrency=1, evaluation_queue_capacity=4,
        request_max_bytes=1024 * 1024, ml_max_response_bytes=2 * 1024 * 1024,
        media_root=media, audio_max_bytes=25 * 1024 * 1024,
        asr_url=None, asr_timeout_seconds=300, reviewer_id="browser-test-reviewer",
        cors_origins=("http://localhost:5173", "http://127.0.0.1:5173"),
    )
    application = create_app(settings, evaluator=fixture_evaluator, start_workers=False)
    application.title = "EVA SYNTHETIC BROWSER TEST ONLY"
    application.description = WARNING
    application.state.browser_test_directory = directory
    return application


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True, help="Unused local test port; production ports are forbidden")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or args.port in {5173, 8000, 8001, 5432, 55432}:
        parser.error("Select an unused test port from 1024 to 65535 outside the normal service ports.")

    import uvicorn

    application = create_browser_app()
    print(WARNING, flush=True)
    print("Synthetic workspace: " + str(application.state.browser_test_directory), flush=True)
    uvicorn.run(application, host="127.0.0.1", port=args.port,
                workers=1, reload=False, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
