"""Transactional persistence; public contract documents stay unchanged."""

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.engine import Connection, URL, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from .models import audit_runs, checklist_versions, conversations as conversation_rows, evaluation_inputs, evaluations


BACKEND_ROOT = Path(__file__).resolve().parents[1]


class StorageConflict(ValueError):
    """An immutable identity already belongs to different content."""


def _document(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _database_url(database: str | Path) -> URL:
    if isinstance(database, Path) or "://" not in database:
        return URL.create("sqlite+pysqlite", database=str(Path(database).resolve()))
    url = make_url(database)
    if url.drivername in {"postgres", "postgresql"}:
        return url.set(drivername="postgresql+psycopg")
    if url.drivername not in {"postgresql+psycopg", "sqlite", "sqlite+pysqlite"}:
        raise ValueError("Use a PostgreSQL psycopg URL or an explicit local SQLite database.")
    return url


class EvaluationStore:
    def __init__(self, database: str | Path):
        url = _database_url(database)
        self.path = (
            Path(url.database).resolve()
            if url.get_backend_name() == "sqlite" and url.database not in {None, "", ":memory:"}
            else None
        )
        engine_options = {"echo": False, "hide_parameters": True, "pool_pre_ping": True}
        if url.get_backend_name() == "sqlite":
            engine_options["connect_args"] = {"check_same_thread": False, "timeout": 5}
            if self.path is None:
                engine_options["poolclass"] = StaticPool
        else:
            engine_options.update(pool_timeout=5, pool_size=5, max_overflow=5)
            engine_options["connect_args"] = {
                "connect_timeout": 5,
                "options": "-c statement_timeout=15000 -c lock_timeout=5000",
            }
        self.engine = create_engine(url, **engine_options)
        if self.engine.dialect.name == "sqlite":
            # Explicit BEGIN makes DDL and DML transactional on supported Python versions.
            @event.listens_for(self.engine, "connect")
            def configure_sqlite(dbapi_connection, _connection_record):
                dbapi_connection.isolation_level = None
                cursor = dbapi_connection.cursor()
                try:
                    cursor.execute("PRAGMA foreign_keys=ON")
                finally:
                    cursor.close()

            @event.listens_for(self.engine, "begin")
            def begin_sqlite(connection):
                connection.exec_driver_sql(
                    "BEGIN IMMEDIATE" if connection.get_execution_options().get("eva_write") else "BEGIN"
                )

    @contextmanager
    def _write_connection(self) -> Iterator[Connection]:
        with self.engine.connect() as connection:
            if self.engine.dialect.name == "sqlite":
                connection = connection.execution_options(eva_write=True)
            with connection.begin():
                yield connection

    def initialize(self) -> None:
        """Apply checked-in migrations with one migrator per database."""
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        config = Config(str(BACKEND_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
        with self._write_connection() as connection:
            if self.engine.dialect.name == "postgresql":
                connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
                connection.exec_driver_sql("SELECT pg_advisory_xact_lock(630487201)")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    def close(self) -> None:
        self.engine.dispose()

    def list_conversations(self) -> list[dict]:
        with self.engine.connect() as connection:
            payloads = connection.execute(select(conversation_rows.c.payload).order_by(conversation_rows.c.id)).scalars().all()
        return [json.loads(payload) for payload in payloads]

    def get_conversation(self, conversation_id: str) -> dict | None:
        with self.engine.connect() as connection:
            payload = connection.execute(
                select(conversation_rows.c.payload).where(conversation_rows.c.id == conversation_id)
            ).scalar_one_or_none()
        return json.loads(payload) if payload is not None else None

    def list_checklists(self) -> list[dict]:
        with self.engine.connect() as connection:
            payloads = connection.execute(select(checklist_versions.c.payload).order_by(
                checklist_versions.c.checklist_id, checklist_versions.c.version,
            )).scalars().all()
        return [json.loads(payload) for payload in payloads]

    def get_checklist(self, checklist_id: str, version: int) -> dict | None:
        with self.engine.connect() as connection:
            payload = connection.execute(select(checklist_versions.c.payload).where(
                checklist_versions.c.checklist_id == checklist_id,
                checklist_versions.c.version == version,
            )).scalar_one_or_none()
        return json.loads(payload) if payload is not None else None

    def has_checklist(self, checklist_id: str) -> bool:
        with self.engine.connect() as connection:
            return bool(connection.execute(select(checklist_versions.c.checklist_id).where(
                checklist_versions.c.checklist_id == checklist_id,
            ).limit(1)).first())

    def seed_checklist(self, checklist: dict) -> None:
        payload = _document(checklist)
        content_hash = _digest(payload)
        try:
            with self._write_connection() as connection:
                existing = connection.execute(select(checklist_versions.c.content_hash).where(
                    checklist_versions.c.checklist_id == checklist["id"],
                    checklist_versions.c.version == checklist["version"],
                )).scalar_one_or_none()
                if existing is not None:
                    if existing != content_hash:
                        raise StorageConflict("The checklist version already contains different content.")
                    return
                connection.execute(insert(checklist_versions).values(
                    checklist_id=checklist["id"], version=checklist["version"],
                    payload=payload, content_hash=content_hash,
                ))
        except IntegrityError:
            raise StorageConflict("The checklist version conflicts with a concurrent write.") from None

    def import_conversations(self, group_id: str, conversations: list[dict], *,
                             raw_records: list[dict] | None = None) -> dict:
        if raw_records is not None and len(raw_records) != len(conversations):
            raise ValueError("Raw and normalized records must have the same length.")
        ids = [conversation["id"] for conversation in conversations]
        if len(ids) != len(set(ids)):
            raise StorageConflict("An import contains duplicate conversation identities.")
        # Prepare the entire batch before any database changes.
        prepared = []
        for index, conversation in enumerate(conversations):
            payload = _document(conversation)
            prepared.append({
                "id": conversation["id"], "group_id": group_id, "payload": payload,
                "content_hash": _digest(payload),
                "raw_payload": _document(raw_records[index]) if raw_records is not None else None,
            })
        inserted = unchanged = 0
        try:
            with self._write_connection() as connection:
                for item in prepared:
                    existing = connection.execute(select(
                        conversation_rows.c.group_id, conversation_rows.c.content_hash,
                        conversation_rows.c.raw_payload,
                    ).where(conversation_rows.c.id == item["id"])).mappings().one_or_none()
                    if existing is not None:
                        if existing["group_id"] != group_id or existing["content_hash"] != item["content_hash"]:
                            raise StorageConflict("The conversation identity belongs to different source content.")
                        if (item["raw_payload"] is not None and existing["raw_payload"] is not None
                                and existing["raw_payload"] != item["raw_payload"]):
                            raise StorageConflict("The conversation identity belongs to different source content.")
                        unchanged += 1
                    else:
                        connection.execute(insert(conversation_rows), item)
                        inserted += 1
        except IntegrityError:
            # The failed transaction has rolled back. A competing identical import
            # may now own every identity; accept only the complete committed batch.
            with self.engine.connect() as connection:
                for item in prepared:
                    existing = connection.execute(select(
                        conversation_rows.c.group_id, conversation_rows.c.content_hash,
                        conversation_rows.c.raw_payload,
                    ).where(conversation_rows.c.id == item["id"])).mappings().one_or_none()
                    if (existing is None or existing["group_id"] != item["group_id"]
                            or existing["content_hash"] != item["content_hash"]
                            or existing["raw_payload"] != item["raw_payload"]):
                        raise StorageConflict("The import conflicts with a concurrent write.") from None
            return {"group_id": group_id, "total": len(prepared), "inserted": 0, "unchanged": len(prepared)}
        return {"group_id": group_id, "total": len(prepared), "inserted": inserted, "unchanged": unchanged}

    def initialize_audit(self):
        audit_runs.create(self.engine, checkfirst=True)

    def save_shadow(self, evaluation_id: str, payload: dict):
        with self._write_connection() as connection:
            connection.execute(audit_runs.update().where(audit_runs.c.evaluation_id == evaluation_id)
                               .values(payload=_document(payload)))

    def get_audit(self, evaluation_id: str) -> dict | None:
        from sqlalchemy import inspect
        if not inspect(self.engine).has_table(audit_runs.name):
            return None
        with self.engine.connect() as connection:
            body = connection.execute(select(audit_runs.c.payload).where(
                audit_runs.c.evaluation_id == evaluation_id)).scalar_one_or_none()
        return json.loads(body) if body else None

    def create(self, record: dict, request_snapshot: dict | None = None, audit: dict | None = None) -> None:
        payload = _document(record)
        snapshot = _document(request_snapshot) if request_snapshot is not None else None
        original = record.get("original", {})
        try:
            with self._write_connection() as connection:
                connection.execute(insert(evaluations).values(
                    id=record["id"], created_at=record["created_at"], payload=payload,
                    conversation_id=original.get("conversation_id"),
                    checklist_id=original.get("checklist_id"),
                    checklist_version=original.get("checklist_version"),
                ))
                if audit is not None:
                    connection.execute(insert(audit_runs).values(evaluation_id=record["id"],payload=_document(audit)))
                if snapshot is not None:
                    connection.execute(insert(evaluation_inputs).values(
                        evaluation_id=record["id"], request_payload=snapshot, content_hash=_digest(snapshot),
                    ))
        except IntegrityError:
            raise StorageConflict("The evaluation identity already exists.") from None

    def get(self, evaluation_id: str) -> dict | None:
        with self.engine.connect() as connection:
            payload = connection.execute(
                select(evaluations.c.payload).where(evaluations.c.id == evaluation_id)
            ).scalar_one_or_none()
        return json.loads(payload) if payload is not None else None

    def get_snapshot(self, evaluation_id: str) -> dict | None:
        with self.engine.connect() as connection:
            payload = connection.execute(select(evaluation_inputs.c.request_payload).where(
                evaluation_inputs.c.evaluation_id == evaluation_id,
            )).scalar_one_or_none()
        return json.loads(payload) if payload is not None else None
