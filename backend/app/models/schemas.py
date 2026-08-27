from datetime import datetime
from typing import Optional, Any, Dict, List
from pydantic import BaseModel, ConfigDict, Field

class EventCreate(BaseModel):
    payment_id: Optional[str] = Field(None, description="Associated payment ID, if applicable")
    event_type: str = Field(..., description="Type of event, e.g. PAYMENT_FAILED, RETRY_SCHEDULED")
    event_payload: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Metadata payload")

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
    method: Optional[str] = None
    description: Optional[str] = None
    created_at: Optional[int] = Field(None, description="Unix timestamp")

class RazorpayWebhookPayload(BaseModel):
    """Minimal Razorpay webhook payload — captures only what we need."""
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
