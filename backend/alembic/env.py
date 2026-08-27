from logging.config import fileConfig
import sys
import asyncio

from sqlalchemy.ext.asyncio import create_async_engine, AsyncConnection
from sqlalchemy import pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Ensure backend/ is importable when running from other directories
if "." not in sys.path:
    sys.path.insert(0, ".")

# Import SQLAlchemy Base metadata and application settings
from app.database.models import Base
from app.config import settings

# Use the existing DATABASE_URL from app.config/settings
target_metadata = Base.metadata
DATABASE_URL = settings.DATABASE_URL


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an async Engine
    and associate a connection with the context.

    """
    def do_run_migrations(sync_conn) -> None:
        context.configure(
            connection=sync_conn,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()

    async def run() -> None:
        connectable = create_async_engine(
            DATABASE_URL,
            poolclass=pool.NullPool,
        )
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
        await connectable.dispose()

    asyncio.run(run())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
