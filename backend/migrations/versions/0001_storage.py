"""Adopt legacy evaluations and add immutable inputs, conversations and versions."""

from alembic import op
import sqlalchemy as sa


revision = "0001_storage"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("evaluations"):
        column_info = inspector.get_columns("evaluations")
        columns = {column["name"] for column in column_info}
        primary_key = inspector.get_pk_constraint("evaluations")["constrained_columns"]
        if (columns != {"id", "created_at", "payload"} or primary_key != ["id"]
                or any(not isinstance(column["type"], sa.String) for column in column_info)):
            raise RuntimeError("Existing evaluations schema is incompatible with this migration.")
    else:
        op.create_table(
            "evaluations",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("payload", sa.Text(), nullable=False),
        )
    op.create_table(
        "evaluation_inputs",
        sa.Column("evaluation_id", sa.Text(), sa.ForeignKey("evaluations.id"), primary_key=True),
        sa.Column("request_payload", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("group_id", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("raw_payload", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
    )
    op.create_index("ix_conversations_group_id", "conversations", ["group_id"])
    op.create_table(
        "checklist_versions",
        sa.Column("checklist_id", sa.Text(), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    # Evaluations may predate Alembic. Never remove audit records implicitly.
    raise RuntimeError("This data-preserving initial migration does not support automatic downgrade.")
