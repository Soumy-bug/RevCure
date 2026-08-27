import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.main import app
from app.database.models import Base
from app.database.connection import get_db

# In-memory test engine for isolated unit testing
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

@pytest_asyncio.fixture
async def test_session():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()

@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

@pytest.mark.asyncio
async def test_create_and_list_events(test_session: AsyncSession):
    # Override get_db dependency
    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 1. Create an event
            payload = {
                "payment_id": "pay_test_001",
                "event_type": "PAYMENT_FAILED",
                "event_payload": {"amount": 2500, "currency": "INR", "reason": "insufficient_funds"},
            }
            create_res = await ac.post("/api/v1/events", json=payload)
            assert create_res.status_code == 201
            data = create_res.json()
            assert data["id"] is not None
            assert data["payment_id"] == "pay_test_001"
            assert data["event_type"] == "PAYMENT_FAILED"
            assert data["event_payload"]["amount"] == 2500
            assert "created_at" in data

            # 2. List events
            list_res = await ac.get("/api/v1/events")
            assert list_res.status_code == 200
            events = list_res.json()
            assert len(events) >= 1
            assert events[0]["payment_id"] == "pay_test_001"

            # 3. Filter by payment_id
            filter_res = await ac.get("/api/v1/events?payment_id=pay_test_001")
            assert filter_res.status_code == 200
            assert len(filter_res.json()) == 1

            non_existent = await ac.get("/api/v1/events?payment_id=non_existent")
            assert non_existent.status_code == 200
            assert len(non_existent.json()) == 0
    finally:
        app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_db_test_diagnostic_endpoint(test_session: AsyncSession, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ENABLE_DB_TEST", True)

    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/api/v1/db-test")
            assert res.status_code == 200
            body = res.json()
            assert body["status"] == "connected"
            assert body["diagnostic_only"] is True
    finally:
        app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_lifespan_db_failure_raises_runtime_error(monkeypatch):
    from app.main import lifespan

    class FailingConn:
        async def execute(self, *args, **kwargs):
            raise ConnectionRefusedError("Simulated DB connection failure")

    class FailingBeginCtx:
        async def __aenter__(self):
            return FailingConn()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FailingEngine:
        def begin(self):
            return FailingBeginCtx()

    monkeypatch.setattr("app.main.engine", FailingEngine())

    with pytest.raises(RuntimeError) as exc_info:
        async with lifespan(app):
            pass

    assert "Failed to connect to database at startup" in str(exc_info.value)
