"""
Revenue-at-risk detection engine.

Evaluates a sequence of events for a payment and produces a deterministic,
explainable risk assessment (score + label + reasons).

Risk score: 0.0 (no risk) → 1.0 (critical risk).
Labels: none, low, medium, high, critical.
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RiskResult:
    risk_score: float
    risk_label: str
    reasons: List[str] = field(default_factory=list)


# ── Risk label thresholds ──────────────────────────────────────────────
LABEL_THRESHOLDS = [
    (0.8, "critical"),
    (0.6, "high"),
    (0.4, "medium"),
    (0.2, "low"),
    (0.0, "none"),
]


def _score_to_label(score: float) -> str:
    for threshold, label in LABEL_THRESHOLDS:
        if score >= threshold:
            return label
    return "none"


# ── Individual rules ───────────────────────────────────────────────────
# Each rule receives the event list (oldest-first) and returns
# (incremental_score, reason_string | None).

def _rule_payment_failed(events: List[Dict[str, Any]]) -> tuple[float, Optional[str]]:
    """Any PAYMENT_FAILED event adds risk."""
    failures = [e for e in events if e.get("event_type") == "PAYMENT_FAILED"]
    if not failures:
        return 0.0, None

    amount = failures[-1].get("event_payload", {}).get("amount")
    score = 0.3 if len(failures) == 1 else 0.4
    reason = f"Payment failed {len(failures)} time(s)"
    if amount:
        reason += f" (amount: {amount})"
    return score, reason


def _rule_multiple_failures(events: List[Dict[str, Any]]) -> tuple[float, Optional[str]]:
    """Repeated failures for the same payment amplify risk."""
    failures = [e for e in events if e.get("event_type") == "PAYMENT_FAILED"]
    if len(failures) < 2:
        return 0.0, None
    return 0.15, f"Repeated payment failures ({len(failures)} total)"


def _rule_retry_exhaustion(events: List[Dict[str, Any]]) -> tuple[float, Optional[str]]:
    """A RETRY_FAILED or RETRY_EXHAUSTED event signals no more retries left."""
    exhaustion_types = {"RETRY_FAILED", "RETRY_EXHAUSTED"}
    exhausted = [e for e in events if e.get("event_type") in exhaustion_types]
    if not exhausted:
        return 0.0, None
    return 0.3, "Payment retry attempts exhausted"


def _rule_no_success(events: List[Dict[str, Any]]) -> tuple[float, Optional[str]]:
    """If there is no PAYMENT_SUCCESS event among failure events, risk is elevated."""
    success_types = {"PAYMENT_SUCCESS", "PAYMENT_CAPTURED"}
    has_success = any(e.get("event_type") in success_types for e in events)
    failure_types = {"PAYMENT_FAILED", "RETRY_FAILED", "RETRY_EXHAUSTED"}
    has_failure = any(e.get("event_type") in failure_types for e in events)
    if has_failure and not has_success:
        return 0.1, "No successful payment recorded after failure"
    return 0.0, None


def _rule_high_amount(events: List[Dict[str, Any]]) -> tuple[float, Optional[str]]:
    """High-value payments carry more revenue-at-risk."""
    for e in reversed(events):
        payload = e.get("event_payload") or {}
        amount = payload.get("amount")
        if amount is not None:
            try:
                amount = float(amount)
            except (TypeError, ValueError):
                return 0.0, None
            if amount >= 10000:
                return 0.1, f"High-value payment at risk (amount: {int(amount)})"
    return 0.0, None


def _rule_settlement_delay(events: List[Dict[str, Any]]) -> tuple[float, Optional[str]]:
    """A SETTLEMENT_DELAYED event indicates settlement problems."""
    delayed = [e for e in events if e.get("event_type") == "SETTLEMENT_DELAYED"]
    if not delayed:
        return 0.0, None
    return 0.2, "Settlement delayed"


def _rule_refund_dispute(events: List[Dict[str, Any]]) -> tuple[float, Optional[str]]:
    """REFUND_PENDING or DISPUTE_OPENED events add risk."""
    dispute_types = {"REFUND_PENDING", "DISPUTE_OPENED", "CHARGEBACK"}
    disputes = [e for e in events if e.get("event_type") in dispute_types]
    if not disputes:
        return 0.0, None
    return 0.25, f"Active dispute or refund ({disputes[-1].get('event_type')})"


# ── Engine ─────────────────────────────────────────────────────────────

ALL_RULES = [
    _rule_payment_failed,
    _rule_multiple_failures,
    _rule_retry_exhaustion,
    _rule_no_success,
    _rule_high_amount,
    _rule_settlement_delay,
    _rule_refund_dispute,
]


def assess_risk(events: List[Dict[str, Any]]) -> RiskResult:
    """
    Evaluate a list of payment events and return a risk assessment.

    Args:
        events: List of event dicts, each with at least 'event_type'.
                Optionally includes 'event_payload' and 'created_at'.
                Order is chronological (oldest first).

    Returns:
        RiskResult with risk_score (0.0–1.0), risk_label, and reasons.
    """
    if not events:
        return RiskResult(risk_score=0.0, risk_label="none", reasons=[])

    total_score = 0.0
    reasons: List[str] = []

    for rule in ALL_RULES:
        score, reason = rule(events)
        if score > 0:
            total_score += score
            if reason:
                reasons.append(reason)

    # Clamp to [0.0, 1.0]
    total_score = min(max(total_score, 0.0), 1.0)
    label = _score_to_label(total_score)

    return RiskResult(
        risk_score=round(total_score, 4),
        risk_label=label,
        reasons=reasons,
    )
