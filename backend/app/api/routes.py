from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import hashlib
import hmac
import logging

from app.config import settings
from app.database.connection import get_db
from app.database import crud
from app.models.schemas import (
    EventCreate, EventResponse, RiskAssessmentResponse,
    RazorpayWebhookPayload, WebhookResponse,
    RecoveryAttemptResponse, RecoveryMetricsResponse,
    DiagnosisResultResponse,
)
from app.services.detector import assess_risk
from app.services.recovery import decide_action
from app.services.diagnosis import diagnose
from app.services.razorpay_client import create_retry_order

logger = logging.getLogger("revcure.webhook")

# ── Razorpay event type mapping ────────────────────────────────────────
# Maps Razorpay webhook event strings to our internal Event.event_type.
RAZORPAY_EVENT_MAP = {
    "payment.authorized":    "PAYMENT_AUTHORIZED",
    "payment.captured":      "PAYMENT_SUCCESS",
    "payment.failed":        "PAYMENT_FAILED",
    "payment.dispute.created":  "DISPUTE_OPENED",
    "payment.dispute.closed":   "DISPUTE_CLOSED",
    "refund.created":        "REFUND_PENDING",
    "refund.processed":      "REFUND_PROCESSED",
    "settlement.processed":  "SETTLEMENT_PROCESSED",
    "settlement.reconciled": "SETTLEMENT_RECONCILED",
}

# Event types that are revenue-at-risk signals for the detector.
RISK_SIGNAL_EVENTS = {
    "PAYMENT_FAILED", "REFUND_PENDING", "DISPUTE_OPENED",
    "SETTLEMENT_DELAYED", "RETRY_FAILED", "RETRY_EXHAUSTED",
}

router = APIRouter()

@router.get("/health", tags=["Health"])
async def health_check():
    """
    Health check endpoint returning system status.
    """
    return {"status": "ok"}

@router.get(
    "/db-test",
    tags=["Diagnostics"],
    summary="Internal DB Diagnostic",
    description="Development and internal diagnostic endpoint to test PostgreSQL connectivity. Disabled by default in production.",
)
async def db_test(db: AsyncSession = Depends(get_db)):
    """
    Development and internal diagnostic endpoint to verify database connectivity.
    Executes SELECT 1. Only enabled when ENABLE_DB_TEST is True.
    """
    if not settings.ENABLE_DB_TEST:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diagnostic endpoint is disabled.",
        )

    result = await db.execute(text("SELECT 1"))
    scalar_value = result.scalar()
    return {
        "status": "connected",
        "database": "postgresql",
        "result": scalar_value,
        "diagnostic_only": True,
    }

@router.post(
    "/events",
    response_model=EventResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Events"],
    summary="Create an audit event",
)
async def create_event(
    event_in: EventCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    Persist a new audit event into the database.
    """
    return await crud.create_event(db, event_in)

@router.get(
    "/events",
    response_model=List[EventResponse],
    tags=["Events"],
    summary="List audit events",
)
async def list_events(
    limit: int = Query(default=100, ge=1, le=500, description="Max number of events to return"),
    offset: int = Query(default=0, ge=0, description="Offset for pagination"),
    payment_id: Optional[str] = Query(default=None, description="Filter by payment ID"),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieve paginated audit events ordered newest first.
    """
    return await crud.list_events(db, limit=limit, offset=offset, payment_id=payment_id)


# ── Risk Assessment Endpoints ──────────────────────────────────────────

@router.get(
    "/payments/{payment_id}/risk",
    response_model=RiskAssessmentResponse,
    tags=["Risk"],
    summary="Get revenue-at-risk assessment for a payment",
)
async def get_payment_risk(
    payment_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Return the stored risk assessment for a payment.
    If no assessment exists yet, runs the detector on the fly and persists the result.
    """
    assessment = await crud.get_risk_assessment(db, payment_id)

    if assessment is not None:
        return assessment

    # No stored assessment — compute on the fly
    events = await crud.get_events_for_payment(db, payment_id)
    if not events:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No events found for payment '{payment_id}'",
        )

    event_dicts = [
        {
            "event_type": e.event_type,
            "event_payload": e.event_payload,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]
    result = assess_risk(event_dicts)

    persisted = await crud.upsert_risk_assessment(
        db,
        payment_id=payment_id,
        risk_score=result.risk_score,
        risk_label=result.risk_label,
        reasons=result.reasons,
    )
    return persisted


@router.post(
    "/payments/{payment_id}/risk/assess",
    response_model=RiskAssessmentResponse,
    tags=["Risk"],
    summary="Re-assess revenue-at-risk for a payment",
)
async def re_assess_payment_risk(
    payment_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Force a fresh risk assessment for a payment by re-evaluating all its events.
    Always overwrites any existing assessment.
    """
    events = await crud.get_events_for_payment(db, payment_id)
    if not events:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No events found for payment '{payment_id}'",
        )

    event_dicts = [
        {
            "event_type": e.event_type,
            "event_payload": e.event_payload,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]
    result = assess_risk(event_dicts)

    persisted = await crud.upsert_risk_assessment(
        db,
        payment_id=payment_id,
        risk_score=result.risk_score,
        risk_label=result.risk_label,
        reasons=result.reasons,
    )
    return persisted


@router.get(
    "/risk/payments",
    response_model=List[RiskAssessmentResponse],
    tags=["Risk"],
    summary="List payments by risk level",
)
async def list_risky_payments(
    min_score: float = Query(default=0.0, ge=0.0, le=1.0, description="Minimum risk score threshold"),
    limit: int = Query(default=100, ge=1, le=500, description="Max results"),
    offset: int = Query(default=0, ge=0, description="Offset for pagination"),
    db: AsyncSession = Depends(get_db),
):
    """
    List all payments with risk_score >= min_score, highest risk first.
    Useful for the recovery workflow to prioritise interventions.
    """
    return await crud.list_risk_assessments(db, min_score=min_score, limit=limit, offset=offset)


# ── Razorpay Webhook Endpoint ──────────────────────────────────────────

@router.post(
    "/webhooks/razorpay",
    response_model=WebhookResponse,
    tags=["Webhooks"],
    summary="Receive Razorpay payment webhook",
)
async def razorpay_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Accept Razorpay webhook POST, verify signature, map to an internal Event,
    store it, and trigger risk assessment for the payment.

    Razorpay retries on failure — this endpoint is idempotent:
    duplicate deliveries of the same (payment_id, event_type) are skipped.
    """
    # ── Read raw body for signature verification ──────────────────────
    raw_body = await request.body()

    # ── Verify X-Razorpay-Signature ───────────────────────────────────
    # Uses RAZORPAY_WEBHOOK_SECRET (separate from the API key secret).
    # Skip verification in development if no secret is configured.
    webhook_secret = settings.RAZORPAY_WEBHOOK_SECRET
    if webhook_secret:
        signature = request.headers.get("x-razorpay-signature", "")
        if not signature:
            logger.warning("Webhook missing X-Razorpay-Signature header")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing webhook signature",
            )
        expected = hmac.HMAC(
            webhook_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            logger.warning("Webhook signature verification failed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature",
            )
        logger.info("Webhook signature verified")
    else:
        logger.debug("RAZORPAY_WEBHOOK_SECRET not configured — skipping signature check (dev mode)")

    # ── Parse payload ─────────────────────────────────────────────────
    import json
    payload_dict = json.loads(raw_body)
    payload = RazorpayWebhookPayload.model_validate(payload_dict)

    # Extract Razorpay's unique event ID for deduplication
    razorpay_event_id = payload_dict.get("id")

    # Extract payment entity from the Razorpay payload
    payment_entity = None
    if payload.payload:
        payment_entity = payload.payload.get("payment", {}).get("entity")

    razorpay_event = payload.event
    internal_event_type = RAZORPAY_EVENT_MAP.get(razorpay_event)

    # Unknown Razorpay event — acknowledge but do nothing
    if internal_event_type is None:
        logger.info("Ignoring unrecognised Razorpay event: %s", razorpay_event)
        return WebhookResponse(
            status="ignored",
            event_type=razorpay_event,
            payment_id=None,
            duplicate=False,
            risk_assessed=False,
        )

    # Extract payment_id from the entity
    payment_id = None
    if payment_entity:
        payment_id = payment_entity.get("id")

    if not payment_id:
        logger.warning("Razorpay webhook missing payment entity/id: event=%s", razorpay_event)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Razorpay payload missing payment entity or id",
        )

    # ── Duplicate check (by Razorpay event ID) ────────────────────────
    # Razorpay retries webhooks with the same event ID on failure.
    # Using the unique event ID prevents processing duplicates while
    # still allowing legitimate events of the same type for the same
    # payment (e.g. multiple PAYMENT_FAILED events).
    if razorpay_event_id:
        is_duplicate = await crud.webhook_event_exists(db, razorpay_event_id)
        if is_duplicate:
            logger.info("Duplicate webhook skipped: event_id=%s", razorpay_event_id)
            return WebhookResponse(
                status="duplicate",
                event_type=internal_event_type,
                payment_id=payment_id,
                duplicate=True,
                risk_assessed=False,
            )

    # ── Store event ──────────────────────────────────────────────────
    event_in = EventCreate(
        payment_id=payment_id,
        event_type=internal_event_type,
        event_payload=payment_entity or {},
    )
    await crud.create_event(db, event_in, webhook_event_id=razorpay_event_id)
    logger.info("Stored event: %s / %s (event_id=%s)", payment_id, internal_event_type, razorpay_event_id)

    # ── Check if this captured payment closes a recovery retry ─────────
    # When a retry order is captured, Razorpay sends a new payment.captured
    # webhook with a NEW payment_id.  We match via order_id to link it
    # back to the original failed payment's recovery attempt.
    recovery_marked = False
    if internal_event_type == "PAYMENT_SUCCESS" and payment_entity:
        captured_order_id = payment_entity.get("order_id")
        if captured_order_id:
            pending_retry = await crud.find_pending_retry_by_order_id(db, captured_order_id)
            if pending_retry:
                original_payment_id = pending_retry.payment_id
                await crud.mark_recovery_recovered(
                    db, pending_retry.id, payment_id,
                )
                # Record a RECOVERY_SUCCEEDED event against the ORIGINAL payment
                await crud.create_event(
                    db,
                    EventCreate(
                        payment_id=original_payment_id,
                        event_type="RECOVERY_SUCCEEDED",
                        event_payload={
                            "captured_payment_id": payment_id,
                            "order_id": captured_order_id,
                            "amount": pending_retry.amount,
                            "recovery_attempt_id": pending_retry.id,
                        },
                    ),
                )
                recovery_marked = True
                logger.info(
                    "Recovery succeeded for %s: captured=%s order=%s amount=%s",
                    original_payment_id, payment_id, captured_order_id,
                    pending_retry.amount,
                )

    # ── Trigger risk assessment ──────────────────────────────────────
    risk_assessed = False
    all_events = await crud.get_events_for_payment(db, payment_id)
    event_dicts = [
        {
            "event_type": e.event_type,
            "event_payload": e.event_payload,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in all_events
    ]
    result = assess_risk(event_dicts)
    await crud.upsert_risk_assessment(
        db,
        payment_id=payment_id,
        risk_score=result.risk_score,
        risk_label=result.risk_label,
        reasons=result.reasons,
    )
    risk_assessed = True
    logger.info(
        "Risk assessed for %s: score=%.4f label=%s",
        payment_id, result.risk_score, result.risk_label,
    )

    return WebhookResponse(
        status="ok",
        event_type=internal_event_type,
        payment_id=payment_id,
        duplicate=False,
        risk_assessed=risk_assessed,
    )


# ── Recovery Workflow Endpoint ─────────────────────────────────────────

@router.post(
    "/payments/{payment_id}/recover",
    response_model=RecoveryAttemptResponse,
    tags=["Recovery"],
    summary="Trigger bounded recovery for a payment",
)
async def recover_payment(
    payment_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Run the deterministic recovery workflow for a payment:
    1. Fetch events and risk assessment
    2. Decide the next intervention (retry / remind / escalate / none)
    3. Record the attempt with bounding guards
    4. Return the decision

    Bounding rules:
    - Max 3 payment retries
    - Max 5 total recovery actions per payment
    - Escalation when limits are hit
    """
    # ── Load context ──────────────────────────────────────────────────
    events = await crud.get_events_for_payment(db, payment_id)
    if not events:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No events found for payment '{payment_id}'",
        )

    assessment = await crud.get_risk_assessment(db, payment_id)
    previous_attempts = await crud.count_recovery_attempts(db, payment_id)

    risk_score = assessment.risk_score if assessment else 0.0
    risk_label = assessment.risk_label if assessment else "none"

    event_dicts = [
        {
            "event_type": e.event_type,
            "event_payload": e.event_payload,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]

    # ── Decide ────────────────────────────────────────────────────────
    decision = decide_action(
        events=event_dicts,
        risk_score=risk_score,
        risk_label=risk_label,
        previous_attempts=previous_attempts,
    )

    # ── Run constrained diagnosis (explain/contextualize only) ────────
    # The diagnosis classifies the payment situation but NEVER executes
    # actions or bypasses the deterministic recovery policy.
    diagnosis = diagnose(event_dicts, risk_score, risk_label)

    # ── No-action decisions: return without persisting ────────────────
    # When the decision engine says "no_action", we do NOT create an
    # event or a recovery_attempt row — this prevents burning the
    # bounded attempt counter on informational decisions.
    if decision.action == "no_action":
        logger.info("No recovery action for %s: %s", payment_id, decision.reason)
        return RecoveryAttemptResponse(
            id=0,
            payment_id=payment_id,
            action="no_action",
            status="executed",
            reason=decision.reason,
            attempt_number=decision.attempt_number,
            created_at=datetime.now(timezone.utc),
            diagnosis=DiagnosisResultResponse(
                category=diagnosis.category,
                confidence=diagnosis.confidence,
                explanation=diagnosis.explanation,
                supporting_event=diagnosis.supporting_event,
            ),
        )

    # ── Execute action ────────────────────────────────────────────────
    action_status = "executed"
    action_reason = decision.reason
    action_payload = {
        "action": decision.action,
        "reason": decision.reason,
        "attempt_number": decision.attempt_number,
        "risk_score": risk_score,
        "risk_label": risk_label,
    }
    retry_order_id = None
    amount = 0

    # For retry_payment: call the Razorpay Orders API to create a retry order
    if decision.action == "retry_payment":
        # Extract amount from the latest failed payment event
        amount = 0
        for e in reversed(events):
            payload = e.event_payload or {}
            if payload.get("amount"):
                try:
                    amount = int(payload["amount"])
                except (TypeError, ValueError):
                    pass
                break

        if amount <= 0:
            action_status = "failed"
            action_reason = "Cannot retry: payment amount not found in event history"
            logger.warning("Retry skipped for %s: no amount in events", payment_id)
        else:
            razorpay_result = await create_retry_order(
                payment_id=payment_id,
                amount=amount,
            )
            if razorpay_result.success:
                action_status = "executed"
                action_reason = (
                    f"Retry order created (amount={amount} paise)"
                )
                retry_order_id = razorpay_result.order_id
            else:
                action_status = "failed"
                action_reason = razorpay_result.error or "Payment gateway request failed"
                retry_order_id = None

    # Map action to event type
    event_type_for_action = {
        "retry_payment":  "RETRY_PAYMENT",
        "send_reminder":  "REMINDER_SENT",
        "escalate":       "ESCALATED",
    }
    action_event_type = event_type_for_action.get(decision.action)
    if action_event_type:
        action_payload["final_status"] = action_status
        await crud.create_event(
            db,
            EventCreate(
                payment_id=payment_id,
                event_type=action_event_type,
                event_payload=action_payload,
            ),
        )
        logger.info(
            "Recorded %s event for %s (status=%s)",
            action_event_type, payment_id, action_status,
        )

    # ── Record attempt ────────────────────────────────────────────────
    retry_amount = amount if decision.action == "retry_payment" else None
    retry_order = retry_order_id if decision.action == "retry_payment" else None
    attempt_outcome = "pending" if (decision.action == "retry_payment" and action_status == "executed") else action_status

    attempt = await crud.create_recovery_attempt(
        db,
        payment_id=payment_id,
        action=decision.action,
        status=action_status,
        reason=action_reason,
        attempt_number=decision.attempt_number,
        amount=retry_amount,
        razorpay_order_id=retry_order,
        outcome=attempt_outcome,
    )

    logger.info(
        "Recovery %s for %s: action=%s reason=%s (diagnosis=%s)",
        attempt.status, payment_id, decision.action, decision.reason, diagnosis.category,
    )

    # Attach diagnosis to the persisted attempt response
    response = RecoveryAttemptResponse.model_validate(attempt)
    response.diagnosis = DiagnosisResultResponse(
        category=diagnosis.category,
        confidence=diagnosis.confidence,
        explanation=diagnosis.explanation,
        supporting_event=diagnosis.supporting_event,
    )
    return response


# ── Recovery Metrics Endpoint ─────────────────────────────────────────

@router.get(
    "/recovery/metrics",
    response_model=RecoveryMetricsResponse,
    tags=["Recovery"],
    summary="Get recovery outcome metrics",
)
async def get_recovery_metrics(
    db: AsyncSession = Depends(get_db),
):
    """
    Return honest recovery metrics based on actual payment capture outcomes.

    - money_at_risk:    total amount (paise) from failed-payment retry attempts
    - money_recovered:  total amount (paise) from retry attempts where payment was captured
    - recovery_rate:    money_recovered / money_at_risk
    - eligible_payments: count of distinct payments with retry attempts
    - recovered_payments: count of distinct payments actually recovered
    """
    return await crud.get_recovery_metrics(db)
