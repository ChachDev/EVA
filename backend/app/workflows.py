"""Version publication, immutable history and append-only criterion review."""

import base64
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from uuid import UUID, uuid4

from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from .contracts import ContractError, Contracts
from .models import appeals, checklist_versions, evaluation_inputs, evaluations, operation_receipts, review_cases, review_decisions
from .scoring import score
from .storage import EvaluationStore, _digest, _document


class WorkflowNotFound(ValueError):
    pass


class WorkflowConflict(ValueError):
    pass


class WorkflowInvalid(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _load(payload: str) -> dict:
    try:
        result = json.loads(payload)
        if not isinstance(result, dict):
            raise ValueError()
    except (ValueError, TypeError):
        raise WorkflowInvalid("Stored workflow data is invalid.") from None
    return result


def _text(value: object, label: str, maximum: int | None = 4000) -> None:
    if not isinstance(value, str) or not value.strip() or (maximum is not None and len(value) > maximum):
        raise WorkflowInvalid(f"Invalid {label}.")


class WorkflowService:
    def __init__(self, store: EvaluationStore, contracts: Contracts, *, reviewer_id: str = "local-reviewer"):
        _text(reviewer_id, "reviewer identity", 128)
        self.store = store
        self.contracts = contracts
        self.reviewer_id = reviewer_id

    @contextmanager
    def _read_connection(self) -> Iterator[Connection]:
        with self.store.engine.connect() as connection:
            if connection.dialect.name == "postgresql":
                connection = connection.execution_options(isolation_level="REPEATABLE READ")
            with connection.begin():
                yield connection

    def publish_checklist(self, checklist_id: str, *, base_version: int, name: str, criteria: list[dict]) -> dict:
        _text(checklist_id, "checklist identity")
        if type(base_version) is not int or base_version < 0:
            raise WorkflowInvalid("Invalid base version.")
        checklist = {"id": checklist_id, "version": base_version + 1, "name": name, "criteria": criteria}
        try:
            self.contracts.validate("checklist", checklist)
            ids = [item["id"] for item in criteria]
            if len(ids) != len(set(ids)):
                raise WorkflowInvalid("Checklist criterion identities must be unique.")
        except ContractError:
            raise WorkflowInvalid("Invalid checklist configuration.") from None
        try:
            with self.store._write_connection() as connection:
                latest_query = select(checklist_versions.c.version).where(
                    checklist_versions.c.checklist_id == checklist_id,
                ).order_by(checklist_versions.c.version.desc()).limit(1)
                exists = connection.execute(latest_query.with_for_update()).scalar_one_or_none()
                if exists is None and base_version != 0:
                    raise WorkflowNotFound("Checklist was not found.")
                # Read again after acquiring the lock: another publication may have just committed.
                latest = connection.execute(latest_query).scalar_one_or_none() or 0
                if latest != base_version:
                    raise WorkflowConflict("The checklist base version is no longer current.")
                payload = _document(checklist)
                connection.execute(insert(checklist_versions).values(
                    checklist_id=checklist_id, version=base_version + 1,
                    payload=payload, content_hash=_digest(payload),
                ))
        except IntegrityError:
            raise WorkflowConflict("Checklist publication conflicts with another operation.") from None
        return checklist

    def list_evaluations(self, *, conversation_id: str | None = None,
                         limit: int = 25, cursor: str | None = None) -> dict:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise WorkflowInvalid("History limit must be between 1 and 100.")
        if conversation_id is not None:
            _text(conversation_id, "conversation identity")
        query = select(evaluations.c.id, evaluations.c.created_at, evaluations.c.payload)
        if conversation_id is not None:
            query = query.where(evaluations.c.conversation_id == conversation_id)
        if cursor is not None:
            created_at, evaluation_id = self._decode_cursor(cursor)
            query = query.where(or_(evaluations.c.created_at < created_at, and_(
                evaluations.c.created_at == created_at, evaluations.c.id < evaluation_id,
            )))
        query = query.order_by(evaluations.c.created_at.desc(), evaluations.c.id.desc()).limit(limit + 1)
        with self._read_connection() as connection:
            rows = connection.execute(query).mappings().all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            last = page[-1]
            encoded = json.dumps([last["created_at"], last["id"]], ensure_ascii=False).encode("utf-8")
            next_cursor = base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")
        return {"items": [_load(row["payload"]) for row in page], "next_cursor": next_cursor}

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[str, str]:
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 4096:
                raise ValueError()
            decoded = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            values = json.loads(decoded.decode("utf-8"))
            if (not isinstance(values, list) or len(values) != 2
                    or any(not isinstance(item, str) or not item or len(item) > 2000 for item in values)):
                raise ValueError()
            if any(any(ord(character) < 32 or ord(character) == 127 for character in item) for item in values):
                raise ValueError()
            for item in values:
                item.encode("utf-8")
            return values[0], values[1]
        except (ValueError, TypeError, UnicodeError):
            raise WorkflowInvalid("History cursor is invalid.") from None

    def get_snapshot(self, evaluation_id: str) -> dict:
        with self._read_connection() as connection:
            self._evaluation(connection, evaluation_id)
            return self._snapshot(connection, evaluation_id)

    def get_review_case(self, evaluation_id: str) -> dict:
        with self._read_connection() as connection:
            original = self._evaluation(connection, evaluation_id)
            return self._case(connection, evaluation_id, original)

    @staticmethod
    def _evaluation(connection: Connection, evaluation_id: str, *, lock: bool = False) -> dict:
        query = select(evaluations.c.payload).where(evaluations.c.id == evaluation_id)
        if lock:
            query = query.with_for_update()
        payload = connection.execute(query).scalar_one_or_none()
        if payload is None:
            raise WorkflowNotFound("Evaluation was not found.")
        return _load(payload)

    def _snapshot(self, connection: Connection, evaluation_id: str) -> dict:
        payload = connection.execute(select(evaluation_inputs.c.request_payload).where(
            evaluation_inputs.c.evaluation_id == evaluation_id,
        )).scalar_one_or_none()
        if payload is None:
            raise WorkflowConflict("The historical input snapshot is unavailable.")
        snapshot = _load(payload)
        try:
            self.contracts.validate_request(snapshot)
        except ContractError:
            raise WorkflowInvalid("The historical input snapshot is invalid.") from None
        return snapshot

    def _case(self, connection: Connection, evaluation_id: str, original: dict) -> dict:
        stored = connection.execute(select(review_cases).where(
            review_cases.c.evaluation_id == evaluation_id,
        )).mappings().one_or_none()
        response = _load(stored["final_response"]) if stored else original["original"]
        scoring = _load(stored["final_scoring"]) if stored else original["scoring"]
        decision_rows = connection.execute(select(review_decisions).where(
            review_decisions.c.evaluation_id == evaluation_id,
        ).order_by(review_decisions.c.created_at, review_decisions.c.id)).mappings().all()
        decisions = [{
            "id": row["id"], "appeal_id": row["appeal_id"], "evaluation_id": evaluation_id,
            "decision": row["decision"], "reason": row["reason"], "reviewer_id": row["reviewer_id"],
            "override": _load(row["override_payload"]) if row["override_payload"] is not None else None,
            "created_at": row["created_at"],
        } for row in decision_rows]
        states = {row["appeal_id"]: row["decision"] for row in decisions}
        appeal_rows = connection.execute(select(appeals).where(
            appeals.c.evaluation_id == evaluation_id,
        ).order_by(appeals.c.created_at, appeals.c.id)).mappings().all()
        public_appeals = [{
            "id": row["id"], "evaluation_id": evaluation_id, "criterion_id": row["criterion_id"],
            "reason": row["reason"], "message_ids": json.loads(row["message_ids"]),
            "created_at": row["created_at"], "status": states.get(row["id"], "open"),
        } for row in appeal_rows]
        has_snapshot = connection.execute(select(evaluation_inputs.c.evaluation_id).where(
            evaluation_inputs.c.evaluation_id == evaluation_id,
        )).scalar_one_or_none() is not None
        result = {
            "evaluation_id": evaluation_id, "revision": stored["revision"] if stored else 0,
            "review_status": ("pending" if not public_appeals else
                              "appealed" if any(row["status"] == "open" for row in public_appeals) else "resolved"),
            "snapshot_available": has_snapshot, "original": original,
            "final": {"criteria": response["criteria"], "scoring": scoring},
            "appeals": public_appeals, "decisions": decisions,
        }
        try:
            self.contracts.validate("review-case", result)
        except ContractError:
            raise WorkflowInvalid("Stored review data does not satisfy the workflow contract.") from None
        return result

    @staticmethod
    def _operation(operation_id: str, expected_revision: int, reason: str) -> None:
        try:
            if not isinstance(operation_id, str):
                raise ValueError()
            UUID(operation_id)
        except (ValueError, TypeError, AttributeError):
            raise WorkflowInvalid("Operation identity must be a UUID.") from None
        if type(expected_revision) is not int or expected_revision < 0:
            raise WorkflowInvalid("Expected revision must be a nonnegative integer.")
        _text(reason, "reason")

    @staticmethod
    def _receipt(connection: Connection, operation_id: str, request_hash: str) -> dict | None:
        row = connection.execute(select(operation_receipts).where(
            operation_receipts.c.operation_id == operation_id,
        )).mappings().one_or_none()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise WorkflowConflict("Operation identity has already been used for a different request.")
        return _load(row["response_payload"])

    @staticmethod
    def _save_receipt(connection: Connection, operation_id: str, request_hash: str, response: dict) -> None:
        connection.execute(insert(operation_receipts).values(
            operation_id=operation_id, request_hash=request_hash, response_payload=_document(response),
        ))

    @staticmethod
    def _update_case(connection: Connection, evaluation_id: str, revision: int, original: dict,
                     *, response: dict | None = None, scoring: dict | None = None) -> None:
        existing = connection.execute(select(review_cases.c.evaluation_id).where(
            review_cases.c.evaluation_id == evaluation_id,
        )).scalar_one_or_none()
        if existing is None:
            connection.execute(insert(review_cases).values(
                evaluation_id=evaluation_id, revision=revision,
                final_response=_document(response if response is not None else original["original"]),
                final_scoring=_document(scoring if scoring is not None else original["scoring"]),
            ))
        else:
            values = {"revision": revision}
            if response is not None:
                values.update(final_response=_document(response), final_scoring=_document(scoring))
            connection.execute(update(review_cases).where(review_cases.c.evaluation_id == evaluation_id).values(**values))

    def create_appeal(self, evaluation_id: str, *, operation_id: str, expected_revision: int,
                      criterion_id: str, reason: str, message_ids: list[str]) -> dict:
        self._operation(operation_id, expected_revision, reason)
        _text(criterion_id, "criterion identity", None)
        if (not isinstance(message_ids, list) or not message_ids
                or len(message_ids) > 10000
                or any(not isinstance(item, str) or not item for item in message_ids)
                or len(message_ids) != len(set(message_ids))):
            raise WorkflowInvalid("Appeal evidence requires unique message identities.")
        request_hash = _digest(_document({"kind": "appeal", "evaluation_id": evaluation_id,
            "expected_revision": expected_revision, "criterion_id": criterion_id,
            "reason": reason, "message_ids": message_ids}))
        try:
            with self.store._write_connection() as connection:
                replay = self._receipt(connection, operation_id, request_hash)
                if replay is not None:
                    return replay
                # Lock the immutable evaluation row even when its review case does not yet exist.
                original = self._evaluation(connection, evaluation_id, lock=True)
                replay = self._receipt(connection, operation_id, request_hash)
                if replay is not None:
                    return replay
                snapshot = self._snapshot(connection, evaluation_id)
                case = self._case(connection, evaluation_id, original)
                if case["revision"] != expected_revision:
                    raise WorkflowConflict("Review revision is no longer current.")
                if criterion_id not in {row["id"] for row in snapshot["checklist"]["criteria"]}:
                    raise WorkflowInvalid("Criterion does not belong to this evaluation snapshot.")
                if not set(message_ids) <= {row["id"] for row in snapshot["conversation"]["messages"]}:
                    raise WorkflowInvalid("Evidence does not belong to this evaluation snapshot.")
                connection.execute(insert(appeals).values(
                    id=str(uuid4()), evaluation_id=evaluation_id, criterion_id=criterion_id,
                    reason=reason, message_ids=json.dumps(message_ids, ensure_ascii=False), created_at=_now(),
                ))
                self._update_case(connection, evaluation_id, expected_revision + 1, original)
                response = self._case(connection, evaluation_id, original)
                self._save_receipt(connection, operation_id, request_hash, response)
                return response
        except IntegrityError:
            raise WorkflowConflict("The appeal conflicts with another operation.") from None

    def decide_appeal(self, appeal_id: str, *, operation_id: str, expected_revision: int,
                      decision: str, reason: str, override: dict | None = None) -> dict:
        self._operation(operation_id, expected_revision, reason)
        if decision not in {"accepted", "rejected"}:
            raise WorkflowInvalid("Decision must be accepted or rejected.")
        if ((decision == "accepted" and not isinstance(override, dict))
                or (decision == "rejected" and override is not None)):
            raise WorkflowInvalid("Accepted decisions require an override; rejected decisions cannot contain one.")
        try:
            request_hash = _digest(_document({"kind": "decision", "appeal_id": appeal_id,
                "expected_revision": expected_revision, "decision": decision, "reason": reason, "override": override}))
        except (TypeError, ValueError):
            raise WorkflowInvalid("Decision content is invalid.") from None
        try:
            with self.store._write_connection() as connection:
                replay = self._receipt(connection, operation_id, request_hash)
                if replay is not None:
                    return replay
                appeal = connection.execute(select(appeals).where(appeals.c.id == appeal_id)).mappings().one_or_none()
                if appeal is None:
                    raise WorkflowNotFound("Appeal was not found.")
                evaluation_id = appeal["evaluation_id"]
                original = self._evaluation(connection, evaluation_id, lock=True)
                replay = self._receipt(connection, operation_id, request_hash)
                if replay is not None:
                    return replay
                snapshot = self._snapshot(connection, evaluation_id)
                case = self._case(connection, evaluation_id, original)
                if case["revision"] != expected_revision:
                    raise WorkflowConflict("Review revision is no longer current.")
                if any(row["appeal_id"] == appeal_id for row in case["decisions"]):
                    raise WorkflowConflict("Appeal already has a decision.")
                response = deepcopy(original["original"])
                response["criteria"] = deepcopy(case["final"]["criteria"])
                if override is not None:
                    if override.get("criterion_id") != appeal["criterion_id"] or override.get("status") != "scored":
                        raise WorkflowInvalid("The override must score the appealed criterion.")
                    response["criteria"] = [deepcopy(override) if row["criterion_id"] == appeal["criterion_id"] else row
                                            for row in response["criteria"]]
                try:
                    self.contracts.validate_result(snapshot, response)
                    scoring = score(snapshot, response)
                    self.contracts.validate("score-summary", scoring)
                except ContractError:
                    raise WorkflowInvalid("Decision does not satisfy the evaluation contract.") from None
                connection.execute(insert(review_decisions).values(
                    id=str(uuid4()), appeal_id=appeal_id, evaluation_id=evaluation_id,
                    decision=decision, reason=reason,
                    override_payload=_document(override) if override is not None else None,
                    reviewer_id=self.reviewer_id, created_at=_now(),
                ))
                self._update_case(connection, evaluation_id, expected_revision + 1, original,
                                  response=response, scoring=scoring)
                result = self._case(connection, evaluation_id, original)
                self._save_receipt(connection, operation_id, request_hash, result)
                return result
        except IntegrityError:
            raise WorkflowConflict("The decision conflicts with another operation.") from None
