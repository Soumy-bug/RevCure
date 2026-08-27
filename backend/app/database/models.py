from datetime import datetime
from typing import Optional, Any, Dict, List
from sqlalchemy import Integer, String, DateTime, Float, func, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class Event(Base):
    """
    Audit event model storing payment and recovery lifecycle events.
    """
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    payment_id: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    event_payload: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class RecoveryAttempt(Base):
    """
    Records each recovery action taken for a payment.
    Enables bounding (max attempts) and audit trail.
    """
    __tablename__ = "recovery_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    payment_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class RiskAssessment(Base):
    """
    Stores the result of a revenue-at-risk analysis for a payment.
    One row per payment_id; updated on each reassessment.
    """
    __tablename__ = "risk_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    payment_id: Mapped[str] = mapped_column(String(255), index=True, unique=True, nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    risk_label: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    reasons: Mapped[Optional[List[str]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
