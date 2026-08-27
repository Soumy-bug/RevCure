"""
Deterministic payment diagnosis layer.

Classifies a payment situation into a fixed category based on event
payloads and error codes.  This is a pure, local function — no external
AI API calls, no ML models, no network requests.

The diagnosis explains/contexts the situation.  It NEVER executes actions
or bypasses the deterministic recovery policy (decide_action).
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


# ── Fixed diagnosis categories ────────────────────────────────────────
DIAGNOSIS_CATEGORIES = (
    "bank_timeout",
    "insufficient_funds",
    "expired_card",
    "card_declined",
    "payment_failed",
    "dispute",
    "refund_pending",
    "unknown",
)


@dataclass
class DiagnosisResult:
    """Structured output from the diagnosis layer."""
    category: str
    confidence: float  # 0.0 – 1.0
    explanation: str
    supporting_event: Optional[Dict[str, Any]] = None


# ── Razorpay error code → category mapping ────────────────────────────
# Based on Razorpay's standard error codes documented at:
# https://razorpay.com/docs/payments/payments/failure-modes/
_ERROR_CODE_MAP: Dict[str, tuple[str, float]] = {
    # Network / gateway issues
    "gateway_timeout":          ("bank_timeout", 0.9),
    "gateway_error":            ("bank_timeout", 0.8),
    "network_error":            ("bank_timeout", 0.8),
    "bank_timeout":             ("bank_timeout", 0.9),

    # Insufficient funds
    "insufficient_funds":       ("insufficient_funds", 0.95),
    "insufficient_balance":     ("insufficient_funds", 0.9),
    "wallet_balance_low":       ("insufficient_funds", 0.85),

    # Card issues
    "card_expired":             ("expired_card", 0.95),
    "expired_card":             ("expired_card", 0.95),

    # Card declines
    "do_not_honor":             ("card_declined", 0.7),
    "card_declined":            ("card_declined", 0.8),
    "lost_card":                ("card_declined", 0.85),
    "stolen_card":              ("card_declined", 0.85),
    "suspected_fraud":          ("card_declined", 0.8),
    "transaction_not_allowed":  ("card_declined", 0.7),
    " insufficient_funds":      ("insufficient_funds", 0.85),
}


def _extract_error_info(event: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Extract error_code and error_description from an event's payload."""
    payload = event.get("event_payload") or {}
    error_code = payload.get("error_code")
    error_desc = payload.get("error_description")
    return error_code, error_desc


def _match_by_error_code(error_code: Optional[str]) -> Optional[tuple[str, float]]:
    """Look up category by Razorpay error code."""
    if not error_code:
        return None
    code_lower = error_code.lower().strip()
    return _ERROR_CODE_MAP.get(code_lower)


def _match_by_event_type(events: List[Dict[str, Any]]) -> Optional[tuple[str, float]]:
    """Classify based on event types when no error code is available."""
    event_types = [e.get("event_type") for e in events]

    if "DISPUTE_OPENED" in event_types or "CHARGEBACK" in event_types:
        return ("dispute", 0.85)
    if "REFUND_PENDING" in event_types:
        return ("refund_pending", 0.7)
    if "PAYMENT_FAILED" in event_types:
        return ("payment_failed", 0.5)

    return None


def diagnose(
    events: List[Dict[str, Any]],
    risk_score: float = 0.0,
    risk_label: str = "none",
) -> DiagnosisResult:
    """
    Produce a deterministic diagnosis for a payment's event history.

    This function is pure — same inputs always produce the same output.
    It reads event payloads and event types to classify the situation.

    Args:
        events:      chronological event list (oldest first)
        risk_score:  0.0–1.0 from risk assessment (used for confidence boost)
        risk_label:  none/low/medium/high/critical (used for confidence boost)

    Returns:
        DiagnosisResult with category, confidence, explanation, and
        the supporting event that triggered the classification.
    """
    if not events:
        return DiagnosisResult(
            category="unknown",
            confidence=0.0,
            explanation="No events recorded for this payment",
        )

    # ── Check for disputes/refunds first (regardless of success) ──────
    event_types = {e.get("event_type") for e in events}
    if "DISPUTE_OPENED" in event_types or "CHARGEBACK" in event_types:
        supporting = None
        for e in reversed(events):
            if e.get("event_type") in ("DISPUTE_OPENED", "CHARGEBACK"):
                supporting = e
                break
        return DiagnosisResult(
            category="dispute",
            confidence=0.85,
            explanation="Active dispute or chargeback detected",
            supporting_event=supporting,
        )
    if "REFUND_PENDING" in event_types:
        supporting = None
        for e in reversed(events):
            if e.get("event_type") == "REFUND_PENDING":
                supporting = e
                break
        return DiagnosisResult(
            category="refund_pending",
            confidence=0.7,
            explanation="Refund pending — awaiting processing",
            supporting_event=supporting,
        )

    # ── Check for successful payment (no failures) ────────────────────
    success_types = {"PAYMENT_SUCCESS", "PAYMENT_CAPTURED"}
    has_success = any(e.get("event_type") in success_types for e in events)
    failure_types = {"PAYMENT_FAILED", "RETRY_FAILED", "RETRY_EXHAUSTED"}
    has_failure = any(e.get("event_type") in failure_types for e in events)

    if has_success and not has_failure:
        return DiagnosisResult(
            category="payment_failed",
            confidence=0.0,
            explanation="Payment succeeded — no failure diagnosed",
        )

    # ── Scan PAYMENT_FAILED events for error_code ─────────────────────
    failed_events = [e for e in events if e.get("event_type") == "PAYMENT_FAILED"]
    for event in failed_events:
        error_code, error_desc = _extract_error_info(event)
        match = _match_by_error_code(error_code)
        if match:
            category, base_confidence = match
            # Boost confidence slightly if risk is high
            confidence = min(base_confidence + (risk_score * 0.05), 1.0)
            payload = event.get("event_payload") or {}
            explanation = (
                f"Detected {category.replace('_', ' ')} from error code "
                f"'{error_code}' (payment failed event)"
            )
            if error_desc:
                explanation += f": {error_desc}"
            return DiagnosisResult(
                category=category,
                confidence=round(confidence, 2),
                explanation=explanation,
                supporting_event=event,
            )

    # ── Scan RETRY_FAILED / RETRY_EXHAUSTED events ────────────────────
    retry_events = [e for e in events if e.get("event_type") in ("RETRY_FAILED", "RETRY_EXHAUSTED")]
    for event in retry_events:
        error_code, error_desc = _extract_error_info(event)
        match = _match_by_error_code(error_code)
        if match:
            category, base_confidence = match
            confidence = min(base_confidence + (risk_score * 0.05), 1.0)
            explanation = (
                f"Detected {category.replace('_', ' ')} from error code "
                f"'{error_code}' (retry exhausted event)"
            )
            if error_desc:
                explanation += f": {error_desc}"
            return DiagnosisResult(
                category=category,
                confidence=round(confidence, 2),
                explanation=explanation,
                supporting_event=event,
            )

    # ── Fallback: classify by event types ─────────────────────────────
    event_type_match = _match_by_event_type(events)
    if event_type_match:
        category, base_confidence = event_type_match
        confidence = min(base_confidence + (risk_score * 0.05), 1.0)
        # Find the most relevant supporting event
        supporting = None
        for e in reversed(events):
            if e.get("event_type") in {
                "DISPUTE_OPENED", "CHARGEBACK", "REFUND_PENDING",
                "PAYMENT_FAILED", "RETRY_FAILED", "RETRY_EXHAUSTED",
            }:
                supporting = e
                break

        explanation = f"Classified as {category.replace('_', ' ')} based on event types present"
        if category == "payment_failed":
            explanation = "Payment failure detected — no specific error code available"

        return DiagnosisResult(
            category=category,
            confidence=round(confidence, 2),
            explanation=explanation,
            supporting_event=supporting,
        )

    # ── Unknown ───────────────────────────────────────────────────────
    return DiagnosisResult(
        category="unknown",
        confidence=0.1,
        explanation="No matching diagnosis pattern found in event history",
    )
