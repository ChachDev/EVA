"""Internal SQL tables; HTTP JSON Schemas remain the canonical public format."""

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, MetaData, Table, Text


metadata = MetaData()

# Keep these columns compatible with pre-migration SQLite databases.
evaluations = Table(
    "evaluations", metadata,
    Column("id", Text, primary_key=True),
    Column("created_at", Text, nullable=False),
    Column("payload", Text, nullable=False),
    Column("conversation_id", Text),
    Column("checklist_id", Text),
    Column("checklist_version", Integer),
)
Index("ix_evaluations_history", evaluations.c.created_at, evaluations.c.id)
Index("ix_evaluations_conversation_history", evaluations.c.conversation_id, evaluations.c.created_at, evaluations.c.id)

evaluation_inputs = Table(
    "evaluation_inputs", metadata,
    Column("evaluation_id", Text, ForeignKey("evaluations.id"), primary_key=True),
    Column("request_payload", Text, nullable=False),
    Column("content_hash", Text, nullable=False),
)

# Optional companion data. No change to legacy evaluation JSON or migration head.
audit_runs = Table(
    "eva_audit_runs", metadata,
    Column("evaluation_id", Text, ForeignKey("evaluations.id"), primary_key=True),
    Column("payload", Text, nullable=False),
)

conversations = Table(
    "conversations", metadata,
    Column("id", Text, primary_key=True),
    Column("group_id", Text, nullable=False, index=True),
    Column("payload", Text, nullable=False),
    Column("raw_payload", Text),
    Column("content_hash", Text, nullable=False),
)

checklist_versions = Table(
    "checklist_versions", metadata,
    Column("checklist_id", Text, primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("payload", Text, nullable=False),
    Column("content_hash", Text, nullable=False),
)

review_cases = Table(
    "review_cases", metadata,
    Column("evaluation_id", Text, ForeignKey("evaluations.id"), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("final_response", Text, nullable=False),
    Column("final_scoring", Text, nullable=False),
    CheckConstraint("revision >= 0", name="ck_review_cases_revision"),
)

appeals = Table(
    "appeals", metadata,
    Column("id", Text, primary_key=True),
    Column("evaluation_id", Text, ForeignKey("evaluations.id"), nullable=False, index=True),
    Column("criterion_id", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("message_ids", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)

review_decisions = Table(
    "review_decisions", metadata,
    Column("id", Text, primary_key=True),
    Column("appeal_id", Text, ForeignKey("appeals.id"), nullable=False, unique=True),
    Column("evaluation_id", Text, ForeignKey("evaluations.id"), nullable=False, index=True),
    Column("decision", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("override_payload", Text),
    Column("reviewer_id", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    CheckConstraint("decision IN ('accepted', 'rejected')", name="ck_review_decisions_decision"),
)

operation_receipts = Table(
    "operation_receipts", metadata,
    Column("operation_id", Text, primary_key=True),
    Column("request_hash", Text, nullable=False),
    Column("response_payload", Text, nullable=False),
)
