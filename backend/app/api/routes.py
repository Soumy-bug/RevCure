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
    RecoveryAttemptResponse,
)
from app.services.detector import assess_risk
from app.services.recovery import decide_action

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
    # Skip verification in development if no secret is configured.
    razorpay_secret = settings.RAZORPAY_KEY_SECRET
    if razorpay_secret:
        signature = request.headers.get("x-razorpay-signature", "")
        if not signature:
            logger.warning("Webhook missing X-Razorpay-Signature header")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing webhook signature",
            )
        expected = hmac.new(
            razorpay_secret.encode("utf-8"),
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
        logger.debug("Razorpay secret not configured — skipping signature check (dev mode)")

    # ── Parse payload ─────────────────────────────────────────────────
    import json
    payload_dict = json.loads(raw_body)
    payload = RazorpayWebhookPayload.model_validate(payload_dict)

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

    # ── Duplicate check ──────────────────────────────────────────────
    is_duplicate = await crud.event_exists(db, payment_id, internal_event_type)
    if is_duplicate:
        logger.info("Duplicate webhook skipped: %s / %s", payment_id, internal_event_type)
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
    await crud.create_event(db, event_in)
    logger.info("Stored event: %s / %s", payment_id, internal_event_type)

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

    # ── Execute action as auditable event ─────────────────────────────
    event_type_for_action = {
        "retry_payment":  "RETRY_PAYMENT",
        "send_reminder":  "REMINDER_SENT",
        "escalate":       "ESCALATED",
    }
    action_event_type = event_type_for_action.get(decision.action)
    if action_event_type:
        action_payload = {
            "action": decision.action,
            "reason": decision.reason,
            "attempt_number": decision.attempt_number,
            "risk_score": risk_score,
            "risk_label": risk_label,
        }
        await crud.create_event(
            db,
            EventCreate(
                payment_id=payment_id,
                event_type=action_event_type,
                event_payload=action_payload,
            ),
        )
        logger.info("Recorded %s event for %s", action_event_type, payment_id)

    # ── Record attempt ────────────────────────────────────────────────
    attempt = await crud.create_recovery_attempt(
        db,
        payment_id=payment_id,
        action=decision.action,
        status="executed" if decision.action != "no_action" else "skipped",
        reason=decision.reason,
        attempt_number=decision.attempt_number,
    )

    logger.info(
        "Recovery %s for %s: action=%s reason=%s",
        attempt.status, payment_id, decision.action, decision.reason,
    )

    return attempt
