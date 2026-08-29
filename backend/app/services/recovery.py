"""
Bounded, deterministic recovery workflow.

Given a payment_id, its events, and risk assessment, decides the next
intervention and records the attempt.  Hard limits prevent infinite retries.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


# ── Bounding constants ─────────────────────────────────────────────────
MAX_RETRY_ATTEMPTS = 3
MAX_TOTAL_ACTIONS = 5

# Actions the engine can select
ACTION_RETRY = "retry_payment"
ACTION_REMIND = "send_reminder"
ACTION_ESCALATE = "escalate"
ACTION_NONE = "no_action"


@dataclass
class RecoveryDecision:
    action: str
    reason: str
    attempt_number: int
    bounded: bool = False


def _count_event_type(events: List[Dict[str, Any]], event_type: str) -> int:
    return sum(1 for e in events if e.get("event_type") == event_type)


def _has_event_type(events: List[Dict[str, Any]], *types: str) -> bool:
    return any(e.get("event_type") in types for e in events)


def _latest_payload(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    for e in reversed(events):
        if e.get("event_payload"):
            return e["event_payload"]
    return {}


def decide_action(
    events: List[Dict[str, Any]],
    risk_score: float,
    risk_label: str,
    previous_attempts: int,
) -> RecoveryDecision:
    """
    Pure, deterministic decision function.

    Args:
        events:            chronological event list for the payment
        risk_score:        0.0 – 1.0 from RiskAssessment
        risk_label:        none / low / medium / high / critical
        previous_attempts: total RecoveryAttempt rows for this payment

    Returns:
        RecoveryDecision with action, reason, attempt_number, bounded flag.
    """

    # ── Bounding: hard stop ───────────────────────────────────────────
    if previous_attempts >= MAX_TOTAL_ACTIONS:
        return RecoveryDecision(
            action=ACTION_NONE,
            reason=f"Hard limit reached: {previous_attempts}/{MAX_TOTAL_ACTIONS} recovery actions already taken",
            attempt_number=previous_attempts + 1,
            bounded=True,
        )

    next_attempt = previous_attempts + 1

    # ── Dispute / chargeback → always escalate (before success check) ──
    # Active disputes and refunds must be handled regardless of payment
    # status — a payment that "succeeded" but has an open dispute still
    # needs human review.
    if _has_event_type(events, "DISPUTE_OPENED", "CHARGEBACK"):
        return RecoveryDecision(
            action=ACTION_ESCALATE,
            reason="Active dispute or chargeback requires human review",
            attempt_number=next_attempt,
        )

    # ── Refund pending → remind customer ──────────────────────────────
    if _has_event_type(events, "REFUND_PENDING"):
        return RecoveryDecision(
            action=ACTION_REMIND,
            reason="Refund pending — send customer reminder/confirmation",
            attempt_number=next_attempt,
        )

    # ── No risk / already succeeded ───────────────────────────────────
    if risk_label == "none":
        return RecoveryDecision(
            action=ACTION_NONE,
            reason="No risk detected — payment is healthy",
            attempt_number=next_attempt,
        )

    if _has_event_type(events, "PAYMENT_SUCCESS", "PAYMENT_CAPTURED"):
        return RecoveryDecision(
            action=ACTION_NONE,
            reason="Payment already succeeded — no recovery needed",
            attempt_number=next_attempt,
        )

    # ── Critical risk → escalate immediately ──────────────────────────
    if risk_label == "critical":
        return RecoveryDecision(
            action=ACTION_ESCALATE,
            reason=f"Risk is critical (score={risk_score:.2f}) — escalate for manual intervention",
            attempt_number=next_attempt,
        )

    # ── Payment failed logic ──────────────────────────────────────────
    failure_count = _count_event_type(events, "PAYMENT_FAILED")
    retry_exhausted = _has_event_type(events, "RETRY_FAILED", "RETRY_EXHAUSTED")

    if retry_exhausted:
        # Retries already exhausted at Razorpay level → escalate
        if previous_attempts >= MAX_RETRY_ATTEMPTS:
            return RecoveryDecision(
                action=ACTION_ESCALATE,
                reason=f"Retry attempts exhausted and recovery limit reached ({previous_attempts}/{MAX_RETRY_ATTEMPTS})",
                attempt_number=next_attempt,
            )
        return RecoveryDecision(
            action=ACTION_ESCALATE,
            reason="Payment retry attempts exhausted at gateway level",
            attempt_number=next_attempt,
        )

    if failure_count > 0:
        # Enough retry budget left?
        retry_attempts = _count_event_type(events, "RETRY_PAYMENT")
        remaining_retries = MAX_RETRY_ATTEMPTS - retry_attempts

        if remaining_retries > 0 and previous_attempts < MAX_RETRY_ATTEMPTS:
            return RecoveryDecision(
                action=ACTION_RETRY,
                reason=f"Payment failed {failure_count} time(s) — retry (attempt {next_attempt}/{MAX_TOTAL_ACTIONS})",
                attempt_number=next_attempt,
            )
        else:
            return RecoveryDecision(
                action=ACTION_ESCALATE,
                reason=f"No retry budget left ({retry_attempts} retries done, {previous_attempts} total actions)",
                attempt_number=next_attempt,
            )

    # ── High amount at risk → remind ──────────────────────────────────
    amount = _latest_payload(events).get("amount")
    if amount is not None:
        try:
            if float(amount) >= 10000:
                return RecoveryDecision(
                    action=ACTION_REMIND,
                    reason=f"High-value payment (amount={int(float(amount))}) at risk — send reminder",
                    attempt_number=next_attempt,
                )
        except (TypeError, ValueError):
            pass

    # ── Fallback: medium/high risk with no specific trigger → remind ──
    if risk_label in ("medium", "high"):
        return RecoveryDecision(
            action=ACTION_REMIND,
            reason=f"Risk level is {risk_label} (score={risk_score:.2f}) — send recovery reminder",
            attempt_number=next_attempt,
        )

    # ── Low risk → no action ──────────────────────────────────────────
    return RecoveryDecision(
        action=ACTION_NONE,
        reason=f"Risk is low (score={risk_score:.2f}) — monitoring only",
        attempt_number=next_attempt,
    )
