"""Index evaluation history and add append-only review workflows."""

import json

from alembic import op
import sqlalchemy as sa


revision = "0002_workflows"
down_revision = "0001_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("conversation_id", sa.Text()),
        sa.Column("checklist_id", sa.Text()),
        sa.Column("checklist_version", sa.Integer()),
    ):
        op.add_column("evaluations", column)
    connection = op.get_bind()
    history = sa.table("evaluations", sa.column("id"), sa.column("payload"),
                       sa.column("conversation_id"), sa.column("checklist_id"), sa.column("checklist_version"))
    # Preserve legacy JSON exactly; only populate the new internal lookup columns.
    for row in connection.execute(sa.select(history.c.id, history.c.payload)).mappings().all():
        try:
            original = json.loads(row["payload"]).get("original", {})
            if not isinstance(original, dict):
                continue
        except (ValueError, TypeError, AttributeError):
            continue
        values = {
            key: original[key] for key in ("conversation_id", "checklist_id")
            if isinstance(original.get(key), str)
        }
        if type(original.get("checklist_version")) is int:
            values["checklist_version"] = original["checklist_version"]
        if values:
            connection.execute(sa.update(history).where(history.c.id == row["id"]).values(**values))
    op.create_index("ix_evaluations_history", "evaluations", ["created_at", "id"])
    op.create_index("ix_evaluations_conversation_history", "evaluations", ["conversation_id", "created_at", "id"])
    op.create_table(
        "review_cases",
        sa.Column("evaluation_id", sa.Text(), sa.ForeignKey("evaluations.id"), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("final_response", sa.Text(), nullable=False),
        sa.Column("final_scoring", sa.Text(), nullable=False),
        sa.CheckConstraint("revision >= 0", name="ck_review_cases_revision"),
    )
    op.create_table(
        "appeals",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("evaluation_id", sa.Text(), sa.ForeignKey("evaluations.id"), nullable=False),
        sa.Column("criterion_id", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("message_ids", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_appeals_evaluation_id", "appeals", ["evaluation_id"])
    op.create_table(
        "review_decisions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("appeal_id", sa.Text(), sa.ForeignKey("appeals.id"), nullable=False, unique=True),
        sa.Column("evaluation_id", sa.Text(), sa.ForeignKey("evaluations.id"), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("override_payload", sa.Text()),
        sa.Column("reviewer_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint("decision IN ('accepted', 'rejected')", name="ck_review_decisions_decision"),
    )
    op.create_index("ix_review_decisions_evaluation_id", "review_decisions", ["evaluation_id"])
    op.create_table(
        "operation_receipts",
        sa.Column("operation_id", sa.Text(), primary_key=True),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("response_payload", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    raise RuntimeError("Review history requires an explicit data-preserving migration.")
