"""Internal SQL tables; HTTP JSON Schemas remain the canonical public format."""

from sqlalchemy import Column, ForeignKey, Integer, MetaData, Table, Text


metadata = MetaData()

# Keep these columns compatible with pre-migration SQLite databases.
evaluations = Table(
    "evaluations", metadata,
    Column("id", Text, primary_key=True),
    Column("created_at", Text, nullable=False),
    Column("payload", Text, nullable=False),
)

evaluation_inputs = Table(
    "evaluation_inputs", metadata,
    Column("evaluation_id", Text, ForeignKey("evaluations.id"), primary_key=True),
    Column("request_payload", Text, nullable=False),
    Column("content_hash", Text, nullable=False),
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
