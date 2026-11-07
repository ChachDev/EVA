"""Immutable audio assets and durable transcription leases."""

from alembic import op
import sqlalchemy as sa


revision = "0003_media"
down_revision = "0002_workflows"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "media_assets",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("operation_id", sa.Text(), nullable=False, unique=True),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False, unique=True),
    )
    op.create_table(
        "transcription_jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("asset_id", sa.Text(), sa.ForeignKey("media_assets.id"), nullable=False),
        sa.Column("operation_id", sa.Text(), nullable=False, unique=True),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("lease_token", sa.Text()),
        sa.Column("lease_expires_at", sa.Text()),
        sa.Column("result_payload", sa.Text()),
        sa.Column("conversation_id", sa.Text(), sa.ForeignKey("conversations.id")),
        sa.Column("error_code", sa.Text()),
    )
    op.create_index("ix_transcription_jobs_status_created", "transcription_jobs", ["status", "created_at"])
    op.create_table(
        "transcription_reviews",
        sa.Column("operation_id", sa.Text(), primary_key=True),
        sa.Column("job_id", sa.Text(), sa.ForeignKey("transcription_jobs.id"), nullable=False, unique=True),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("roles_payload", sa.Text(), nullable=False),
        sa.Column("reviewer_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )


def downgrade():
    raise RuntimeError("Media history cannot be removed by an automatic downgrade.")
