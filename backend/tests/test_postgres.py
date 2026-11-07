"""Opt-in checks against a dedicated local PostgreSQL test database."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.main import create_app
from app.storage import EvaluationStore, StorageConflict


pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def postgres_url():
    value = os.getenv("EVA_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Set EVA_TEST_DATABASE_URL to a dedicated local database ending in _test.")
    Settings(database_url=value)
    parsed = make_url(value)
    if parsed.drivername != "postgresql+psycopg" or not parsed.database.endswith("_test"):
        pytest.fail("PostgreSQL checks require a dedicated local database ending in _test.")
    return value


@pytest.fixture(scope="module")
def postgres_store(postgres_url):
    store = EvaluationStore(postgres_url)
    store.initialize()
    try:
        yield store
    finally:
        store.close()


def unique_id():
    return "synthetic-pg-" + uuid4().hex


def test_postgres_migrations_are_idempotent(postgres_store):
    assert postgres_store.engine.dialect.name == "postgresql"
    postgres_store.initialize()
    assert {"evaluations", "evaluation_inputs", "conversations", "checklist_versions"}.issubset(
        inspect(postgres_store.engine).get_table_names()
    )
    with postgres_store.engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0001_storage"


def test_postgres_competing_migrators_are_serialized(postgres_url, postgres_store):
    stores = [EvaluationStore(postgres_url), EvaluationStore(postgres_url)]
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda store: store.initialize(), stores))
    finally:
        for store in stores:
            store.close()


def test_postgres_api_evaluation_and_snapshot_survive_restart(postgres_url, contracts, request_data, result_data):
    conversation = deepcopy(request_data["conversation"])
    checklist = deepcopy(request_data["checklist"])
    conversation["id"] = unique_id()
    checklist["id"] = unique_id()
    expected = deepcopy(result_data)
    expected.update(conversation_id=conversation["id"], checklist_id=checklist["id"])

    async def evaluator(request):
        contracts.validate_request(request)
        return deepcopy(expected)

    settings = Settings(database_url=postgres_url, seed_demo=False)
    app = create_app(settings, evaluator)
    with TestClient(app) as client:
        store = app.state.store
        store.import_conversations("synthetic-pg", [conversation])
        store.seed_checklist(checklist)
        response = client.post("/api/v1/evaluations", json={
            "conversation_id": conversation["id"], "checklist_id": checklist["id"], "checklist_version": 1,
        })
        assert response.status_code == 201
        record = response.json()
        contracts.validate("evaluation-record", record)
        snapshot = store.get_snapshot(record["id"])
        assert snapshot == {"schema_version": "1.0.0", "conversation": conversation, "checklist": checklist}
    restarted = create_app(settings, evaluator)
    with TestClient(restarted) as client:
        assert client.get(f"/api/v1/evaluations/{record['id']}").json() == record
        assert restarted.state.store.get_snapshot(record["id"]) == snapshot


def test_postgres_import_conflict_rolls_back_all_records(postgres_store, request_data):
    original = deepcopy(request_data["conversation"])
    original["id"] = unique_id()
    inserted_before_conflict = deepcopy(original)
    inserted_before_conflict["id"] = unique_id()
    postgres_store.import_conversations("synthetic-pg", [original])
    changed = deepcopy(original)
    changed["messages"][0]["text"] = "Synthetic changed content"
    with pytest.raises(StorageConflict):
        postgres_store.import_conversations("synthetic-pg", [inserted_before_conflict, changed])
    assert postgres_store.get_conversation(inserted_before_conflict["id"]) is None
    assert postgres_store.get_conversation(original["id"]) == original


def test_postgres_competing_imports_preserve_one_immutable_record(postgres_url, request_data):
    first = deepcopy(request_data["conversation"])
    first["id"] = unique_id()
    second = deepcopy(first)
    second["messages"][0]["text"] = "Synthetic competing content"
    stores = [EvaluationStore(postgres_url), EvaluationStore(postgres_url)]
    barrier = Barrier(2)

    def attempt(index):
        barrier.wait(timeout=5)
        try:
            stores[index].import_conversations("synthetic-pg", [[first, second][index]])
            return "inserted"
        except StorageConflict:
            return "conflict"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            assert sorted(executor.map(attempt, [0, 1])) == ["conflict", "inserted"]
        assert stores[0].get_conversation(first["id"]) in [first, second]
    finally:
        for store in stores:
            store.close()


def test_postgres_snapshot_failure_rolls_back_evaluation(postgres_store, contracts, request_data):
    record = contracts.example("evaluation-record")
    record["id"] = unique_id()

    def fail_snapshot(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.startswith("INSERT INTO evaluation_inputs"):
            raise IntegrityError("synthetic SQL", {}, Exception("synthetic failure"))

    event.listen(postgres_store.engine, "before_cursor_execute", fail_snapshot)
    try:
        with pytest.raises(StorageConflict):
            postgres_store.create(record, request_data)
    finally:
        event.remove(postgres_store.engine, "before_cursor_execute", fail_snapshot)
    assert postgres_store.get(record["id"]) is None
    assert postgres_store.get_snapshot(record["id"]) is None
