import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import sqlite3
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError

from app.models import appeals, evaluations, operation_receipts, review_cases, review_decisions
from app.scoring import score
from app.storage import EvaluationStore, StorageConflict
from app.workflows import WorkflowConflict, WorkflowInvalid, WorkflowNotFound, WorkflowService


@pytest.fixture
def store(tmp_path):
    store = EvaluationStore(tmp_path / "workflows.sqlite3")
    store.initialize()
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def workflow(store, contracts):
    return WorkflowService(store, contracts)


def make_record(request_data, result_data, *, evaluation_id=None, created_at="2026-11-07T10:00:00.000000Z"):
    scoring = score(request_data, result_data)
    return {"id": evaluation_id or str(uuid4()),
            "status": "completed" if scoring["status"] == "complete" else "needs_review",
            "created_at": created_at, "original": deepcopy(result_data), "scoring": scoring}


@pytest.fixture
def record(store, request_data, result_data):
    record = make_record(request_data, result_data)
    store.create(record, request_data)
    return record


def appeal_arguments(request_data, **changes):
    return {"operation_id": str(uuid4()), "expected_revision": 0,
            "criterion_id": request_data["checklist"]["criteria"][0]["id"],
            "reason": "Synthetic appeal reason", "message_ids": [request_data["conversation"]["messages"][0]["id"]],
            **changes}


def override_for(record, *, value=False):
    override = deepcopy(record["original"]["criteria"][0])
    override.update(status="scored", value=value, reason="Synthetic reviewer explanation")
    override["evidence"] = [{"kind": "message", "message_ids": ["m0001"]}]
    return override


def decision_arguments(**changes):
    return {"operation_id": str(uuid4()), "expected_revision": 1,
            "decision": "rejected", "reason": "Synthetic decision reason", **changes}


def counts(store):
    with store.engine.connect() as connection:
        return [connection.execute(select(func.count()).select_from(table)).scalar_one()
                for table in (review_cases, appeals, review_decisions, operation_receipts)]


def test_get_case_is_read_only_and_uses_original_record(workflow, store, record, contracts):
    case = workflow.get_review_case(record["id"])
    contracts.validate("review-case", case)
    assert case["revision"] == 0
    assert case["review_status"] == "pending"
    assert case["snapshot_available"] is True
    assert case["original"] == record
    assert case["final"] == {"criteria": record["original"]["criteria"], "scoring": record["scoring"]}
    assert counts(store) == [0, 0, 0, 0]


def test_workflow_timestamps_sort_chronologically_at_zero_microseconds(workflow, record, request_data, monkeypatch):
    class FrozenDatetime(datetime):
        microseconds = 0

        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 7, 10, 0, 0, cls.microseconds, tzinfo=timezone.utc)

    monkeypatch.setattr("app.workflows.datetime", FrozenDatetime)
    workflow.create_appeal(record["id"], **appeal_arguments(request_data))
    FrozenDatetime.microseconds = 900000
    later = workflow.create_appeal(record["id"], **appeal_arguments(request_data, expected_revision=1))
    assert [row["created_at"] for row in later["appeals"]] == [
        "2026-11-07T10:00:00.000000Z", "2026-11-07T10:00:00.900000Z",
    ]


def test_appeals_accept_canonical_long_criterion_and_message_ids(workflow, store, request_data, result_data):
    request_data["checklist"]["criteria"][0]["id"] = "criterion-" + "c" * 4097
    request_data["conversation"]["messages"][0]["id"] = "message-" + "m" * 4097
    result_data["criteria"][0]["criterion_id"] = request_data["checklist"]["criteria"][0]["id"]
    for row in result_data["criteria"]:
        for evidence in row["evidence"]:
            evidence["message_ids"] = [request_data["conversation"]["messages"][0]["id"]
                                       if item == "m0001" else item for item in evidence["message_ids"]]
    record = make_record(request_data, result_data)
    store.create(record, request_data)
    opened = workflow.create_appeal(record["id"], **appeal_arguments(request_data))
    assert opened["appeals"][0]["criterion_id"] == request_data["checklist"]["criteria"][0]["id"]
    assert opened["appeals"][0]["message_ids"] == [request_data["conversation"]["messages"][0]["id"]]


def test_publish_is_immutable_cas_and_supports_new_criterion(workflow, store, request_data):
    first = request_data["checklist"]
    store.seed_checklist(first)
    criteria = deepcopy(first["criteria"])
    criteria[0].update(id="new-generic-criterion", instruction="A new synthetic instruction")
    result = workflow.publish_checklist(first["id"], base_version=first["version"], name="Synthetic v2", criteria=criteria)
    assert result["version"] == first["version"] + 1
    assert result["criteria"][0]["id"] == "new-generic-criterion"
    assert store.get_checklist(first["id"], first["version"]) == first
    assert store.has_checklist(first["id"]) is True
    assert store.has_checklist("absent") is False
    with pytest.raises(WorkflowConflict):
        workflow.publish_checklist(first["id"], base_version=first["version"], name="Stale", criteria=criteria)
    assert len(store.list_checklists()) == 2


def test_invalid_publish_rolls_back_and_unknown_checklist_is_not_found(workflow, store, request_data):
    first = request_data["checklist"]
    store.seed_checklist(first)
    with pytest.raises(WorkflowInvalid):
        workflow.publish_checklist(first["id"], base_version=1, name="Bad", criteria=[first["criteria"][0]] * 2)
    with pytest.raises(WorkflowInvalid):
        workflow.publish_checklist(first["id"], base_version=True, name="Bad", criteria=first["criteria"])
    with pytest.raises(WorkflowNotFound):
        workflow.publish_checklist("absent", base_version=1, name="New", criteria=first["criteria"])
    assert store.list_checklists() == [first]


def test_base_zero_publishes_a_new_identity_once(workflow, store, request_data):
    criteria = request_data["checklist"]["criteria"]
    published = workflow.publish_checklist("new-checklist", base_version=0, name="Synthetic new checklist", criteria=criteria)
    assert published["version"] == 1
    assert store.get_checklist("new-checklist", 1) == published
    with pytest.raises(WorkflowConflict):
        workflow.publish_checklist("new-checklist", base_version=0, name="Different", criteria=criteria)


def test_history_has_stable_ties_pagination_and_conversation_filter(workflow, store, request_data, result_data, contracts):
    records = []
    for evaluation_id in ["a", "b", "c", "d"]:
        record = make_record(request_data, result_data, evaluation_id=evaluation_id)
        store.create(record, request_data)
        records.append(record)
    other_result = deepcopy(result_data)
    other_result["conversation_id"] = "another-synthetic-conversation"
    other_request = deepcopy(request_data)
    other_request["conversation"]["id"] = other_result["conversation_id"]
    store.create(make_record(other_request, other_result, evaluation_id="z"))
    first = workflow.list_evaluations(conversation_id=result_data["conversation_id"], limit=2)
    contracts.validate("evaluation-list", first)
    second = workflow.list_evaluations(conversation_id=result_data["conversation_id"], limit=2, cursor=first["next_cursor"])
    assert [row["id"] for row in first["items"]] == ["d", "c"]
    assert [row["id"] for row in second["items"]] == ["b", "a"]
    assert second["next_cursor"] is None
    assert workflow.list_evaluations(conversation_id="absent") == {"items": [], "next_cursor": None}
    assert [row["id"] for row in workflow.list_evaluations(limit=100)["items"]] == ["z", "d", "c", "b", "a"]


@pytest.mark.parametrize("cursor", ["", "not-base64!", "x" * 4097, "e30", "WzEsMl0", "WyIiLCJ4Il0",
                                    base64.urlsafe_b64encode(b'["time","x\\u0000"]' ).decode(),
                                    base64.urlsafe_b64encode(b'["time","x\\ud800"]' ).decode(),
                                    base64.urlsafe_b64encode(b'["\\udfff","identity"]' ).decode()])
def test_invalid_history_cursor_is_safe(workflow, cursor):
    with pytest.raises(WorkflowInvalid, match="History cursor is invalid"):
        workflow.list_evaluations(cursor=cursor)


@pytest.mark.parametrize("limit", [0, 101, True, "2"])
def test_invalid_history_limits_are_rejected(workflow, limit):
    with pytest.raises(WorkflowInvalid):
        workflow.list_evaluations(limit=limit)


def test_history_metadata_is_backfilled_without_rewriting_legacy_payload(tmp_path, request_data, result_data, contracts):
    path = tmp_path / "legacy.sqlite3"
    record = make_record(request_data, result_data)
    payload = json.dumps(record, ensure_ascii=False, indent=2)
    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.execute("CREATE TABLE evaluations (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)")
            connection.execute("INSERT INTO evaluations VALUES (?, ?, ?)", (record["id"], record["created_at"], payload))
    finally:
        connection.close()
    store = EvaluationStore(path)
    try:
        store.initialize()
        workflow = WorkflowService(store, contracts)
        assert workflow.list_evaluations(conversation_id=result_data["conversation_id"])["items"] == [record]
        with store.engine.connect() as connection:
            row = connection.execute(select(evaluations)).mappings().one()
            assert row["payload"] == payload
            assert row["checklist_id"] == result_data["checklist_id"]
            assert row["checklist_version"] == result_data["checklist_version"]
        assert workflow.get_review_case(record["id"])["snapshot_available"] is False
        with pytest.raises(WorkflowConflict):
            workflow.get_snapshot(record["id"])
        with pytest.raises(WorkflowConflict):
            workflow.create_appeal(record["id"], **appeal_arguments(request_data))
        assert counts(store) == [0, 0, 0, 0]
    finally:
        store.close()


def test_accepted_decision_recalculates_from_snapshot_and_preserves_original(workflow, store, record, request_data, contracts):
    store.seed_checklist(request_data["checklist"])
    changed = deepcopy(request_data["checklist"])
    changed["version"] += 1
    changed["criteria"][0]["weight"] = 90
    store.seed_checklist(changed)
    opened = workflow.create_appeal(record["id"], **appeal_arguments(request_data))
    assert opened["revision"] == 1
    assert opened["review_status"] == "appealed"
    result = workflow.decide_appeal(opened["appeals"][0]["id"], **decision_arguments(
        decision="accepted", override=override_for(record),
    ))
    contracts.validate("review-case", result)
    assert result["revision"] == 2
    assert result["review_status"] == "resolved"
    assert result["appeals"][0]["status"] == "accepted"
    projected = deepcopy(record["original"])
    projected["criteria"][0] = override_for(record)
    assert result["final"]["scoring"] == score(request_data, projected)
    assert result["final"]["scoring"]["total"] < record["scoring"]["total"]
    assert result["decisions"][0]["reviewer_id"] == "local-reviewer"
    assert store.get(record["id"]) == record
    assert workflow.get_snapshot(record["id"]) == request_data
    assert counts(store) == [1, 1, 1, 2]


def test_rejected_decision_changes_workflow_not_score(workflow, record, request_data):
    opened = workflow.create_appeal(record["id"], **appeal_arguments(request_data))
    decided = workflow.decide_appeal(opened["appeals"][0]["id"], **decision_arguments())
    assert decided["final"] == opened["final"]
    assert decided["appeals"][0]["status"] == "rejected"
    assert decided["decisions"][0]["override"] is None


def test_receipts_return_the_original_response_after_later_mutations(workflow, record, request_data):
    args = appeal_arguments(request_data)
    opened = workflow.create_appeal(record["id"], **args)
    decision = decision_arguments()
    decided = workflow.decide_appeal(opened["appeals"][0]["id"], **decision)
    assert workflow.create_appeal(record["id"], **args) == opened
    workflow.create_appeal(record["id"], **appeal_arguments(request_data, expected_revision=2))
    assert workflow.decide_appeal(opened["appeals"][0]["id"], **decision) == decided
    assert workflow.get_review_case(record["id"])["revision"] == 3
    with pytest.raises(WorkflowConflict):
        workflow.create_appeal(record["id"], **{**args, "reason": "Changed payload"})
    with pytest.raises(WorkflowConflict):
        workflow.decide_appeal(opened["appeals"][0]["id"], **{**decision, "reason": "Changed payload"})
    with pytest.raises(WorkflowConflict):
        workflow.create_appeal("another-evaluation", **args)
    with pytest.raises(WorkflowConflict):
        workflow.decide_appeal("another-appeal", **decision)


@pytest.mark.parametrize("change", [
    {"criterion_id": "foreign-criterion"}, {"message_ids": ["foreign-message"]},
    {"message_ids": []}, {"message_ids": ["m0001", "m0001"]},
    {"reason": " "}, {"reason": "x" * 4001}, {"operation_id": "not-a-uuid"}, {"expected_revision": True},
])
def test_invalid_appeal_never_creates_workflow_state(workflow, store, record, request_data, change):
    with pytest.raises(WorkflowInvalid):
        workflow.create_appeal(record["id"], **appeal_arguments(request_data, **change))
    assert counts(store) == [0, 0, 0, 0]


@pytest.mark.parametrize("change", [
    {"decision": "accepted"},
    {"decision": "unexpected"},
    {"decision": "rejected", "override": {}},
    {"decision": "accepted", "override": {"criterion_id": "foreign", "status": "scored"}},
    {"decision": "accepted", "override": {"criterion_id": "contact", "status": "needs_review"}},
])
def test_invalid_decision_never_updates_revision_or_history(workflow, store, record, request_data, change):
    opened = workflow.create_appeal(record["id"], **appeal_arguments(request_data))
    with pytest.raises(WorkflowInvalid):
        workflow.decide_appeal(opened["appeals"][0]["id"], **decision_arguments(**change))
    assert workflow.get_review_case(record["id"]) == opened
    assert counts(store) == [1, 1, 0, 1]


@pytest.mark.parametrize("mutation", [
    lambda row: row.update(value=1),
    lambda row: row.update(value=None, evidence=[{"kind": "not_applicable", "message_ids": ["m0001"]}]),
    lambda row: row.update(evidence=[{"kind": "message", "message_ids": ["foreign-message"]}]),
    lambda row: row.update(evidence=[{"kind": "uncertain", "message_ids": ["m0001"]}]),
])
def test_override_semantics_use_canonical_validator(workflow, store, record, request_data, mutation):
    opened = workflow.create_appeal(record["id"], **appeal_arguments(request_data))
    override = override_for(record)
    mutation(override)
    with pytest.raises(WorkflowInvalid):
        workflow.decide_appeal(opened["appeals"][0]["id"], **decision_arguments(decision="accepted", override=override))
    assert workflow.get_review_case(record["id"]) == opened


def test_remaining_pending_criteria_keep_final_score_unknown(workflow, store, request_data, result_data):
    pending = deepcopy(result_data)
    for row in pending["criteria"]:
        row.update(status="needs_review", value=None, evidence=[{"kind": "uncertain", "message_ids": ["m0001"]}])
    record = make_record(request_data, pending)
    store.create(record, request_data)
    opened = workflow.create_appeal(record["id"], **appeal_arguments(request_data))
    decided = workflow.decide_appeal(opened["appeals"][0]["id"], **decision_arguments(
        decision="accepted", override=override_for(record, value=True),
    ))
    assert decided["review_status"] == "resolved"
    assert decided["final"]["scoring"]["status"] == "needs_review"
    assert decided["final"]["scoring"]["total"] is None


def test_allowed_na_override_removes_weight_without_changing_original(workflow, record, request_data):
    criterion = next(row for row in request_data["checklist"]["criteria"] if row["allow_na"])
    opened = workflow.create_appeal(record["id"], **appeal_arguments(request_data, criterion_id=criterion["id"]))
    override = {"criterion_id": criterion["id"], "status": "scored", "value": None,
                "reason": "Synthetic justified non-applicability",
                "evidence": [{"kind": "not_applicable", "message_ids": ["m0001"]}]}
    decided = workflow.decide_appeal(opened["appeals"][0]["id"], **decision_arguments(decision="accepted", override=override))
    projected = deepcopy(record["original"])
    projected["criteria"] = [override if row["criterion_id"] == criterion["id"] else row for row in projected["criteria"]]
    assert decided["final"]["scoring"] == score(request_data, projected)
    assert decided["original"] == record


def test_stale_revision_and_second_decision_are_conflicts(workflow, record, request_data):
    opened = workflow.create_appeal(record["id"], **appeal_arguments(request_data))
    with pytest.raises(WorkflowConflict):
        workflow.create_appeal(record["id"], **appeal_arguments(request_data))
    workflow.decide_appeal(opened["appeals"][0]["id"], **decision_arguments())
    with pytest.raises(WorkflowConflict):
        workflow.decide_appeal(opened["appeals"][0]["id"], **decision_arguments(expected_revision=2))


def test_unknown_evaluations_and_appeals_are_not_found(workflow, request_data):
    for operation in (workflow.get_review_case, workflow.get_snapshot):
        with pytest.raises(WorkflowNotFound):
            operation("absent")
    with pytest.raises(WorkflowNotFound):
        workflow.create_appeal("absent", **appeal_arguments(request_data))
    with pytest.raises(WorkflowNotFound):
        workflow.decide_appeal("absent", **decision_arguments())


def test_receipt_failure_rolls_back_decision_and_projection(workflow, store, record, request_data):
    opened = workflow.create_appeal(record["id"], **appeal_arguments(request_data))

    def fail_receipt(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.startswith("INSERT INTO operation_receipts"):
            raise IntegrityError("synthetic", {}, Exception("synthetic-private-marker"))

    event.listen(store.engine, "before_cursor_execute", fail_receipt)
    try:
        with pytest.raises(WorkflowConflict) as error:
            workflow.decide_appeal(opened["appeals"][0]["id"], **decision_arguments(
                decision="accepted", override=override_for(record),
            ))
    finally:
        event.remove(store.engine, "before_cursor_execute", fail_receipt)
    assert "synthetic-private-marker" not in str(error.value)
    assert workflow.get_review_case(record["id"]) == opened
    assert counts(store) == [1, 1, 0, 1]


def test_workflow_and_receipts_survive_restart(tmp_path, contracts, request_data, result_data):
    path = tmp_path / "restart.sqlite3"
    store = EvaluationStore(path)
    record = make_record(request_data, result_data)
    args = appeal_arguments(request_data)
    try:
        store.initialize()
        store.create(record, request_data)
        opened = WorkflowService(store, contracts).create_appeal(record["id"], **args)
    finally:
        store.close()
    restarted = EvaluationStore(path)
    try:
        restarted.initialize()
        workflow = WorkflowService(restarted, contracts)
        assert workflow.get_review_case(record["id"]) == opened
        assert workflow.create_appeal(record["id"], **args) == opened
    finally:
        restarted.close()


def test_concurrent_mutations_with_the_same_revision_do_not_lose_history(store, contracts, record, request_data):
    barrier = Barrier(2)
    stores = [store, EvaluationStore(store.path)]

    def attempt(index):
        barrier.wait(timeout=5)
        try:
            WorkflowService(stores[index], contracts).create_appeal(record["id"], **appeal_arguments(request_data))
            return "created"
        except WorkflowConflict:
            return "conflict"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            assert sorted(executor.map(attempt, [0, 1])) == ["conflict", "created"]
        case = WorkflowService(store, contracts).get_review_case(record["id"])
        assert case["revision"] == 1
        assert len(case["appeals"]) == 1
        assert counts(store) == [1, 1, 0, 1]
    finally:
        stores[1].close()


def test_concurrent_same_operation_returns_one_exact_response(store, contracts, record, request_data):
    barrier = Barrier(2)
    stores = [store, EvaluationStore(store.path)]
    args = appeal_arguments(request_data)

    def attempt(index):
        barrier.wait(timeout=5)
        return WorkflowService(stores[index], contracts).create_appeal(record["id"], **args)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, [0, 1]))
        assert results[0] == results[1]
        assert counts(store) == [1, 1, 0, 1]
    finally:
        stores[1].close()


def test_competing_publications_have_one_winner(store, contracts, request_data):
    store.seed_checklist(request_data["checklist"])
    barrier = Barrier(2)
    stores = [store, EvaluationStore(store.path)]

    def attempt(index):
        barrier.wait(timeout=5)
        try:
            WorkflowService(stores[index], contracts).publish_checklist(
                request_data["checklist"]["id"], base_version=1,
                name=f"Synthetic candidate {index}", criteria=request_data["checklist"]["criteria"],
            )
            return "published"
        except WorkflowConflict:
            return "conflict"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            assert sorted(executor.map(attempt, [0, 1])) == ["conflict", "published"]
        assert len(store.list_checklists()) == 2
    finally:
        stores[1].close()


def test_competing_decisions_do_not_overwrite_history(store, contracts, record, request_data):
    workflow = WorkflowService(store, contracts)
    opened = workflow.create_appeal(record["id"], **appeal_arguments(request_data))
    appeal_id = opened["appeals"][0]["id"]
    barrier = Barrier(2)
    stores = [store, EvaluationStore(store.path)]

    def attempt(index):
        barrier.wait(timeout=5)
        try:
            WorkflowService(stores[index], contracts).decide_appeal(appeal_id, **decision_arguments(
                decision="accepted" if index else "rejected", override=override_for(record) if index else None,
            ))
            return "decided"
        except WorkflowConflict:
            return "conflict"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            assert sorted(executor.map(attempt, [0, 1])) == ["conflict", "decided"]
        case = workflow.get_review_case(record["id"])
        assert case["revision"] == 2
        assert len(case["decisions"]) == 1
        assert counts(store) == [1, 1, 1, 2]
    finally:
        stores[1].close()


@pytest.mark.parametrize("difference", [None, "missing", "group", "normalized", "raw", "missing_raw"])
def test_concurrent_import_recovers_only_the_entire_identical_committed_batch(
        store, request_data, monkeypatch, difference):
    batch = [deepcopy(request_data["conversation"]) for _ in range(2)]
    for index, item in enumerate(batch):
        item["id"] = f"synthetic-race-{index}"
    raw = [{"synthetic_source": index} for index in range(2)]
    committed = deepcopy(batch)
    committed_raw = deepcopy(raw)
    group_id = "synthetic-group"
    if difference == "missing":
        committed, committed_raw = committed[:1], committed_raw[:1]
    elif difference == "group":
        group_id = "different-synthetic-group"
    elif difference == "normalized":
        committed[1]["messages"][0]["text"] = "Different synthetic content"
    elif difference == "raw":
        committed_raw[1] = {"synthetic_source": "different"}
    elif difference == "missing_raw":
        committed_raw = None

    competitor = EvaluationStore(store.path)
    original_write = store._write_connection
    calls = 0

    def fail_second_insert(_connection, _cursor, statement, _parameters, _context, _executemany):
        nonlocal calls
        if statement.startswith("INSERT INTO conversations"):
            calls += 1
            if calls == 2:
                raise IntegrityError("synthetic SQL", {}, Exception("synthetic-private-marker"))

    @contextmanager
    def concurrent_commit_after_rollback():
        try:
            with original_write() as connection:
                yield connection
        except IntegrityError:
            event.remove(store.engine, "before_cursor_execute", fail_second_insert)
            # Simulate the winner committing while the losing transaction rolls back.
            assert competitor.list_conversations() == []
            competitor.import_conversations(group_id, committed, raw_records=committed_raw)
            raise

    event.listen(store.engine, "before_cursor_execute", fail_second_insert)
    monkeypatch.setattr(store, "_write_connection", concurrent_commit_after_rollback)
    try:
        if difference is None:
            assert store.import_conversations("synthetic-group", batch, raw_records=raw) == {
                "group_id": "synthetic-group", "total": 2, "inserted": 0, "unchanged": 2,
            }
        else:
            with pytest.raises(StorageConflict) as error:
                store.import_conversations("synthetic-group", batch, raw_records=raw)
            assert "synthetic-private-marker" not in str(error.value)
            assert "INSERT" not in str(error.value)
        assert store.list_conversations() == committed
        assert calls == 2
    finally:
        if event.contains(store.engine, "before_cursor_execute", fail_second_insert):
            event.remove(store.engine, "before_cursor_execute", fail_second_insert)
        competitor.close()
