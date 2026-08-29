from datetime import datetime
from typing import Optional, Any, Dict, List
from pydantic import BaseModel, ConfigDict, Field

class EventCreate(BaseModel):
    payment_id: Optional[str] = Field(None, description="Associated payment ID, if applicable")
    event_type: str = Field(..., description="Type of event, e.g. PAYMENT_FAILED, RETRY_SCHEDULED")
    event_payload: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Metadata payload")


# ── Diagnosis ─────────────────────────────────────────────────────────

class DiagnosisResultResponse(BaseModel):
    """Structured diagnosis output from the constrained diagnosis layer."""
    category: str = Field(..., description="Payment situation category, e.g. bank_timeout, insufficient_funds, expired_card, card_declined, payment_failed, dispute, refund_pending, unknown")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Diagnosis confidence from 0.0 to 1.0")
    explanation: str = Field(..., description="Human-readable explanation of the diagnosis")
    supporting_event: Optional[Dict[str, Any]] = Field(None, description="The event that triggered this classification")

class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    payment_id: Optional[str] = None
    event_type: str
    event_payload: Optional[Dict[str, Any]] = None
    created_at: datetime


class RiskAssessmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    payment_id: str
    risk_score: float = Field(..., ge=0.0, le=1.0, description="Risk score from 0.0 (no risk) to 1.0 (critical)")
    risk_label: str = Field(..., description="one of: none, low, medium, high, critical")
    reasons: List[str] = Field(default_factory=list, description="Human-readable reasons for the risk score")
    assessed_at: datetime


# ── Razorpay Webhook ───────────────────────────────────────────────────

class RazorpayPaymentEntity(BaseModel):
    id: str = Field(..., description="Razorpay payment ID, e.g. pay_xxx")
    amount: Optional[int] = Field(None, description="Amount in paise")
    currency: Optional[str] = None
    status: Optional[str] = None
    order_id: Optional[str] = Field(None, description="Razorpay order ID, e.g. order_xxx")
    method: Optional[str] = None
    description: Optional[str] = None
    created_at: Optional[int] = Field(None, description="Unix timestamp")

class RazorpayWebhookPayload(BaseModel):
    """Minimal Razorpay webhook payload — captures only what we need."""
    id: Optional[str] = Field(None, description="Razorpay unique event ID, e.g. evt_xxx")
    event: str = Field(..., description="Razorpay event type, e.g. payment.captured")
    payload: Optional[Dict[str, Any]] = Field(default_factory=dict)


class WebhookResponse(BaseModel):
    status: str
    event_type: str
    payment_id: Optional[str] = None
    duplicate: bool = False
    risk_assessed: bool = False


# ── Recovery ───────────────────────────────────────────────────────────

class RecoveryAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    payment_id: str
    action: str
    status: str
    reason: Optional[str] = None
    attempt_number: int
    created_at: datetime
    amount: Optional[int] = None
    razorpay_order_id: Optional[str] = None
    outcome: str = "executed"
    recovered_at: Optional[datetime] = None
    diagnosis: Optional[DiagnosisResultResponse] = None
    next_action: Optional[str] = Field(None, description="The next executable recovery action, or a terminal state label")


# ── Recovery Metrics ───────────────────────────────────────────────────

class RecoveryMetricsResponse(BaseModel):
    money_at_risk: int = Field(..., description="Total amount in paise at risk (from retry attempts)")
    money_recovered: int = Field(..., description="Total amount in paise recovered")
    recovery_rate: float = Field(..., ge=0.0, le=1.0, description="money_recovered / money_at_risk")
    eligible_payments: int = Field(..., description="Count of payments with retry attempts")
    recovered_payments: int = Field(..., description="Count of payments actually recovered")
