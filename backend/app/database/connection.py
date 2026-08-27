from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

try:
    from app.config import settings
except ImportError:
    from config import settings

def _normalize_async_url(url: str) -> str:
    """Ensure standard PostgreSQL connection URLs use asyncpg dialect."""
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url

DATABASE_URL = _normalize_async_url(settings.DATABASE_URL)

# Async SQLAlchemy Engine
engine = create_async_engine(
    DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
)

# Async Session Factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI async dependency that yields an AsyncSession per request.
    Automatically handles session cleanup.
    """
    async with AsyncSessionLocal() as session:
        yield session
