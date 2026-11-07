"""Use the application's connection; credentials and logging stay outside migrations."""

from alembic import context

from app.models import metadata


config = context.config

if context.is_offline_mode():
    raise RuntimeError("These migrations require an application database connection.")

connection = config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Run migrations through EvaluationStore.initialize().")

context.configure(connection=connection, target_metadata=metadata, compare_type=True)
with context.begin_transaction():
    context.run_migrations()
