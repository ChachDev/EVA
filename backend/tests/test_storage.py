from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3
from threading import Barrier

import pytest
from sqlalchemy import event, inspect, insert, select, text
from sqlalchemy.exc import IntegrityError

from app.models import conversations, evaluation_inputs, evaluations
from app.storage import EvaluationStore, StorageConflict


@pytest.fixture
def store(tmp_path):
    result = EvaluationStore(tmp_path / "eva.sqlite3")
    result.initialize()
    try:
        yield result
    finally:
        result.close()


def conversation(conversation_id="synthetic-001"):
    return {
        "id": conversation_id, "channel": "agent_chat", "language": "RU", "audio_url": None,
        "messages": [{"id": "m0001", "speaker": "operator", "text": "Здравствуйте!",
                      "timestamp": "00:01", "offset_seconds": 1}],
    }


def test_initialize_migrates_and_is_idempotent(store):
    store.initialize()
    assert set(inspect(store.engine).get_table_names()) == {
        "alembic_version", "evaluations", "evaluation_inputs", "conversations", "checklist_versions",
    }
    with store.engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0001_storage"
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "delete"


def test_legacy_evaluation_is_adopted_without_rewriting_payload(tmp_path, contracts):
    path = tmp_path / "legacy.sqlite3"
    record = contracts.example("evaluation-record")
    legacy_payload = json.dumps(record, ensure_ascii=False, indent=2)
    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.execute("CREATE TABLE evaluations (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)")
            connection.execute("INSERT INTO evaluations VALUES (?, ?, ?)",
                               (record["id"], record["created_at"], legacy_payload))
    finally:
        connection.close()
    store = EvaluationStore(path)
    try:
        store.initialize()
        store.initialize()
        assert store.get(record["id"]) == record
        assert store.get_snapshot(record["id"]) is None
        with store.engine.connect() as connection:
            assert connection.execute(select(evaluations.c.payload)).scalar_one() == legacy_payload
    finally:
        store.close()
    # Windows must release the file once the explicit store lifecycle ends.
    path.rename(tmp_path / "moved.sqlite3")


def test_incompatible_legacy_schema_is_rejected_without_partial_migration(tmp_path):
    path = tmp_path / "incompatible.sqlite3"
    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.execute("CREATE TABLE evaluations (payload TEXT)")
            connection.execute("INSERT INTO evaluations VALUES ('synthetic')")
    finally:
        connection.close()
    store = EvaluationStore(path)
    try:
        with pytest.raises(RuntimeError, match="incompatible"):
            store.initialize()
        assert inspect(store.engine).get_table_names() == ["evaluations"]
        with store.engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT payload FROM evaluations").scalar_one() == "synthetic"
    finally:
        store.close()


def test_import_persists_raw_provenance_and_normalized_data_idempotently(store):
    data = conversation()
    raw = {"conversation_id": data["id"], "transcript": [{"timestamp": "00:01", "text": "synthetic"}]}
    assert store.import_conversations("synthetic-group", [data], raw_records=[raw]) == {
        "group_id": "synthetic-group", "total": 1, "inserted": 1, "unchanged": 0,
    }
    assert store.import_conversations("synthetic-group", [dict(reversed(list(data.items())))]) == {
        "group_id": "synthetic-group", "total": 1, "inserted": 0, "unchanged": 1,
    }
    assert store.get_conversation(data["id"]) == data
    assert store.list_conversations() == [data]
    assert store.get_conversation("absent") is None
    with store.engine.connect() as connection:
        row = connection.execute(select(conversations)).mappings().one()
        assert row["group_id"] == "synthetic-group"
        assert json.loads(row["raw_payload"]) == raw
        assert len(row["content_hash"]) == 64
    assert "group_id" not in store.get_conversation(data["id"])


@pytest.mark.parametrize("changed_group", [False, True])
def test_conflicting_import_rolls_back_the_entire_batch(store, changed_group):
    original = conversation()
    store.import_conversations("original-group", [original])
    conflicting = deepcopy(original)
    if not changed_group:
        conflicting["messages"][0]["text"] = "Изменённый синтетический текст"
    with pytest.raises(StorageConflict):
        store.import_conversations(
            "different-group" if changed_group else "original-group",
            [conversation("new-before-conflict"), conflicting],
        )
    assert store.get_conversation("new-before-conflict") is None
    assert store.list_conversations() == [original]


def test_import_rejects_duplicate_ids_and_mismatched_raw_records(store):
    with pytest.raises(StorageConflict):
        store.import_conversations("synthetic-group", [conversation(), conversation()])
    with pytest.raises(ValueError, match="same length"):
        store.import_conversations("synthetic-group", [conversation()], raw_records=[])
    assert store.list_conversations() == []


def test_changed_raw_provenance_is_not_silently_accepted(store):
    data = conversation()
    store.import_conversations("synthetic-group", [data], raw_records=[{"source": "original"}])
    with pytest.raises(StorageConflict):
        store.import_conversations("synthetic-group", [data], raw_records=[{"source": "changed"}])
    with store.engine.connect() as connection:
        assert json.loads(connection.execute(select(conversations.c.raw_payload)).scalar_one()) == {"source": "original"}


def test_checklist_versions_are_immutable_and_sorted(store, request_data):
    first = request_data["checklist"]
    second = deepcopy(first)
    second["version"] = first["version"] + 1
    second["name"] = "New synthetic version"
    store.seed_checklist(second)
    store.seed_checklist(first)
    store.seed_checklist(dict(reversed(list(first.items()))))
    assert store.list_checklists() == [first, second]
    assert store.get_checklist(first["id"], first["version"]) == first
    assert store.get_checklist(first["id"], 999) is None
    changed = deepcopy(first)
    changed["criteria"][0]["weight"] += 1
    with pytest.raises(StorageConflict):
        store.seed_checklist(changed)
    assert store.list_checklists() == [first, second]


def test_evaluation_and_snapshot_survive_restart_without_public_shape_changes(tmp_path, contracts, request_data):
    path = tmp_path / "restart.sqlite3"
    record = contracts.example("evaluation-record")
    store = EvaluationStore(path)
    try:
        store.initialize()
        store.create(record, request_data)
    finally:
        store.close()
    restarted = EvaluationStore(path)
    try:
        restarted.initialize()
        assert restarted.get(record["id"]) == record
        assert restarted.get_snapshot(record["id"]) == request_data
        assert restarted.get("absent") is None
        assert restarted.get_snapshot("absent") is None
        changed = deepcopy(record)
        changed["status"] = "needs_review"
        with pytest.raises(StorageConflict):
            restarted.create(changed, {"different": "snapshot"})
        assert restarted.get(record["id"]) == record
        assert restarted.get_snapshot(record["id"]) == request_data
    finally:
        restarted.close()


def test_snapshot_failure_rolls_back_evaluation_and_redacts_sql(store, contracts, request_data, caplog):
    record = contracts.example("evaluation-record")
    private_marker = "synthetic-private-marker"

    def fail_snapshot(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.startswith("INSERT INTO evaluation_inputs"):
            raise IntegrityError("synthetic SQL", {"private": private_marker}, Exception(private_marker))

    event.listen(store.engine, "before_cursor_execute", fail_snapshot)
    try:
        with pytest.raises(StorageConflict) as error:
            store.create(record, request_data)
    finally:
        event.remove(store.engine, "before_cursor_execute", fail_snapshot)
    assert private_marker not in str(error.value)
    assert "INSERT" not in str(error.value)
    assert private_marker not in caplog.text
    assert store.get(record["id"]) is None
    assert store.get_snapshot(record["id"]) is None
    assert store.engine.echo is False
    assert store.engine.hide_parameters is True


def test_sqlite_foreign_keys_apply_to_new_connections(store):
    store.close()
    with store.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    with pytest.raises(IntegrityError):
        with store.engine.begin() as connection:
            connection.execute(insert(evaluation_inputs).values(
                evaluation_id="absent", request_payload="{}", content_hash="synthetic",
            ))


def test_competing_sqlite_writes_do_not_replace_immutable_conversation(tmp_path):
    path = tmp_path / "concurrent.sqlite3"
    stores = [EvaluationStore(path), EvaluationStore(path)]
    barrier = Barrier(2)
    stores[0].initialize()
    first = conversation()
    second = deepcopy(first)
    second["messages"][0]["text"] = "Другая синтетическая версия"

    def attempt(index):
        barrier.wait(timeout=5)
        try:
            stores[index].import_conversations("synthetic-group", [[first, second][index]])
            return "inserted"
        except StorageConflict:
            return "conflict"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, [0, 1]))
        assert sorted(results) == ["conflict", "inserted"]
        assert stores[0].get_conversation(first["id"]) in [first, second]
        assert len(stores[0].list_conversations()) == 1
    finally:
        for store in stores:
            store.close()


def test_explicit_sqlite_memory_url_runs_migrations_and_persists(store):
    memory = EvaluationStore("sqlite+pysqlite:///:memory:")
    try:
        memory.initialize()
        memory.import_conversations("synthetic-group", [conversation()])
        assert memory.list_conversations() == [conversation()]
    finally:
        memory.close()


def test_unsupported_database_driver_is_rejected():
    with pytest.raises(ValueError, match="PostgreSQL"):
        EvaluationStore("mysql://127.0.0.1/eva")
