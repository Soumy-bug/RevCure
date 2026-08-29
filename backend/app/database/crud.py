from typing import List, Optional, Union, Dict, Any
from sqlalchemy import select, func as sqlfunc, case
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.models import Event, RiskAssessment, RecoveryAttempt
from app.models.schemas import EventCreate

async def create_event(
    db: AsyncSession,
    event_data: Union[EventCreate, Dict[str, Any]],
    webhook_event_id: Optional[str] = None,
) -> Event:
    """
    Creates and commits a new audit Event to the database.
    Accepts either an EventCreate Pydantic schema or a dictionary.
    Includes explicit rollback on exception and refresh after commit.

    Args:
        webhook_event_id: Razorpay's unique event ID for deduplication.
                          Only stored for webhook-originated events.
    """
    if isinstance(event_data, EventCreate):
        payment_id = event_data.payment_id
        event_type = event_data.event_type
        event_payload = event_data.event_payload
    else:
        payment_id = event_data.get("payment_id")
        event_type = event_data.get("event_type", "UNKNOWN")
        event_payload = event_data.get("event_payload") or event_data.get("payload")

    event = Event(
        payment_id=payment_id,
        event_type=event_type,
        event_payload=event_payload,
        webhook_event_id=webhook_event_id,
    )

    db.add(event)
    try:
        await db.commit()
        await db.refresh(event)
        return event
    except Exception:
        await db.rollback()
        raise

# Alias for backward compatibility
append_event = create_event

async def list_events(
    db: AsyncSession,
    limit: int = 100,
    offset: int = 0,
    payment_id: Optional[str] = None,
) -> List[Event]:
    """
    Fetches audit events with optional filtering by payment_id, ordered newest first.
    """
    stmt = (
        select(Event)
        .order_by(Event.created_at.desc(), Event.id.desc())
        .offset(offset)
        .limit(limit)
    )
    if payment_id:
        stmt = stmt.where(Event.payment_id == payment_id)

    result = await db.execute(stmt)
    return list(result.scalars().all())


# ── Risk Assessment CRUD ───────────────────────────────────────────────

async def upsert_risk_assessment(
    db: AsyncSession,
    payment_id: str,
    risk_score: float,
    risk_label: str,
    reasons: List[str],
) -> RiskAssessment:
    """
    Insert or update the risk assessment for a payment.
    Uses upsert semantics: updates if payment_id already exists.
    """
    stmt = select(RiskAssessment).where(RiskAssessment.payment_id == payment_id)
    result = await db.execute(stmt)
    assessment = result.scalar_one_or_none()

    if assessment is None:
        assessment = RiskAssessment(
            payment_id=payment_id,
            risk_score=risk_score,
            risk_label=risk_label,
            reasons=reasons,
        )
        db.add(assessment)
    else:
        assessment.risk_score = risk_score
        assessment.risk_label = risk_label
        assessment.reasons = reasons

    try:
        await db.commit()
        await db.refresh(assessment)
        return assessment
    except Exception:
        await db.rollback()
        raise


async def get_risk_assessment(
    db: AsyncSession,
    payment_id: str,
) -> Optional[RiskAssessment]:
    """Fetch the stored risk assessment for a payment, or None."""
    stmt = select(RiskAssessment).where(RiskAssessment.payment_id == payment_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_risk_assessments(
    db: AsyncSession,
    min_score: float = 0.0,
    limit: int = 100,
    offset: int = 0,
) -> List[RiskAssessment]:
    """
    List risk assessments with a minimum score threshold.
    Ordered by risk_score descending (highest risk first).
    """
    stmt = (
        select(RiskAssessment)
        .where(RiskAssessment.risk_score >= min_score)
        .order_by(RiskAssessment.risk_score.desc(), RiskAssessment.id.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_events_for_payment(
    db: AsyncSession,
    payment_id: str,
) -> List[Event]:
    """Fetch all events for a given payment, ordered chronologically (oldest first)."""
    stmt = (
        select(Event)
        .where(Event.payment_id == payment_id)
        .order_by(Event.created_at.asc(), Event.id.asc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def event_exists(
    db: AsyncSession,
    payment_id: str,
    event_type: str,
) -> bool:
    """Return True if an event with this payment_id + event_type already exists (idempotency check)."""
    from sqlalchemy import exists
    stmt = select(exists().where(
        Event.payment_id == payment_id,
        Event.event_type == event_type,
    ))
    result = await db.execute(stmt)
    return result.scalar()


async def webhook_event_exists(
    db: AsyncSession,
    webhook_event_id: str,
) -> bool:
    """Return True if an event with this Razorpay webhook_event_id already exists."""
    from sqlalchemy import exists
    stmt = select(exists().where(
        Event.webhook_event_id == webhook_event_id,
    ))
    result = await db.execute(stmt)
    return result.scalar()


# ── Recovery Attempt CRUD ──────────────────────────────────────────────

async def count_recovery_attempts(
    db: AsyncSession,
    payment_id: str,
) -> int:
    """Count total recovery attempts for a payment."""
    from sqlalchemy import func as sqlfunc
    stmt = select(sqlfunc.count()).select_from(RecoveryAttempt).where(
        RecoveryAttempt.payment_id == payment_id
    )
    result = await db.execute(stmt)
    return result.scalar() or 0


async def create_recovery_attempt(
    db: AsyncSession,
    payment_id: str,
    action: str,
    status: str,
    reason: str,
    attempt_number: int,
    amount: Optional[int] = None,
    razorpay_order_id: Optional[str] = None,
    outcome: str = "executed",
) -> RecoveryAttempt:
    """Persist a new recovery attempt."""
    attempt = RecoveryAttempt(
        payment_id=payment_id,
        action=action,
        status=status,
        reason=reason,
        attempt_number=attempt_number,
        amount=amount,
        razorpay_order_id=razorpay_order_id,
        outcome=outcome,
    )
    db.add(attempt)
    try:
        await db.commit()
        await db.refresh(attempt)
        return attempt
    except Exception:
        await db.rollback()
        raise


async def find_pending_retry_by_order_id(
    db: AsyncSession,
    razorpay_order_id: str,
) -> Optional[RecoveryAttempt]:
    """Find the pending retry attempt for a given Razorpay order ID."""
    stmt = select(RecoveryAttempt).where(
        RecoveryAttempt.razorpay_order_id == razorpay_order_id,
        RecoveryAttempt.action == "retry_payment",
        RecoveryAttempt.outcome == "pending",
    ).order_by(RecoveryAttempt.id.desc()).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def mark_recovery_recovered(
    db: AsyncSession,
    attempt_id: int,
    captured_payment_id: str,
) -> None:
    """Mark a recovery attempt as recovered and store the captured payment ID."""
    from datetime import datetime, timezone
    stmt = select(RecoveryAttempt).where(RecoveryAttempt.id == attempt_id)
    result = await db.execute(stmt)
    attempt = result.scalar_one_or_none()
    if attempt:
        attempt.outcome = "recovered"
        attempt.recovered_at = datetime.now(timezone.utc)
        # Store the captured payment_id in the reason field (avoids schema change)
        attempt.reason = f"{attempt.reason or ''} | captured={captured_payment_id}".strip(" |")
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            raise


async def get_recovery_metrics(db: AsyncSession) -> Dict[str, Any]:
    """Compute recovery metrics for the dashboard.

    Returns:
        money_at_risk:    sum of amounts from failed recovery retry attempts
                          (pending + recovered + failed — any retry_payment with an amount)
        money_recovered:  sum of amounts from retry attempts whose outcome = recovered
        recovery_rate:    money_recovered / money_at_risk (0.0 if no at-risk)
        eligible_payments: count of distinct payments with at least one retry_payment attempt
        recovered_payments: count of distinct payments that have at least one recovered attempt
    """
    # Eligible payments = distinct payment_ids with at least one retry_payment attempt
    eligible_q = await db.execute(
        select(sqlfunc.count(sqlfunc.distinct(RecoveryAttempt.payment_id))).where(
            RecoveryAttempt.action == "retry_payment",
            RecoveryAttempt.amount.isnot(None),
        )
    )
    eligible_payments = eligible_q.scalar() or 0

    # Recovered payments = distinct payment_ids with at least one outcome=recovered
    recovered_q = await db.execute(
        select(sqlfunc.count(sqlfunc.distinct(RecoveryAttempt.payment_id))).where(
            RecoveryAttempt.action == "retry_payment",
            RecoveryAttempt.outcome == "recovered",
        )
    )
    recovered_payments = recovered_q.scalar() or 0

    # money_at_risk = sum of amounts from retry attempts, counted once per payment
    # (a payment retried 3 times still contributes its original amount only once)
    subq = (
        select(
            RecoveryAttempt.payment_id,
            sqlfunc.max(RecoveryAttempt.amount).label("max_amount"),
        )
        .where(
            RecoveryAttempt.action == "retry_payment",
            RecoveryAttempt.amount.isnot(None),
        )
        .group_by(RecoveryAttempt.payment_id)
        .subquery()
    )
    at_risk_q = await db.execute(
        select(sqlfunc.coalesce(sqlfunc.sum(subq.c.max_amount), 0))
    )
    money_at_risk = at_risk_q.scalar() or 0

    # money_recovered = sum of amounts from retry attempts where outcome = recovered
    recovered_q2 = await db.execute(
        select(sqlfunc.coalesce(sqlfunc.sum(RecoveryAttempt.amount), 0)).where(
            RecoveryAttempt.action == "retry_payment",
            RecoveryAttempt.outcome == "recovered",
            RecoveryAttempt.amount.isnot(None),
        )
    )
    money_recovered = recovered_q2.scalar() or 0

    recovery_rate = (money_recovered / money_at_risk) if money_at_risk > 0 else 0.0

    return {
        "money_at_risk": money_at_risk,
        "money_recovered": money_recovered,
        "recovery_rate": round(recovery_rate, 4),
        "eligible_payments": eligible_payments,
        "recovered_payments": recovered_payments,
    }


async def get_recovery_history(
    db: AsyncSession,
    payment_id: str,
) -> List[RecoveryAttempt]:
    """Fetch all recovery attempts for a payment, newest first."""
    stmt = (
        select(RecoveryAttempt)
        .where(RecoveryAttempt.payment_id == payment_id)
        .order_by(RecoveryAttempt.created_at.desc(), RecoveryAttempt.id.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
