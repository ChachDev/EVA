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
from app.media import MediaService, MediaConflict
from app.workflows import WorkflowService, WorkflowConflict


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

def test_batch_workers_claim_distinct_items_and_restart(postgres_store,postgres_url,contracts):
    from app.batches import BatchStore,Start
    q=BatchStore(postgres_store);q.initialize()
    check=contracts.example("checklist");postgres_store.seed_checklist(check)
    group=unique_id()
    conv=[dict(contracts.example("conversation"),id=unique_id()) for _ in range(4)]
    postgres_store.import_conversations(group,conv)
    job=q.start(Start(group_id=group,checklist_id=check["id"],checklist_version=1),"baseline")
    barrier=Barrier(4)
    def claim(_):
        barrier.wait();return q.claim("baseline",120)
    with ThreadPoolExecutor(max_workers=4) as executor:claimed=list(executor.map(claim,range(4)))
    assert {x["cid"] for x in claimed}=={c["id"] for c in conv}
    for x in claimed:q.finish(x,"failed","Synthetic failure")
    another=EvaluationStore(postgres_url)
    try:
        resumed=BatchStore(another)
        assert resumed.get(job["id"])["counts"]=={"failed":4}
        assert resumed.retry(job["id"])["counts"]=={"queued":4}
        for _ in range(4):resumed.finish(resumed.claim("baseline",120),"failed","Synthetic failure")
    finally:another.close()


def test_postgres_migrations_are_idempotent(postgres_store):
    assert postgres_store.engine.dialect.name == "postgresql"
    postgres_store.initialize()
    assert {"evaluations", "evaluation_inputs", "conversations", "checklist_versions"}.issubset(
        inspect(postgres_store.engine).get_table_names()
    )
    with postgres_store.engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0003_media"


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


def test_postgres_identical_concurrent_imports_are_idempotent(postgres_store, request_data):
    conversation = deepcopy(request_data["conversation"])
    conversation["id"] = unique_id()
    barrier = Barrier(2)

    def attempt(_index):
        barrier.wait(timeout=5)
        return postgres_store.import_conversations("synthetic-pg", [conversation])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, [0, 1]))
    assert sum(row["inserted"] for row in results) == 1
    assert sum(row["unchanged"] for row in results) == 1
    assert postgres_store.get_conversation(conversation["id"]) == conversation


def test_postgres_review_conflicts_replays_and_restart(postgres_url, postgres_store, contracts, request_data):
    record = contracts.example("evaluation-record")
    record["id"] = unique_id()
    postgres_store.create(record, request_data)
    workflow = WorkflowService(postgres_store, contracts)
    barrier = Barrier(2)
    appeal_operations = [str(uuid4()), str(uuid4())]

    def appeal(index):
        barrier.wait(timeout=5)
        try:
            return workflow.create_appeal(record["id"], operation_id=appeal_operations[index],
                                         expected_revision=0, criterion_id="contact",
                                         reason="Synthetic appeal", message_ids=["m0001"])
        except WorkflowConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        attempts = list(executor.map(appeal, [0, 1]))
    cases = [case for case in attempts if case is not None]
    assert len(cases) == 1
    appealed = cases[0]
    assert appealed["revision"] == 1
    appeal_id = appealed["appeals"][0]["id"]
    override = deepcopy(record["original"]["criteria"][0])
    override.update(value=False, reason="Synthetic reviewer correction",
                    evidence=[{"kind": "message", "message_ids": ["m0001"]}])
    decision_operations = [str(uuid4()), str(uuid4())]
    barrier = Barrier(2)

    def decide(index):
        barrier.wait(timeout=5)
        try:
            return workflow.decide_appeal(appeal_id, operation_id=decision_operations[index],
                                         expected_revision=1, decision="accepted",
                                         reason="Synthetic decision", override=override)
        except WorkflowConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        attempts = list(executor.map(decide, [0, 1]))
    assert sum(case is not None for case in attempts) == 1
    winner = next(index for index, case in enumerate(attempts) if case is not None)
    final = attempts[winner]
    contracts.validate("review-case", final)
    assert final["revision"] == 2
    assert final["original"] == record
    assert final["final"]["scoring"]["total"] < record["scoring"]["total"]
    assert workflow.decide_appeal(appeal_id, operation_id=decision_operations[winner],
                                 expected_revision=1, decision="accepted",
                                 reason="Synthetic decision", override=override) == final
    restarted = EvaluationStore(postgres_url)
    try:
        assert WorkflowService(restarted, contracts).get_review_case(record["id"]) == final
        assert restarted.get_snapshot(record["id"]) == request_data
    finally:
        restarted.close()


def test_postgres_concurrent_checklist_versions_have_one_winner(postgres_store, contracts, request_data):
    checklist = deepcopy(request_data["checklist"])
    checklist["id"] = unique_id()
    postgres_store.seed_checklist(checklist)
    workflow = WorkflowService(postgres_store, contracts)
    barrier = Barrier(2)

    def publish(index):
        barrier.wait(timeout=5)
        try:
            return workflow.publish_checklist(checklist["id"], base_version=1,
                                              name=f"Synthetic version {index}", criteria=checklist["criteria"])
        except WorkflowConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, [0, 1]))
    winners = [item for item in results if item is not None]
    assert len(winners) == 1
    assert winners[0]["version"] == 2
    assert postgres_store.get_checklist(checklist["id"], 1) == checklist


def test_postgres_audio_lease_review_and_restart(postgres_url, postgres_store, contracts, tmp_path):
    import io
    import wave

    data = io.BytesIO()
    with wave.open(data, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 8000)
    media = MediaService(postgres_store, tmp_path / "audio")
    asset = media.upload(data.getvalue(), "audio/wav", str(uuid4()))
    job = media.create_job(asset["id"], "RU", str(uuid4()))
    barrier = Barrier(2)

    def claim(_index):
        barrier.wait(timeout=5)
        return media.claim_job(job["id"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, [0, 1]))
    leases = [item for item in claims if item is not None]
    assert len(leases) == 1
    transcript = {"schema_version": "1.0.0", "audio_sha256": asset["sha256"], "language": "RU",
                  "provider": "test-local", "model": "synthetic-response",
                  "segments": [{"speaker": "unknown", "text": "Synthetic utterance",
                                "start_seconds": 0.0, "end_seconds": 0.9}]}
    with pytest.raises(MediaConflict):
        media.complete_job(job["id"], str(uuid4()), transcript)
    pending = media.complete_job(job["id"], leases[0]["lease_token"], transcript)
    assert pending["status"] == "needs_review"
    assert pending["conversation_id"] is None
    operation = str(uuid4())
    completed = media.review_job(job["id"], ["operator"], operation)
    contracts.validate("transcription-job", completed)
    assert completed["result"] == transcript
    assert completed["status"] == "completed"
    assert media.review_job(job["id"], ["operator"], operation) == completed
    restarted = EvaluationStore(postgres_url)
    try:
        assert MediaService(restarted, tmp_path / "audio").get_job(job["id"]) == completed
        conversation = restarted.get_conversation(completed["conversation_id"])
        contracts.validate("conversation", conversation)
        assert conversation["messages"][0]["speaker"] == "operator"
    finally:
        restarted.close()
