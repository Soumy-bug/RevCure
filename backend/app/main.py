import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import text

from app.config import settings
from app.api.routes import router
from app.database.connection import engine

# Configure logger
logger = logging.getLogger("revcure.startup")
logging.basicConfig(level=logging.INFO if not settings.DEBUG else logging.DEBUG)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI Lifespan handler:
    1. Runs DB readiness check using SELECT 1 on startup.
    2. Logs a clear error and fails startup if DB is unavailable.
    3. Disposes the connection engine on shutdown.
    """
    logger.info("Initializing RevCure Backend - Checking PostgreSQL readiness...")
    try:
        async with engine.begin() as conn:
            # 1. Execute DB readiness check
            await conn.execute(text("SELECT 1"))
            logger.info("Database readiness check succeeded.")
    except Exception as exc:
        logger.error(
            f"CRITICAL: Database readiness check failed during startup: {exc}",
            exc_info=True,
        )
        # Fail startup explicitly
        raise RuntimeError(
            f"Failed to connect to database at startup. Check DATABASE_URL and ensure PostgreSQL is running. Details: {exc}"
        ) from exc

    yield

    # Clean shutdown
    logger.info("Shutting down RevCure Backend - Disposing database engine...")
    await engine.dispose()
    logger.info("Database connections cleanly closed.")

# Initialize FastAPI application with lifespan management
app = FastAPI(
    title=settings.PROJECT_NAME,
    debug=settings.DEBUG,
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

# Mount all API routes under /api/v1
app.include_router(router, prefix="/api/v1")
