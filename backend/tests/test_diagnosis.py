"""
Tests for the constrained diagnosis layer.

Verifies:
1. Common payment failure diagnosis (bank_timeout, insufficient_funds, etc.)
2. Timeout / insufficient-funds classification from error codes
3. Unknown classification when no pattern matches
4. Diagnosis does NOT bypass recovery limits
5. Deterministic policy (decide_action) still controls final action
"""

import pytest
import pytest_asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.database.models import Base
from app.database.connection import get_db
from app.database import crud
from app.models.schemas import EventCreate
from app.services.diagnosis import diagnose, DiagnosisResult
from app.services.recovery import decide_action, MAX_TOTAL_ACTIONS

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

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


def _make_event_dicts(types_and_payloads):
    """Helper: build event_dicts list from [(type, payload), ...]"""
    return [{"event_type": t, "event_payload": p or {}, "created_at": None} for t, p in types_and_payloads]


# ══════════════════════════════════════════════════════════════════════════════
# 1. Common payment failure diagnosis
# ══════════════════════════════════════════════════════════════════════════════

class TestDiagnosisCommonFailures:
    def test_bank_timeout_diagnosed_from_error_code(self):
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 5000, "error_code": "gateway_timeout", "error_description": "Bank took too long"}),
        ])
        result = diagnose(events)
        assert result.category == "bank_timeout"
        assert result.confidence >= 0.8
        assert "bank timeout" in result.explanation.lower() or "gateway_timeout" in result.explanation
        assert result.supporting_event is not None

    def test_insufficient_funds_diagnosed(self):
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 99999, "error_code": "insufficient_funds", "error_description": "Not enough money"}),
        ])
        result = diagnose(events)
        assert result.category == "insufficient_funds"
        assert result.confidence >= 0.8

    def test_expired_card_diagnosed(self):
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 25000, "error_code": "card_expired", "error_description": "Card expired"}),
        ])
        result = diagnose(events)
        assert result.category == "expired_card"
        assert result.confidence >= 0.9

    def test_card_declined_diagnosed(self):
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 15000, "error_code": "do_not_honor", "error_description": "Issuer declined"}),
        ])
        result = diagnose(events)
        assert result.category == "card_declined"
        assert result.confidence >= 0.6

    def test_wallet_balance_low_diagnosed(self):
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 1999, "error_code": "wallet_balance_low", "error_description": "Wallet empty"}),
        ])
        result = diagnose(events)
        assert result.category == "insufficient_funds"
        assert result.confidence >= 0.8

    def test_generic_payment_failed_with_no_error_code(self):
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 5000, "status": "failed"}),
        ])
        result = diagnose(events)
        assert result.category == "payment_failed"
        assert result.confidence >= 0.4


# ══════════════════════════════════════════════════════════════════════════════
# 2. Timeout / insufficient-funds classification from payloads
# ══════════════════════════════════════════════════════════════════════════════

class TestDiagnosisFromPayloads:
    def test_retry_exhausted_with_error_code_diagnosed(self):
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 15000, "error_code": "do_not_honor"}),
            ("RETRY_FAILED", {"reason": "exhausted", "error_code": "do_not_honor"}),
        ])
        result = diagnose(events)
        assert result.category == "card_declined"

    def test_dispute_event_type_classified(self):
        events = _make_event_dicts([
            ("PAYMENT_SUCCESS", {"amount": 8999}),
            ("DISPUTE_OPENED", {"dispute_id": "dsp_001"}),
        ])
        result = diagnose(events)
        assert result.category == "dispute"
        assert result.confidence >= 0.7

    def test_refund_pending_event_type_classified(self):
        events = _make_event_dicts([
            ("PAYMENT_SUCCESS", {"amount": 3499}),
            ("REFUND_PENDING", {"amount": 3499}),
        ])
        result = diagnose(events)
        assert result.category == "refund_pending"
        assert result.confidence >= 0.6

    def test_successful_payment_not_diagnosed_as_failure(self):
        events = _make_event_dicts([
            ("PAYMENT_AUTHORIZED", {"amount": 4999}),
            ("PAYMENT_SUCCESS", {"amount": 4999}),
        ])
        result = diagnose(events)
        # Successful payment with no failures → category is payment_failed with confidence 0.0
        assert result.confidence == 0.0
        assert "succeeded" in result.explanation.lower()

    def test_chargeback_classified_as_dispute(self):
        events = _make_event_dicts([
            ("PAYMENT_SUCCESS", {"amount": 15000}),
            ("CHARGEBACK", {"chargeback_id": "cb_001"}),
        ])
        result = diagnose(events)
        assert result.category == "dispute"


# ══════════════════════════════════════════════════════════════════════════════
# 3. Unknown classification
# ══════════════════════════════════════════════════════════════════════════════

class TestDiagnosisUnknown:
    def test_empty_events_returns_unknown(self):
        result = diagnose([])
        assert result.category == "unknown"
        assert result.confidence == 0.0
        assert "no events" in result.explanation.lower()

    def test_settlement_only_returns_unknown(self):
        events = _make_event_dicts([
            ("SETTLEMENT_PROCESSED", {"amount": 5000}),
        ])
        result = diagnose(events)
        assert result.category == "unknown"
        assert result.confidence <= 0.2

    def test_diagnosis_is_deterministic(self):
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 5000, "error_code": "gateway_timeout"}),
        ])
        r1 = diagnose(events)
        r2 = diagnose(events)
        assert r1.category == r2.category
        assert r1.confidence == r2.confidence
        assert r1.explanation == r2.explanation


# ══════════════════════════════════════════════════════════════════════════════
# 4. Diagnosis does NOT bypass recovery limits
# ══════════════════════════════════════════════════════════════════════════════

class TestDiagnosisDoesNotBypassLimits:
    def test_diagnosis_cannot_override_hard_limit(self):
        """Even a high-confidence bank_timeout diagnosis is irrelevant
        when the hard limit is reached — decide_action returns no_action."""
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 5000, "error_code": "gateway_timeout"}),
        ])

        # Diagnosis says it's a timeout (high confidence, retryable)
        diagnosis = diagnose(events)
        assert diagnosis.category == "bank_timeout"
        assert diagnosis.confidence >= 0.8

        # But the policy engine says no_action when at the hard limit
        decision = decide_action(events, 0.5, "medium", previous_attempts=MAX_TOTAL_ACTIONS)
        assert decision.action == "no_action"
        assert decision.bounded is True

    def test_diagnosis_cannot_force_escalation(self):
        """Diagnosis is informational — it cannot change the policy outcome."""
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 5000, "error_code": "insufficient_funds"}),
        ])

        # Diagnosis says insufficient_funds (retryable)
        diagnosis = diagnose(events)
        assert diagnosis.category == "insufficient_funds"

        # Policy decides based on risk + attempts, not diagnosis
        decision = decide_action(events, 0.4, "medium", previous_attempts=0)
        assert decision.action == "retry_payment"

    def test_diagnosis_cannot_override_dispute_escalation(self):
        """Dispute diagnosis and dispute policy escalation are independent."""
        events = _make_event_dicts([
            ("PAYMENT_SUCCESS", {"amount": 8999}),
            ("DISPUTE_OPENED", {"dispute_id": "dsp_001"}),
        ])
        diagnosis = diagnose(events)
        assert diagnosis.category == "dispute"

        decision = decide_action(events, 0.65, "high", 0)
        assert decision.action == "escalate"


# ══════════════════════════════════════════════════════════════════════════════
# 5. Deterministic policy controls final action
# ══════════════════════════════════════════════════════════════════════════════

class TestDeterministicPolicyControl:
    def test_same_inputs_always_produce_same_decision(self):
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 12500, "error_code": "gateway_timeout"}),
        ])
        d1 = decide_action(events, 0.4, "medium", 0)
        d2 = decide_action(events, 0.4, "medium", 0)
        assert d1.action == d2.action
        assert d1.reason == d2.reason

    def test_diagnosis_and_policy_are_independent(self):
        """The same events produce the same policy decision regardless of diagnosis."""
        events = _make_event_dicts([
            ("PAYMENT_FAILED", {"amount": 5000, "error_code": "gateway_timeout"}),
        ])
        d1 = diagnose(events)
        d2 = diagnose(events)
        assert d1.category == d2.category

        # Policy is unaffected by diagnosis
        p1 = decide_action(events, 0.4, "medium", 0)
        p2 = decide_action(events, 0.4, "medium", 0)
        assert p1.action == p2.action

    @pytest.mark.asyncio
    async def test_recover_endpoint_includes_diagnosis(self, test_session: AsyncSession):
        """The recovery endpoint returns a diagnosis alongside the decision."""
        async def override_get_db():
            yield test_session
        app.dependency_overrides[get_db] = override_get_db

        try:
            await crud.create_event(test_session, EventCreate(
                payment_id="pay_diag_001",
                event_type="PAYMENT_FAILED",
                event_payload={"amount": 5000, "error_code": "gateway_timeout", "error_description": "Bank timeout"},
            ))
            await crud.upsert_risk_assessment(
                test_session, "pay_diag_001", 0.4, "medium",
                ["Payment failed 1 time(s)"],
            )

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                res = await ac.post("/api/v1/payments/pay_diag_001/recover")

            assert res.status_code == 200
            body = res.json()

            # Diagnosis is present
            assert "diagnosis" in body
            assert body["diagnosis"] is not None
            assert body["diagnosis"]["category"] == "bank_timeout"
            assert body["diagnosis"]["confidence"] >= 0.8
            assert "bank timeout" in body["diagnosis"]["explanation"].lower() or "gateway_timeout" in body["diagnosis"]["explanation"].lower()

            # Policy still controls the action
            assert body["action"] == "retry_payment"
        finally:
            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_no_action_endpoint_includes_diagnosis(self, test_session: AsyncSession):
        """Even no_action responses include a diagnosis."""
        async def override_get_db():
            yield test_session
        app.dependency_overrides[get_db] = override_get_db

        try:
            await crud.create_event(test_session, EventCreate(
                payment_id="pay_diag_healthy",
                event_type="PAYMENT_SUCCESS",
                event_payload={"amount": 4999, "status": "captured"},
            ))
            await crud.upsert_risk_assessment(
                test_session, "pay_diag_healthy", 0.0, "none", [],
            )

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                res = await ac.post("/api/v1/payments/pay_diag_healthy/recover")

            assert res.status_code == 200
            body = res.json()
            assert body["action"] == "no_action"
            assert body["diagnosis"] is not None
            assert body["diagnosis"]["category"] == "payment_failed"
            assert body["diagnosis"]["confidence"] == 0.0
        finally:
            app.dependency_overrides.clear()
