from alembic import context
from app.config import settings
from app.database import engine
from app.models import Base

config = context.config
target_metadata = Base.metadata
if context.is_offline_mode():
    context.configure(
        url=settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()
