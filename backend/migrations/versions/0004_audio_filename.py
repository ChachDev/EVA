"""Keep a display name for new uploads; existing recordings remain readable."""
from alembic import op
import sqlalchemy as sa

revision = "0004_audio_filename"
down_revision = "0003_media"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("media_assets", sa.Column("display_name", sa.Text(), nullable=True))
    op.add_column("checklist_versions", sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("checklist_versions", "archived")
    op.drop_column("media_assets", "display_name")
