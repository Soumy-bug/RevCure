"""
Tests for the recovery workflow, Razorpay integration, configuration,
and error-handling hardening.

All tests use in-memory SQLite and mocked external calls — no real
Razorpay credentials or network access required.
"""

import pytest
import pytest_asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.main import app
from app.database.models import Base, Event, RiskAssessment, RecoveryAttempt
from app.database.connection import get_db
from app.database import crud
from app.models.schemas import EventCreate
from app.services.recovery import decide_action, MAX_RETRY_ATTEMPTS, MAX_TOTAL_ACTIONS
from app.services.razorpay_client import (
    create_retry_order,
    RazorpayOrderResult,
    _sanitize_razorpay_error,
)
from app.config import Settings

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def test_session():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session
    await engine.dispose()


def _make_event_dicts(types_and_payloads):
    """Helper: build event_dicts list from [(type, payload), ...]"""
    return [{"event_type": t, "event_payload": p or {}, "created_at": None} for t, p in types_and_payloads]


# ══════════════════════════════════════════════════════════════════════════════
# 1. Configuration validation
# ══════════════════════════════════════════════════════════════════════════════

class TestConfigValidation:
    def test_razorpay_configured_false_when_empty(self):
        s = Settings()
        s.RAZORPAY_KEY_ID = ""
        s.RAZORPAY_KEY_SECRET = ""
        assert s.razorpay_configured is False

    def test_razorpay_configured_false_when_placeholder_key(self):
        s = Settings()
        s.RAZORPAY_KEY_ID = "rzp_test_placeholder_key"
        s.RAZORPAY_KEY_SECRET = "placeholder_secret_key"
        assert s.razorpay_configured is False

    def test_razorpay_configured_true_with_valid_test_keys(self):
        s = Settings()
        s.RAZORPAY_KEY_ID = "rzp_test_abc123xyz"
        s.RAZORPAY_KEY_SECRET = "abcdefghij1234567890abcdefghij12345"
        assert s.razorpay_configured is True


# ══════════════════════════════════════════════════════════════════════════════
# 2. Razorpay client — missing credentials
# ══════════════════════════════════════════════════════════════════════════════

class TestRazorpayClientMissingCredentials:
    @pytest.mark.asyncio
    async def test_returns_error_when_not_configured(self):
        with patch("app.services.razorpay_client.settings") as mock_settings:
            mock_settings.razorpay_configured = False
            result = await create_retry_order("pay_test_001", 5000)
            assert result.success is False
            assert "not configured" in result.error.lower()

    @pytest.mark.asyncio
    async def test_returns_error_when_key_id_empty(self):
        with patch("app.services.razorpay_client.settings") as mock_settings:
            mock_settings.razorpay_configured = False
            result = await create_retry_order("pay_test_001", 5000)
            assert result.success is False
            assert result.order_id is None


# ══════════════════════════════════════════════════════════════════════════════
# 3. Razorpay client — mocked success
# ══════════════════════════════════════════════════════════════════════════════

class TestRazorpayClientSuccess:
    @pytest.mark.asyncio
    async def test_creates_order_successfully(self):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "id": "order_test_abc",
            "amount": 5000,
            "currency": "INR",
            "status": "created",
        }

        with patch("app.services.razorpay_client.settings") as mock_settings, \
             patch("app.services.razorpay_client.httpx.AsyncClient") as mock_client_cls:
            mock_settings.razorpay_configured = True
            mock_settings.RAZORPAY_KEY_ID = "rzp_test_real"
            mock_settings.RAZORPAY_KEY_SECRET = "real_secret"

            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await create_retry_order("pay_test_001", 5000)

            assert result.success is True
            assert result.order_id == "order_test_abc"
            assert result.amount == 5000
            assert result.currency == "INR"


# ══════════════════════════════════════════════════════════════════════════════
# 4. Razorpay client — API error (non-2xx)
# ══════════════════════════════════════════════════════════════════════════════

class TestRazorpayClientApiError:
    @pytest.mark.asyncio
    async def test_returns_sanitized_error_on_401(self):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {
            "error": {
                "code": "AUTHENTICATION_ERROR",
                "description": "The API key provided is invalid. key_id: rzp_test_xxx",
            }
        }

        with patch("app.services.razorpay_client.settings") as mock_settings, \
             patch("app.services.razorpay_client.httpx.AsyncClient") as mock_client_cls:
            mock_settings.razorpay_configured = True
            mock_settings.RAZORPAY_KEY_ID = "rzp_test_bad"
            mock_settings.RAZORPAY_KEY_SECRET = "bad_secret"

            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await create_retry_order("pay_test_001", 5000)

            assert result.success is False
            assert "authentication" in result.error.lower()
            # Verify the raw description is NOT leaked
            assert "rzp_test_xxx" not in result.error

    @pytest.mark.asyncio
    async def test_returns_sanitized_error_on_400(self):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "error": {
                "code": "BAD_REQUEST_ERROR",
                "description": "The amount must be at least 100",
            }
        }

        with patch("app.services.razorpay_client.settings") as mock_settings, \
             patch("app.services.razorpay_client.httpx.AsyncClient") as mock_client_cls:
            mock_settings.razorpay_configured = True
            mock_settings.RAZORPAY_KEY_ID = "rzp_test_real"
            mock_settings.RAZORPAY_KEY_SECRET = "real_secret"

            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await create_retry_order("pay_test_001", 5000)

            assert result.success is False
            assert "invalid request" in result.error.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 5. Razorpay client — network timeout
# ══════════════════════════════════════════════════════════════════════════════

class TestRazorpayClientTimeout:
    @pytest.mark.asyncio
    async def test_returns_error_on_timeout(self):
        import httpx as real_httpx

        with patch("app.services.razorpay_client.settings") as mock_settings, \
             patch("app.services.razorpay_client.httpx.AsyncClient") as mock_client_cls:
            mock_settings.razorpay_configured = True
            mock_settings.RAZORPAY_KEY_ID = "rzp_test_real"
            mock_settings.RAZORPAY_KEY_SECRET = "real_secret"

            mock_client = AsyncMock()
            mock_client.post.side_effect = real_httpx.TimeoutException("Connection timed out")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await create_retry_order("pay_test_001", 5000)

            assert result.success is False
            assert "timed out" in result.error.lower() or "timeout" in result.error.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 6. Razorpay client — network error
# ══════════════════════════════════════════════════════════════════════════════

class TestRazorpayClientNetworkError:
    @pytest.mark.asyncio
    async def test_returns_error_on_connection_failure(self):
        import httpx as real_httpx

        with patch("app.services.razorpay_client.settings") as mock_settings, \
             patch("app.services.razorpay_client.httpx.AsyncClient") as mock_client_cls:
            mock_settings.razorpay_configured = True
            mock_settings.RAZORPAY_KEY_ID = "rzp_test_real"
            mock_settings.RAZORPAY_KEY_SECRET = "real_secret"

            mock_client = AsyncMock()
            mock_client.post.side_effect = real_httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await create_retry_order("pay_test_001", 5000)

            assert result.success is False
            assert "unreachable" in result.error.lower() or "connection" in result.error.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 7. Error sanitization
# ══════════════════════════════════════════════════════════════════════════════

class TestErrorSanitization:
    def test_known_error_codes(self):
        body = {"error": {"code": "AUTHENTICATION_ERROR", "description": "secret details"}}
        result = _sanitize_razorpay_error(body, 401)
        assert "authentication" in result.lower()
        assert "secret details" not in result

    def test_unknown_error_code_uses_http_status(self):
        body = {"error": {"code": "UNKNOWN_CODE", "description": "some detail"}}
        result = _sanitize_razorpay_error(body, 503)
        assert "503" in result or "error" in result.lower()

    def test_malformed_body_returns_status_code(self):
        result = _sanitize_razorpay_error("not a dict", 502)
        assert "502" in result


# ══════════════════════════════════════════════════════════════════════════════
# 8. Recovery decision — retry limit enforcement
# ══════════════════════════════════════════════════════════════════════════════

class TestRecoveryRetryLimits:
    def test_can_retry_within_limit(self):
        events = _make_event_dicts([("PAYMENT_FAILED", {"amount": 5000})])
        decision = decide_action(events, 0.4, "medium", previous_attempts=0)
        assert decision.action == "retry_payment"
        assert decision.bounded is False

    def test_escalate_after_max_retries(self):
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 5000}),
            ("RETRY_PAYMENT", {"attempt_number": 1}),
            ("PAYMENT_FAILED", {"amount": 5000}),
            ("RETRY_PAYMENT", {"attempt_number": 2}),
            ("PAYMENT_FAILED", {"amount": 5000}),
            ("RETRY_PAYMENT", {"attempt_number": 3}),
        ])
        decision = decide_action(events, 0.7, "high", previous_attempts=3)
        assert decision.action == "escalate"

    def test_no_action_at_hard_limit(self):
        events = _make_event_dicts([("PAYMENT_FAILED", {"amount": 5000})])
        decision = decide_action(events, 0.8, "critical", previous_attempts=MAX_TOTAL_ACTIONS)
        assert decision.action == "no_action"
        assert decision.bounded is True


# ══════════════════════════════════════════════════════════════════════════════
# 9. Recovery decision — total action hard limit
# ══════════════════════════════════════════════════════════════════════════════

class TestRecoveryHardLimit:
    def test_hard_limit_enforced_regardless_of_risk(self):
        for label in ("low", "medium", "high", "critical"):
            events = _make_event_dicts([("PAYMENT_FAILED", {"amount": 5000})])
            decision = decide_action(events, 0.9, label, previous_attempts=MAX_TOTAL_ACTIONS)
            assert decision.action == "no_action"
            assert decision.bounded is True

    def test_attempt_number_matches_next_when_limited(self):
        events = _make_event_dicts([("PAYMENT_FAILED", {"amount": 5000})])
        decision = decide_action(events, 0.8, "critical", previous_attempts=5)
        assert decision.attempt_number == 6


# ══════════════════════════════════════════════════════════════════════════════
# 10. Recovery decision — no duplicate escalation at limit
# ══════════════════════════════════════════════════════════════════════════════

class TestNoDuplicateEscalation:
    def test_critical_at_limit_returns_no_action_not_escalate(self):
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 99999}),
            ("PAYMENT_FAILED", {"amount": 99999}),
        ])
        decision = decide_action(events, 0.85, "critical", previous_attempts=5)
        assert decision.action == "no_action"
        assert decision.bounded is True
        # Must NOT be escalate — that would bypass the limit
        assert decision.action != "escalate"

    def test_dispute_at_limit_returns_no_action(self):
        events = _make_event_dicts([
            ("DISPUTE_OPENED", {"dispute_id": "dsp_001"}),
        ])
        decision = decide_action(events, 0.65, "high", previous_attempts=5)
        assert decision.action == "no_action"
        assert decision.bounded is True


# ══════════════════════════════════════════════════════════════════════════════
# 11. Recovery decision — deterministic behavior
# ══════════════════════════════════════════════════════════════════════════════

class TestRecoveryDeterminism:
    def test_healthy_payment_returns_no_action(self):
        events = _make_event_dicts([
            ("PAYMENT_SUCCESS", {"amount": 4999}),
        ])
        decision = decide_action(events, 0.0, "none", previous_attempts=0)
        assert decision.action == "no_action"

    def test_dispute_always_escalates(self):
        events = _make_event_dicts([
            ("DISPUTE_OPENED", {"dispute_id": "dsp_001"}),
        ])
        decision = decide_action(events, 0.65, "high", previous_attempts=0)
        assert decision.action == "escalate"

    def test_refund_pending_sends_reminder(self):
        events = _make_event_dicts([
            ("REFUND_PENDING", {"amount": 3499}),
        ])
        decision = decide_action(events, 0.25, "low", previous_attempts=0)
        assert decision.action == "send_reminder"

    def test_critical_risk_escalates(self):
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 99999}),
        ])
        decision = decide_action(events, 0.85, "critical", previous_attempts=0)
        assert decision.action == "escalate"

    def test_retry_exhausted_escalates(self):
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 15000}),
            ("RETRY_FAILED", {"reason": "exhausted"}),
        ])
        decision = decide_action(events, 0.75, "high", previous_attempts=0)
        assert decision.action == "escalate"


# ══════════════════════════════════════════════════════════════════════════════
# 12. Recovery endpoint — integration with mocked Razorpay
# ══════════════════════════════════════════════════════════════════════════════

class TestRecoveryEndpoint:
    @pytest.mark.asyncio
    async def test_no_action_does_not_create_attempt(self, test_session: AsyncSession):
        """When the decision is no_action, no recovery_attempt row is created."""
        async def override_get_db():
            yield test_session
        app.dependency_overrides[get_db] = override_get_db

        try:
            # Seed a healthy payment with PAYMENT_SUCCESS
            await crud.create_event(test_session, EventCreate(
                payment_id="pay_healthy_001",
                event_type="PAYMENT_SUCCESS",
                event_payload={"amount": 4999, "status": "captured"},
            ))
            await crud.upsert_risk_assessment(
                test_session, "pay_healthy_001", 0.0, "none", [],
            )

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                res = await ac.post("/api/v1/payments/pay_healthy_001/recover")

            assert res.status_code == 200
            body = res.json()
            assert body["action"] == "no_action"
            assert body["status"] == "not_applicable"

            # Verify no recovery attempt was created
            count = await crud.count_recovery_attempts(test_session, "pay_healthy_001")
            assert count == 0
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_retry_with_configured_razorpay_creates_attempt(self, test_session: AsyncSession):
        """When Razorpay is configured and the order succeeds, a retry attempt is recorded."""
        async def override_get_db():
            yield test_session
        app.dependency_overrides[get_db] = override_get_db

        try:
            await crud.create_event(test_session, EventCreate(
                payment_id="pay_retry_001",
                event_type="PAYMENT_FAILED",
                event_payload={"amount": 12500, "status": "failed", "error_code": "gateway_timeout"},
            ))
            await crud.upsert_risk_assessment(
                test_session, "pay_retry_001", 0.4, "medium",
                ["Payment failed 1 time(s)"],
            )

            mock_result = RazorpayOrderResult(
                success=True, order_id="order_test_123", amount=12500, currency="INR",
            )
            with patch("app.api.routes.create_retry_order", new_callable=AsyncMock, return_value=mock_result):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    res = await ac.post("/api/v1/payments/pay_retry_001/recover")

            assert res.status_code == 200
            body = res.json()
            assert body["action"] == "retry_payment"
            assert body["status"] == "executed"

            count = await crud.count_recovery_attempts(test_session, "pay_retry_001")
            assert count == 1
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_retry_with_razorpay_failure_records_failed_attempt(self, test_session: AsyncSession):
        """When Razorpay call fails, the attempt is recorded as failed."""
        async def override_get_db():
            yield test_session
        app.dependency_overrides[get_db] = override_get_db

        try:
            await crud.create_event(test_session, EventCreate(
                payment_id="pay_retry_fail_001",
                event_type="PAYMENT_FAILED",
                event_payload={"amount": 8000, "status": "failed"},
            ))
            await crud.upsert_risk_assessment(
                test_session, "pay_retry_fail_001", 0.4, "medium",
                ["Payment failed 1 time(s)"],
            )

            mock_result = RazorpayOrderResult(
                success=False, error="Payment gateway authentication failed",
            )
            with patch("app.api.routes.create_retry_order", new_callable=AsyncMock, return_value=mock_result):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    res = await ac.post("/api/v1/payments/pay_retry_fail_001/recover")

            assert res.status_code == 200
            body = res.json()
            assert body["action"] == "retry_payment"
            assert body["status"] == "failed"
            assert "authentication failed" in body["reason"].lower()
            # Must not leak raw error details
            assert "rzp_test" not in body["reason"]
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_escalate_does_not_call_razorpay(self, test_session: AsyncSession):
        """Escalation actions must not invoke the Razorpay API."""
        async def override_get_db():
            yield test_session
        app.dependency_overrides[get_db] = override_get_db

        try:
            await crud.create_event(test_session, EventCreate(
                payment_id="pay_esc_001",
                event_type="PAYMENT_FAILED",
                event_payload={"amount": 99999, "status": "failed"},
            ))
            await crud.upsert_risk_assessment(
                test_session, "pay_esc_001", 0.85, "critical",
                ["Critical risk"],
            )

            with patch("app.api.routes.create_retry_order", new_callable=AsyncMock) as mock_razorpay:
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    res = await ac.post("/api/v1/payments/pay_esc_001/recover")

            assert res.status_code == 200
            body = res.json()
            assert body["action"] == "escalate"
            mock_razorpay.assert_not_called()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_hard_limit_returns_no_action_without_persisting(self, test_session: AsyncSession):
        """At the hard limit, recovery returns no_action and creates no new attempt."""
        async def override_get_db():
            yield test_session
        app.dependency_overrides[get_db] = override_get_db

        try:
            await crud.create_event(test_session, EventCreate(
                payment_id="pay_limit_001",
                event_type="PAYMENT_FAILED",
                event_payload={"amount": 5000, "status": "failed"},
            ))
            await crud.upsert_risk_assessment(
                test_session, "pay_limit_001", 0.8, "critical",
                ["Critical risk"],
            )

            # Manually create MAX_TOTAL_ACTIONS attempts
            for i in range(MAX_TOTAL_ACTIONS):
                await crud.create_recovery_attempt(
                    test_session, "pay_limit_001", "escalate", "executed",
                    f"attempt {i+1}", attempt_number=i+1,
                )

            count_before = await crud.count_recovery_attempts(test_session, "pay_limit_001")
            assert count_before == MAX_TOTAL_ACTIONS

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                res = await ac.post("/api/v1/payments/pay_limit_001/recover")

            assert res.status_code == 200
            body = res.json()
            assert body["action"] == "no_action"

            count_after = await crud.count_recovery_attempts(test_session, "pay_limit_001")
            assert count_after == MAX_TOTAL_ACTIONS  # Unchanged
        finally:
            app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════════════════════════
# 13. Recovery decision — all demo scenarios remain valid
# ══════════════════════════════════════════════════════════════════════════════

class TestDemoScenarioDecisions:
    """Verify the decision engine produces the expected actions for the
    8 seeded demo scenarios — ensuring demo data stays correct."""

    def test_success_scenario(self):
        events = _make_event_dicts([
            ("PAYMENT_AUTHORIZED", {"amount": 4999}),
            ("PAYMENT_SUCCESS", {"amount": 4999}),
            ("SETTLEMENT_PROCESSED", {"amount": 4999}),
        ])
        d = decide_action(events, 0.0, "none", 0)
        assert d.action == "no_action"

    def test_retry_scenario(self):
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 12500}),
        ])
        d = decide_action(events, 0.4, "medium", 1)  # 1 existing attempt
        # Should escalate since previous_attempts=1 >= MAX_RETRY_ATTEMPTS(3)? No, 1 < 3.
        # But remaining_retries = 3 - 0 = 0 RETRY_PAYMENT events, and previous_attempts=1 < 3
        # Actually there are 0 RETRY_PAYMENT events, remaining_retries = 3
        # And previous_attempts=1 < 3, so should retry
        assert d.action == "retry_payment"

    def test_escalate_scenario(self):
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 25000}),
            ("RETRY_PAYMENT", {"attempt_number": 1}),
            ("PAYMENT_FAILED", {"amount": 25000}),
            ("RETRY_PAYMENT", {"attempt_number": 2}),
            ("PAYMENT_FAILED", {"amount": 25000}),
            ("RETRY_PAYMENT", {"attempt_number": 3}),
        ])
        d = decide_action(events, 0.7, "high", 3)
        # 3 retries done (remaining_retries=0), 3 total actions
        assert d.action == "escalate"

    def test_dispute_scenario(self):
        # Dispute/refund events are evaluated BEFORE payment success,
        # so a payment with both PAYMENT_SUCCESS and DISPUTE_OPENED correctly
        # returns escalate — the dispute needs human review regardless.
        events = _make_event_dicts([
            ("PAYMENT_SUCCESS", {"amount": 8999}),
            ("REFUND_PENDING", {"amount": 8999}),
            ("DISPUTE_OPENED", {"dispute_id": "dsp_001"}),
        ])
        d = decide_action(events, 0.65, "high", 1)
        assert d.action == "escalate"

    def test_dispute_without_success_escalates(self):
        # A dispute WITHOUT a preceding PAYMENT_SUCCESS escalates correctly
        events = _make_event_dicts([
            ("REFUND_PENDING", {"amount": 8999}),
            ("DISPUTE_OPENED", {"dispute_id": "dsp_001"}),
        ])
        d = decide_action(events, 0.65, "high", 0)
        assert d.action == "escalate"

    def test_critical_scenario(self):
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 99999}),
            ("PAYMENT_FAILED", {"amount": 99999}),
        ])
        d = decide_action(events, 0.85, "critical", 2)
        # 2 existing actions, critical → escalate
        assert d.action == "escalate"

    def test_lowrisk_scenario(self):
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 1999}),
        ])
        d = decide_action(events, 0.4, "medium", 1)
        # 1 existing attempt, 0 retries done → retry
        assert d.action == "retry_payment"

    def test_exhaust_scenario(self):
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 15000}),
            ("RETRY_FAILED", {"reason": "exhausted"}),
        ])
        d = decide_action(events, 0.75, "high", 1)
        assert d.action == "escalate"

    def test_refund_scenario(self):
        # Refund pending is evaluated before payment success, so a payment
        # with both PAYMENT_SUCCESS and REFUND_PENDING correctly returns
        # send_reminder — the refund needs customer communication.
        events = _make_event_dicts([
            ("PAYMENT_SUCCESS", {"amount": 3499}),
            ("REFUND_PENDING", {"amount": 3499}),
        ])
        d = decide_action(events, 0.25, "low", 1)
        assert d.action == "send_reminder"

    def test_refund_without_success_sends_reminder(self):
        # A refund without preceding PAYMENT_SUCCESS sends a reminder
        events = _make_event_dicts([
            ("REFUND_PENDING", {"amount": 3499}),
        ])
        d = decide_action(events, 0.25, "low", 0)
        assert d.action == "send_reminder"


# ══════════════════════════════════════════════════════════════════════════════
# 14. Recovery outcome tracking
# ══════════════════════════════════════════════════════════════════════════════

class TestRecoveryOutcomeTracking:
    @pytest.mark.asyncio
    async def test_retry_stores_amount_and_order_id(self, test_session: AsyncSession):
        """Retry with Razorpay success stores amount, order_id, and outcome=pending."""
        async def override_get_db():
            yield test_session
        app.dependency_overrides[get_db] = override_get_db

        try:
            await crud.create_event(test_session, EventCreate(
                payment_id="pay_outcome_001",
                event_type="PAYMENT_FAILED",
                event_payload={"amount": 15000, "status": "failed"},
            ))
            await crud.upsert_risk_assessment(
                test_session, "pay_outcome_001", 0.4, "medium",
                ["Payment failed 1 time(s)"],
            )

            mock_result = RazorpayOrderResult(
                success=True, order_id="order_test_outcome", amount=15000, currency="INR",
            )
            with patch("app.api.routes.create_retry_order", new_callable=AsyncMock, return_value=mock_result):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    res = await ac.post("/api/v1/payments/pay_outcome_001/recover")

            assert res.status_code == 200
            body = res.json()
            assert body["action"] == "retry_payment"
            assert body["outcome"] == "pending"
            assert body["amount"] == 15000
            assert body["razorpay_order_id"] == "order_test_outcome"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_escalate_does_not_set_order_id(self, test_session: AsyncSession):
        """Escalation attempts don't have order_id or amount."""
        async def override_get_db():
            yield test_session
        app.dependency_overrides[get_db] = override_get_db

        try:
            await crud.create_event(test_session, EventCreate(
                payment_id="pay_esc_outcome_001",
                event_type="PAYMENT_FAILED",
                event_payload={"amount": 99999, "status": "failed"},
            ))
            await crud.upsert_risk_assessment(
                test_session, "pay_esc_outcome_001", 0.85, "critical",
                ["Critical risk"],
            )

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                res = await ac.post("/api/v1/payments/pay_esc_outcome_001/recover")

            assert res.status_code == 200
            body = res.json()
            assert body["action"] == "escalate"
            assert body["razorpay_order_id"] is None
            assert body["outcome"] == "executed"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_mark_recovery_recovered_updates_fields(self, test_session: AsyncSession):
        """mark_recovery_recovered sets outcome=recovered and recovered_at."""
        attempt = await crud.create_recovery_attempt(
            test_session, "pay_recovered_001", "retry_payment", "executed",
            "Retry order created", attempt_number=1,
            amount=10000, razorpay_order_id="order_recovered_001", outcome="pending",
        )
        assert attempt.outcome == "pending"
        assert attempt.recovered_at is None

        await crud.mark_recovery_recovered(test_session, attempt.id, "pay_recovered_new_001")

        # Re-fetch to verify
        from sqlalchemy import select
        refreshed = await test_session.execute(
            select(RecoveryAttempt).where(RecoveryAttempt.id == attempt.id)
        )
        updated = refreshed.scalar_one()
        assert updated.outcome == "recovered"
        assert updated.recovered_at is not None
        assert "captured=pay_recovered_new_001" in updated.reason

    @pytest.mark.asyncio
    async def test_find_pending_retry_by_order_id(self, test_session: AsyncSession):
        """find_pending_retry_by_order_id returns the correct pending retry."""
        await crud.create_recovery_attempt(
            test_session, "pay_find_001", "retry_payment", "executed",
            "order_abc", attempt_number=1,
            amount=5000, razorpay_order_id="order_abc", outcome="pending",
        )
        await crud.create_recovery_attempt(
            test_session, "pay_find_001", "retry_payment", "executed",
            "order_def", attempt_number=2,
            amount=5000, razorpay_order_id="order_def", outcome="recovered",
        )

        found = await crud.find_pending_retry_by_order_id(test_session, "order_abc")
        assert found is not None
        assert found.razorpay_order_id == "order_abc"
        assert found.outcome == "pending"

        # Already recovered — should not be found
        not_found = await crud.find_pending_retry_by_order_id(test_session, "order_def")
        assert not_found is None

        # Non-existent order
        missing = await crud.find_pending_retry_by_order_id(test_session, "order_xyz")
        assert missing is None

    @pytest.mark.asyncio
    async def test_get_recovery_metrics(self, test_session: AsyncSession):
        """get_recovery_metrics returns correct aggregated values."""
        # Create retry attempts with different outcomes
        await crud.create_recovery_attempt(
            test_session, "pay_m1", "retry_payment", "executed",
            "r1", attempt_number=1, amount=10000, razorpay_order_id="o1", outcome="recovered",
        )
        await crud.create_recovery_attempt(
            test_session, "pay_m2", "retry_payment", "executed",
            "r2", attempt_number=1, amount=20000, razorpay_order_id="o2", outcome="pending",
        )
        await crud.create_recovery_attempt(
            test_session, "pay_m3", "retry_payment", "executed",
            "r3", attempt_number=1, amount=5000, razorpay_order_id="o3", outcome="failed",
        )
        # Non-retry attempt — should not count
        await crud.create_recovery_attempt(
            test_session, "pay_m4", "escalate", "executed",
            "escalated", attempt_number=1,
        )

        metrics = await crud.get_recovery_metrics(test_session)
        assert metrics["eligible_payments"] == 3  # pay_m1, pay_m2, pay_m3
        assert metrics["recovered_payments"] == 1  # pay_m1
        assert metrics["money_at_risk"] == 35000  # 10000 + 20000 + 5000
        assert metrics["money_recovered"] == 10000  # only pay_m1
        assert metrics["recovery_rate"] == pytest.approx(10000 / 35000, abs=0.01)

    @pytest.mark.asyncio
    async def test_money_at_risk_counts_unique_payments_not_retries(self, test_session: AsyncSession):
        """One payment retried 3 times contributes its amount once, not 3x."""
        # Three retries of the same payment (same payment_id, different attempt_numbers)
        await crud.create_recovery_attempt(
            test_session, "pay_multi_retry", "retry_payment", "executed",
            "r1", attempt_number=1, amount=25000, razorpay_order_id="o1", outcome="pending",
        )
        await crud.create_recovery_attempt(
            test_session, "pay_multi_retry", "retry_payment", "executed",
            "r2", attempt_number=2, amount=25000, razorpay_order_id="o2", outcome="failed",
        )
        await crud.create_recovery_attempt(
            test_session, "pay_multi_retry", "retry_payment", "executed",
            "r3", attempt_number=3, amount=25000, razorpay_order_id="o3", outcome="recovered",
        )
        # A second, different payment
        await crud.create_recovery_attempt(
            test_session, "pay_other", "retry_payment", "executed",
            "r4", attempt_number=1, amount=10000, razorpay_order_id="o4", outcome="pending",
        )

        metrics = await crud.get_recovery_metrics(test_session)
        # money_at_risk: 25000 (pay_multi_retry, counted once) + 10000 (pay_other) = 35000
        assert metrics["money_at_risk"] == 35000
        # eligible_payments: 2 distinct payment_ids
        assert metrics["eligible_payments"] == 2
        # money_recovered: only pay_multi_retry has outcome=recovered
        assert metrics["money_recovered"] == 25000


# ══════════════════════════════════════════════════════════════════════════════
# 15. Webhook marks recovery as recovered
# ══════════════════════════════════════════════════════════════════════════════

class TestWebhookRecoveryMarking:
    @pytest.mark.asyncio
    async def test_payment_captured_marks_retry_recovered(self, test_session: AsyncSession):
        """When a payment.captured webhook arrives with matching order_id, recovery is marked recovered."""
        async def override_get_db():
            yield test_session
        app.dependency_overrides[get_db] = override_get_db

        try:
            # Create the original failed payment event
            await crud.create_event(test_session, EventCreate(
                payment_id="pay_orig_001",
                event_type="PAYMENT_FAILED",
                event_payload={"amount": 8000, "status": "failed"},
            ))

            # Create a pending retry attempt with order_id
            attempt = await crud.create_recovery_attempt(
                test_session, "pay_orig_001", "retry_payment", "executed",
                "Retry order created", attempt_number=1,
                amount=8000, razorpay_order_id="order_webhook_001", outcome="pending",
            )

            # Simulate Razorpay webhook: payment.captured for a NEW payment_id
            # with order_id matching the pending retry
            webhook_payload = {
                "event": "payment.captured",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_captured_001",
                            "amount": 8000,
                            "currency": "INR",
                            "status": "captured",
                            "order_id": "order_webhook_001",
                        }
                    }
                },
            }

            # Skip signature verification (no secret configured in test)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                res = await ac.post(
                    "/api/v1/webhooks/razorpay",
                    json=webhook_payload,
                )

            assert res.status_code == 200
            body = res.json()
            assert body["status"] == "ok"
            assert body["risk_assessed"] is True

            # Verify the retry attempt was marked recovered
            from sqlalchemy import select
            refreshed = await test_session.execute(
                select(RecoveryAttempt).where(RecoveryAttempt.id == attempt.id)
            )
            updated = refreshed.scalar_one()
            assert updated.outcome == "recovered"
            assert updated.recovered_at is not None

            # Verify RECOVERY_SUCCEEDED event was created for the original payment
            events = await crud.get_events_for_payment(test_session, "pay_orig_001")
            recovery_events = [e for e in events if e.event_type == "RECOVERY_SUCCEEDED"]
            assert len(recovery_events) == 1
            assert recovery_events[0].event_payload["captured_payment_id"] == "pay_captured_001"
            assert recovery_events[0].event_payload["order_id"] == "order_webhook_001"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_captured_without_matching_order_does_not_mark(self, test_session: AsyncSession):
        """A payment.captured without a matching order_id does not affect recovery attempts."""
        async def override_get_db():
            yield test_session
        app.dependency_overrides[get_db] = override_get_db

        try:
            await crud.create_event(test_session, EventCreate(
                payment_id="pay_no_match_001",
                event_type="PAYMENT_FAILED",
                event_payload={"amount": 5000, "status": "failed"},
            ))
            attempt = await crud.create_recovery_attempt(
                test_session, "pay_no_match_001", "retry_payment", "executed",
                "Retry order created", attempt_number=1,
                amount=5000, razorpay_order_id="order_different", outcome="pending",
            )

            webhook_payload = {
                "event": "payment.captured",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_unrelated_001",
                            "amount": 5000,
                            "currency": "INR",
                            "status": "captured",
                            "order_id": "order_unrelated",
                        }
                    }
                },
            }

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                res = await ac.post("/api/v1/webhooks/razorpay", json=webhook_payload)

            assert res.status_code == 200

            # The retry attempt should still be pending
            from sqlalchemy import select
            refreshed = await test_session.execute(
                select(RecoveryAttempt).where(RecoveryAttempt.id == attempt.id)
            )
            still_pending = refreshed.scalar_one()
            assert still_pending.outcome == "pending"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_executed_retry_not_counted_as_recovered(self, test_session: AsyncSession):
        """A retry with outcome=pending is NOT counted in money_recovered."""
        await crud.create_recovery_attempt(
            test_session, "pay_pending_only", "retry_payment", "executed",
            "order_pending", attempt_number=1,
            amount=7000, razorpay_order_id="order_pending", outcome="pending",
        )

        metrics = await crud.get_recovery_metrics(test_session)
        assert metrics["money_recovered"] == 0
        assert metrics["money_at_risk"] == 7000
        assert metrics["recovery_rate"] == 0.0
        assert metrics["recovered_payments"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# 16. Configuration — RAZORPAY_WEBHOOK_SECRET
# ══════════════════════════════════════════════════════════════════════════════

class TestWebhookSecretConfig:
    def test_webhook_secret_defaults_to_empty(self):
        s = Settings()
        assert s.RAZORPAY_WEBHOOK_SECRET == ""

    def test_webhook_secret_can_be_set(self):
        s = Settings()
        s.RAZORPAY_WEBHOOK_SECRET = "whsec_test_abc123"
        assert s.RAZORPAY_WEBHOOK_SECRET == "whsec_test_abc123"


# ══════════════════════════════════════════════════════════════════════════════
# 17. Recovery decision — dispute/refund evaluated before success (Priority 3)
# ══════════════════════════════════════════════════════════════════════════════

class TestRecoveryDecisionPriority3:
    """Regression tests for the fixed decision order: dispute/refund must
    be evaluated BEFORE PAYMENT_SUCCESS to avoid suppressing escalation."""

    def test_dispute_with_success_escalates(self):
        """A dispute + PAYMENT_SUCCESS must escalate (not no_action)."""
        events = _make_event_dicts([
            ("PAYMENT_SUCCESS", {"amount": 8999}),
            ("DISPUTE_OPENED", {"dispute_id": "dsp_001"}),
        ])
        d = decide_action(events, 0.65, "high", 0)
        assert d.action == "escalate"
        assert "dispute" in d.reason.lower()

    def test_refund_with_success_sends_reminder(self):
        """A refund + PAYMENT_SUCCESS must send_reminder (not no_action)."""
        events = _make_event_dicts([
            ("PAYMENT_SUCCESS", {"amount": 3499}),
            ("REFUND_PENDING", {"amount": 3499}),
        ])
        d = decide_action(events, 0.25, "low", 0)
        assert d.action == "send_reminder"
        assert "refund" in d.reason.lower()

    def test_chargeback_with_success_escalates(self):
        """A chargeback + PAYMENT_SUCCESS must escalate."""
        events = _make_event_dicts([
            ("PAYMENT_SUCCESS", {"amount": 15000}),
            ("CHARGEBACK", {"chargeback_id": "cb_001"}),
        ])
        d = decide_action(events, 0.8, "high", 0)
        assert d.action == "escalate"

    def test_dispute_without_success_still_escalates(self):
        """A dispute without PAYMENT_SUCCESS still escalates (unchanged)."""
        events = _make_event_dicts([
            ("DISPUTE_OPENED", {"dispute_id": "dsp_002"}),
        ])
        d = decide_action(events, 0.65, "high", 0)
        assert d.action == "escalate"

    def test_refund_without_success_still_sends_reminder(self):
        """A refund without PAYMENT_SUCCESS still sends reminder (unchanged)."""
        events = _make_event_dicts([
            ("REFUND_PENDING", {"amount": 2500}),
        ])
        d = decide_action(events, 0.25, "low", 0)
        assert d.action == "send_reminder"

    def test_clean_payment_still_no_action(self):
        """A clean PAYMENT_SUCCESS with no disputes still returns no_action."""
        events = _make_event_dicts([
            ("PAYMENT_SUCCESS", {"amount": 4999}),
        ])
        d = decide_action(events, 0.0, "none", 0)
        assert d.action == "no_action"


# ══════════════════════════════════════════════════════════════════════════════
# 18. Webhook event ID deduplication
# ══════════════════════════════════════════════════════════════════════════════

class TestWebhookEventIdDedup:
    @pytest.mark.asyncio
    async def test_webhook_event_exists_returns_true_when_stored(self, test_session: AsyncSession):
        """webhook_event_exists returns True when an event with that ID exists."""
        await crud.create_event(
            test_session,
            EventCreate(payment_id="pay_dedup_001", event_type="PAYMENT_FAILED", event_payload={}),
            webhook_event_id="evt_rzp_dedup_001",
        )
        assert await crud.webhook_event_exists(test_session, "evt_rzp_dedup_001") is True

    @pytest.mark.asyncio
    async def test_webhook_event_exists_returns_false_when_missing(self, test_session: AsyncSession):
        """webhook_event_exists returns False for unknown event IDs."""
        assert await crud.webhook_event_exists(test_session, "evt_rzp_nonexistent") is False

    @pytest.mark.asyncio
    async def test_same_payment_different_event_ids_not_duplicate(self, test_session: AsyncSession):
        """Two events with the same payment_id but different webhook_event_ids are NOT duplicates."""
        await crud.create_event(
            test_session,
            EventCreate(payment_id="pay_multi_001", event_type="PAYMENT_FAILED", event_payload={}),
            webhook_event_id="evt_rzp_001",
        )
        # Second event with same payment_id but different event ID
        assert await crud.webhook_event_exists(test_session, "evt_rzp_002") is False

    @pytest.mark.asyncio
    async def test_duplicate_webhook_event_id_is_detected(self, test_session: AsyncSession):
        """Sending the same webhook event ID twice is detected as duplicate."""
        await crud.create_event(
            test_session,
            EventCreate(payment_id="pay_dup_001", event_type="PAYMENT_FAILED", event_payload={}),
            webhook_event_id="evt_rzp_same_001",
        )
        assert await crud.webhook_event_exists(test_session, "evt_rzp_same_001") is True

    @pytest.mark.asyncio
    async def test_webhook_idempotency_via_endpoint(self, test_session: AsyncSession):
        """Duplicate webhook delivery (same event.id) returns duplicate=True."""
        async def override_get_db():
            yield test_session
        app.dependency_overrides[get_db] = override_get_db

        try:
            webhook_payload = {
                "id": "evt_rzp_endpoint_dedup",
                "event": "payment.failed",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_endpoint_dedup_001",
                            "amount": 5000,
                            "currency": "INR",
                            "status": "failed",
                        }
                    }
                },
            }

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                # First delivery
                res1 = await ac.post("/api/v1/webhooks/razorpay", json=webhook_payload)
                assert res1.status_code == 200
                body1 = res1.json()
                assert body1["status"] == "ok"
                assert body1["duplicate"] is False

                # Second delivery (same event.id)
                res2 = await ac.post("/api/v1/webhooks/razorpay", json=webhook_payload)
                assert res2.status_code == 200
                body2 = res2.json()
                assert body2["status"] == "duplicate"
                assert body2["duplicate"] is True
        finally:
            app.dependency_overrides.clear()


class TestWebhookHeaderDedup:
    @pytest.mark.asyncio
    async def test_x_razorpay_event_id_header_used_for_dedup(self, test_session: AsyncSession):
        """Duplicate delivery with same x-razorpay-event-id header is deduplicated."""
        async def override_get_db():
            yield test_session
        app.dependency_overrides[get_db] = override_get_db

        try:
            webhook_payload = {
                "event": "payment.failed",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_hdr_dedup_001",
                            "amount": 7500,
                            "currency": "INR",
                            "status": "failed",
                        }
                    }
                },
            }
            headers = {"x-razorpay-event-id": "evt_header_dedup_001"}

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                res1 = await ac.post("/api/v1/webhooks/razorpay", json=webhook_payload, headers=headers)
                assert res1.status_code == 200
                assert res1.json()["duplicate"] is False

                # Second delivery with same header — should be deduplicated
                res2 = await ac.post("/api/v1/webhooks/razorpay", json=webhook_payload, headers=headers)
                assert res2.status_code == 200
                assert res2.json()["duplicate"] is True
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_header_takes_precedence_over_body_id(self, test_session: AsyncSession):
        """x-razorpay-event-id header is used even when body has a different 'id'."""
        async def override_get_db():
            yield test_session
        app.dependency_overrides[get_db] = override_get_db

        try:
            webhook_payload = {
                "id": "evt_body_only",
                "event": "payment.failed",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay precedence_001",
                            "amount": 3000,
                            "currency": "INR",
                            "status": "failed",
                        }
                    }
                },
            }
            headers = {"x-razorpay-event-id": "evt_header_only"}

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                res1 = await ac.post("/api/v1/webhooks/razorpay", json=webhook_payload, headers=headers)
                assert res1.status_code == 200
                assert res1.json()["duplicate"] is False

                # Same header, different body id — still deduplicated
                payload2 = {**webhook_payload, "id": "evt_different_body"}
                res2 = await ac.post("/api/v1/webhooks/razorpay", json=payload2, headers=headers)
                assert res2.status_code == 200
                assert res2.json()["duplicate"] is True
        finally:
            app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════════════════════════
# 19. Webhook signature verification uses WEBHOOK_SECRET
# ══════════════════════════════════════════════════════════════════════════════

class TestWebhookSignatureVerification:
    @pytest.mark.asyncio
    async def test_missing_signature_rejected_when_secret_configured(self, test_session: AsyncSession):
        """When RAZORPAY_WEBHOOK_SECRET is set, missing signature returns 401."""
        async def override_get_db():
            yield test_session
        app.dependency_overrides[get_db] = override_get_db

        try:
            with patch("app.api.routes.settings") as mock_settings:
                mock_settings.RAZORPAY_WEBHOOK_SECRET = "whsec_test_real_secret"

                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    res = await ac.post(
                        "/api/v1/webhooks/razorpay",
                        json={"event": "payment.failed", "payload": {}},
                    )
                assert res.status_code == 401
                assert "missing" in res.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_invalid_signature_rejected_when_secret_configured(self, test_session: AsyncSession):
        """When RAZORPAY_WEBHOOK_SECRET is set, wrong signature returns 401."""
        async def override_get_db():
            yield test_session
        app.dependency_overrides[get_db] = override_get_db

        try:
            with patch("app.api.routes.settings") as mock_settings:
                mock_settings.RAZORPAY_WEBHOOK_SECRET = "whsec_test_real_secret"

                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    res = await ac.post(
                        "/api/v1/webhooks/razorpay",
                        json={"event": "payment.failed", "payload": {}},
                        headers={"x-razorpay-signature": "invalid_signature_abc"},
                    )
                assert res.status_code == 401
                assert "invalid" in res.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_skips_verification_when_secret_empty(self, test_session: AsyncSession):
        """When RAZORPAY_WEBHOOK_SECRET is empty, signature is not checked."""
        async def override_get_db():
            yield test_session
        app.dependency_overrides[get_db] = override_get_db

        try:
            with patch("app.api.routes.settings") as mock_settings:
                mock_settings.RAZORPAY_WEBHOOK_SECRET = ""

                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    res = await ac.post(
                        "/api/v1/webhooks/razorpay",
                        json={"event": "payment.failed", "payload": {}},
                    )
                # Should not fail on signature — will fail on missing payment entity instead
                assert res.status_code in (200, 422)
        finally:
            app.dependency_overrides.clear()
