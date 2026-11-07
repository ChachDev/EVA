import asyncio
from copy import deepcopy
import json
import sqlite3

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy.exc import OperationalError

from app import cli
from app.config import Settings
from app.importer import normalize_dataset
from app.main import create_app


def envelope(conversation):
    return {"error": "", "isSuccess": True, "data": {
        "dataset_group_id": "synthetic-test", "total_records": 1, "records": [{
            "conversation_id": conversation["id"], "channel": "agent chat",
            "language": conversation["language"], "audio_url": None,
            "transcript": [{key: message[key] for key in ("speaker", "text", "timestamp")}
                           for message in conversation["messages"]],
        }],
    }}


def test_empty_database_has_no_implicit_demo(tmp_path):
    app = create_app(Settings(database_path=tmp_path / "empty.sqlite3", seed_demo=False))
    with TestClient(app) as client:
        assert client.get("/api/v1/conversations").json() == []
        assert client.get("/api/v1/checklists").json() == []


def test_imported_conversation_is_evaluated_with_immutable_snapshot(
    tmp_path, contracts, request_data, result_data,
):
    conversation = deepcopy(request_data["conversation"])
    conversation["id"] = "imported-synthetic"
    normalized = normalize_dataset(envelope(conversation), contracts)
    result = deepcopy(result_data)
    result["conversation_id"] = conversation["id"]

    async def evaluator(payload):
        assert payload["conversation"]["id"] == conversation["id"]
        # Even a badly behaved injected adapter cannot rewrite the saved request.
        payload["conversation"]["messages"][0]["text"] = "mutated by test adapter"
        return result

    settings = Settings(database_path=tmp_path / "flow.sqlite3", seed_demo=False)
    app = create_app(settings, evaluator)
    with TestClient(app) as client:
        store = app.state.store
        store.seed_checklist(request_data["checklist"])
        store.import_conversations(normalized.group_id, normalized.conversations,
                                   raw_records=normalized.raw_records)
        assert client.get("/api/v1/conversations").json() == normalized.conversations
        response = client.post("/api/v1/evaluations", json={
            "conversation_id": conversation["id"],
            "checklist_id": request_data["checklist"]["id"], "checklist_version": 1,
        })
        assert response.status_code == 201
        record = response.json()
        contracts.validate("evaluation-record", record)
        snapshot = store.get_snapshot(record["id"])
        assert snapshot["conversation"] == normalized.conversations[0]
        assert snapshot["checklist"] == request_data["checklist"]
        version_two = deepcopy(request_data["checklist"])
        version_two.update(version=2, name="Synthetic version two")
        store.seed_checklist(version_two)
        assert store.get_snapshot(record["id"])["checklist"]["version"] == 1
    with TestClient(create_app(settings, evaluator)) as restarted:
        assert restarted.get(f"/api/v1/evaluations/{record['id']}").json() == record
        assert restarted.app.state.store.get_snapshot(record["id"]) == snapshot


def test_deadline_cancels_inference_without_saving_and_releases_slot(tmp_path, request_data, result_data):
    cancelled = []
    calls = 0

    async def evaluator(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            try:
                await asyncio.sleep(5)
            finally:
                cancelled.append(True)
        return deepcopy(result_data)

    app = create_app(Settings(database_path=tmp_path / "deadline.sqlite3", seed_demo=True,
                              ml_timeout_seconds=0.05, ml_max_concurrency=1), evaluator)
    body = {"conversation_id": request_data["conversation"]["id"],
            "checklist_id": request_data["checklist"]["id"], "checklist_version": 1}
    with TestClient(app) as client:
        assert client.post("/api/v1/evaluations", json=body).status_code == 503
        assert cancelled == [True]
        assert client.post("/api/v1/evaluations", json=body).status_code == 201


def test_waiting_for_inference_slot_is_inside_deadline(tmp_path, request_data, result_data):
    calls = 0

    async def evaluator(_):
        nonlocal calls
        calls += 1
        return deepcopy(result_data)

    app = create_app(Settings(database_path=tmp_path / "queued.sqlite3", seed_demo=True,
                              ml_timeout_seconds=0.04, ml_max_concurrency=1), evaluator)

    async def run():
        async with app.router.lifespan_context(app):
            await app.state.ml_slots.acquire()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/v1/evaluations", json={
                    "conversation_id": request_data["conversation"]["id"],
                    "checklist_id": request_data["checklist"]["id"], "checklist_version": 1,
                })
                assert response.status_code == 503
                assert calls == 0
                app.state.ml_slots.release()
    asyncio.run(run())


def test_database_errors_are_sanitized(tmp_path, monkeypatch):
    app = create_app(Settings(database_path=tmp_path / "error.sqlite3"))
    with TestClient(app) as client:
        def failure():
            raise OperationalError("private SQL", {"text": "private transcript"}, Exception("private password"))
        monkeypatch.setattr(app.state.store, "list_conversations", failure)
        response = client.get("/api/v1/conversations")
        assert response.status_code == 503
        assert "private" not in response.text


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://user:secret@example.com/eva",
    "postgresql+psycopg://user:secret@127.0.0.1/eva?host=example.com",
    "sqlite:///:memory:", "sqlite:///C:/outside-eva.sqlite3",
])
def test_database_configuration_rejects_external_or_out_of_scope_locations(url):
    with pytest.raises(ValueError, match="DATABASE_URL") as error:
        Settings(database_url=url)
    assert "secret" not in str(error.value)


def test_cli_import_is_explicit_and_errors_do_not_echo_payload(tmp_path, monkeypatch, capsys, request_data):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "cli.sqlite3").as_posix())
    monkeypatch.setenv("SEED_DEMO", "false")
    monkeypatch.setattr(cli, "BACKEND_ROOT", tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    source = data / "synthetic.json"
    source.write_text(json.dumps(envelope(request_data["conversation"])), encoding="utf-8")
    assert cli.main(["seed-checklist"]) == 0
    assert cli.main(["import-dataset", "--file", str(source), "--group", "synthetic-test"]) == 0
    assert '"inserted": 1' in capsys.readouterr().out
    source.write_text('{"private-dialogue": "secret signed URL"}', encoding="utf-8")
    assert cli.main(["import-dataset", "--file", str(source)]) == 1
    captured = capsys.readouterr()
    assert "Operation failed" in captured.err
    assert "private-dialogue" not in captured.err
    assert "secret" not in captured.err


@pytest.mark.parametrize("unknown_revision", [False, True])
def test_cli_migration_failures_are_sanitized(tmp_path, monkeypatch, capsys, unknown_revision):
    path = tmp_path / "broken-migration.sqlite3"
    connection = sqlite3.connect(path)
    try:
        with connection:
            if unknown_revision:
                connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
                connection.execute("INSERT INTO alembic_version VALUES ('private-revision-marker')")
            else:
                connection.execute("CREATE TABLE evaluations (private_column TEXT)")
    finally:
        connection.close()
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + path.as_posix())
    assert cli.main(["db-upgrade"]) == 1
    captured = capsys.readouterr()
    assert "Operation failed" in captured.err
    assert "private" not in captured.err
    assert "Traceback" not in captured.err
