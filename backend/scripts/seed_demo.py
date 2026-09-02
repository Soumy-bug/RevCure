import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# Ensure the backend package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.database.models import Base, Event, RiskAssessment, RecoveryAttempt
from app.database.connection import _normalize_async_url


# ── Demo data prefix ───────────────────────────────────────────────────
DEMO_PREFIX = "pay_demo_"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ago(minutes: int) -> datetime:
    return _now() - timedelta(minutes=minutes)


# ── Scenario definitions ───────────────────────────────────────────────
# Each scenario is a dict with:
#   payment_id, events (list of event dicts), risk (optional), recovery (optional)

SCENARIOS = [
    # ──────────────────────────────────────────────────────────────────
    # Scenario 1: Successful payment - no risk
    # -----------------------------------------
    {
        "payment_id": f"{DEMO_PREFIX}success_001",
        "description": "Successful payment - no risk",
        "events": [
            {
                "event_type": "PAYMENT_AUTHORIZED",
                "event_payload": {
                    "id": f"{DEMO_PREFIX}success_001",
                    "amount": 4999,
                    "currency": "INR",
                    "method": "card",
                    "status": "authorized",
                    "description": "Order #1001 — Premium subscription",
                },
                "created_at": _ago(120),
            },
            {
                "event_type": "PAYMENT_SUCCESS",
                "event_payload": {
                    "id": f"{DEMO_PREFIX}success_001",
                    "amount": 4999,
                    "currency": "INR",
                    "method": "card",
                    "status": "captured",
                    "description": "Order #1001 — Premium subscription",
                },
                "created_at": _ago(119),
            },
            {
                "event_type": "SETTLEMENT_PROCESSED",
                "event_payload": {
                    "id": f"{DEMO_PREFIX}success_001",
                    "amount": 4999,
                    "currency": "INR",
                    "status": "settled",
                },
                "created_at": _ago(60),
            },
        ],
        "risk": {
            "risk_score": 0.0,
            "risk_label": "none",
            "reasons": [],
        },
        "recovery": [],
    },

    # ──────────────────────────────────────────────────────────────────
    # Scenario 2: Failed payment -> retry -> payment captured -> RECOVERED
    # ---------------------------------------------------------------
    {
        "payment_id": f"{DEMO_PREFIX}retry_001",
        "description": "Failed payment -> retry -> captured -> recovered",
        "events": [
            {
                "event_type": "PAYMENT_FAILED",
                "event_payload": {
                    "id": f"{DEMO_PREFIX}retry_001",
                    "amount": 12500,
                    "currency": "INR",
                    "method": "upi",
                    "status": "failed",
                    "error_code": "gateway_timeout",
                    "error_description": "The bank took too long to respond",
                    "description": "Order #1002 — Annual plan",
                },
                "created_at": _ago(90),
            },
            {
                "event_type": "RETRY_PAYMENT",
                "event_payload": {
                    "action": "retry_payment",
                    "reason": "Payment failed 1 time(s) — retry (attempt 1/5)",
                    "attempt_number": 1,
                    "risk_score": 0.4,
                    "risk_label": "medium",
                    "final_status": "executed",
                    "razorpay_order": {"success": True, "order_id": "order_demo_retry_001"},
                },
                "created_at": _ago(85),
            },
            {
                "event_type": "PAYMENT_SUCCESS",
                "event_payload": {
                    "id": f"{DEMO_PREFIX}retry_002",
                    "amount": 12500,
                    "currency": "INR",
                    "method": "upi",
                    "status": "captured",
                    "order_id": "order_demo_retry_001",
                    "description": "Retry order #1002 captured",
                },
                "created_at": _ago(80),
            },
            {
                "event_type": "RECOVERY_SUCCEEDED",
                "event_payload": {
                    "captured_payment_id": f"{DEMO_PREFIX}retry_002",
                    "order_id": "order_demo_retry_001",
                    "amount": 12500,
                    "recovery_attempt_id": 1,
                },
                "created_at": _ago(80),
            },
        ],
        "risk": {
            "risk_score": 0.0,
            "risk_label": "none",
            "reasons": [],
        },
        "recovery": [
            {
                "action": "retry_payment",
                "status": "executed",
                "reason": "Payment failed 1 time(s) — retry (attempt 1/5)",
                "attempt_number": 1,
                "created_at": _ago(85),
                "amount": 12500,
                "razorpay_order_id": "order_demo_retry_001",
                "outcome": "recovered",
                "recovered_at": _ago(80),
            },
        ],
    },

    # ──────────────────────────────────────────────────────────────────
    # Scenario 3: Repeated failures -> high risk -> retry -> escalation
    # ----------------------------------------------------------------
    {
        "payment_id": f"{DEMO_PREFIX}escalate_001",
        "description": "Repeated failures -> bounded retries -> escalation (1 recovered)",
        "events": [
            {
                "event_type": "PAYMENT_FAILED",
                "event_payload": {
                    "id": f"{DEMO_PREFIX}escalate_001",
                    "amount": 25000,
                    "currency": "INR",
                    "method": "card",
                    "status": "failed",
                    "error_code": "card_expired",
                    "error_description": "Card has expired",
                    "description": "Order #1003 — Enterprise quarterly",
                },
                "created_at": _ago(300),
            },
            {
                "event_type": "RETRY_PAYMENT",
                "event_payload": {
                    "action": "retry_payment",
                    "reason": "Payment failed 1 time(s) — retry (attempt 1/5)",
                    "attempt_number": 1,
                    "risk_score": 0.4,
                    "risk_label": "medium",
                    "final_status": "executed",
                    "razorpay_order": {"success": True, "order_id": "order_demo_esc_001"},
                },
                "created_at": _ago(280),
            },
            {
                "event_type": "PAYMENT_SUCCESS",
                "event_payload": {
                    "id": f"{DEMO_PREFIX}escalate_001b",
                    "amount": 25000,
                    "currency": "INR",
                    "method": "card",
                    "status": "captured",
                    "order_id": "order_demo_esc_001",
                    "description": "Retry order #1003 captured",
                },
                "created_at": _ago(270),
            },
            {
                "event_type": "RECOVERY_SUCCEEDED",
                "event_payload": {
                    "captured_payment_id": f"{DEMO_PREFIX}escalate_001b",
                    "order_id": "order_demo_esc_001",
                    "amount": 25000,
                    "recovery_attempt_id": 1,
                },
                "created_at": _ago(270),
            },
            {
                "event_type": "PAYMENT_FAILED",
                "event_payload": {
                    "id": f"{DEMO_PREFIX}escalate_001",
                    "amount": 25000,
                    "currency": "INR",
                    "method": "card",
                    "status": "failed",
                    "error_code": "card_expired",
                    "error_description": "Card has expired",
                    "description": "Order #1003 — Enterprise quarterly",
                },
                "created_at": _ago(240),
            },
            {
                "event_type": "RETRY_PAYMENT",
                "event_payload": {
                    "action": "retry_payment",
                    "reason": "Payment failed 2 time(s) — retry (attempt 2/5)",
                    "attempt_number": 2,
                    "risk_score": 0.55,
                    "risk_label": "high",
                    "final_status": "executed",
                    "razorpay_order": {"success": True, "order_id": "order_demo_esc_002"},
                },
                "created_at": _ago(220),
            },
            {
                "event_type": "PAYMENT_FAILED",
                "event_payload": {
                    "id": f"{DEMO_PREFIX}escalate_001",
                    "amount": 25000,
                    "currency": "INR",
                    "method": "card",
                    "status": "failed",
                    "error_code": "card_expired",
                    "error_description": "Card has expired",
                    "description": "Order #1003 — Enterprise quarterly",
                },
                "created_at": _ago(180),
            },
            {
                "event_type": "RETRY_PAYMENT",
                "event_payload": {
                    "action": "retry_payment",
                    "reason": "Payment failed 3 time(s) — retry (attempt 3/5)",
                    "attempt_number": 3,
                    "risk_score": 0.7,
                    "risk_label": "high",
                    "final_status": "executed",
                    "razorpay_order": {"success": True, "order_id": "order_demo_esc_003"},
                },
                "created_at": _ago(160),
            },
            {
                "event_type": "ESCALATED",
                "event_payload": {
                    "action": "escalate",
                    "reason": "No retry budget left (3 retries done, 3 total actions)",
                    "attempt_number": 4,
                    "risk_score": 0.7,
                    "risk_label": "high",
                    "final_status": "executed",
                },
                "created_at": _ago(150),
            },
        ],
        "risk": {
            "risk_score": 0.7,
            "risk_label": "high",
            "reasons": [
                "Payment failed 3 time(s) (amount: 25000)",
                "Repeated payment failures (3 total)",
                "No successful payment recorded after failure",
                "High-value payment at risk (amount: 25000)",
            ],
        },
        "recovery": [
            {
                "action": "retry_payment",
                "status": "executed",
                "reason": "Payment failed 1 time(s) — retry (attempt 1/5)",
                "attempt_number": 1,
                "created_at": _ago(280),
                "amount": 25000,
                "razorpay_order_id": "order_demo_esc_001",
                "outcome": "recovered",
                "recovered_at": _ago(270),
            },
            {
                "action": "retry_payment",
                "status": "executed",
                "reason": "Payment failed 2 time(s) — retry (attempt 2/5)",
                "attempt_number": 2,
                "created_at": _ago(220),
                "amount": 25000,
                "razorpay_order_id": "order_demo_esc_002",
                "outcome": "pending",
            },
            {
                "action": "retry_payment",
                "status": "executed",
                "reason": "Payment failed 3 time(s) — retry (attempt 3/5)",
                "attempt_number": 3,
                "created_at": _ago(160),
                "amount": 25000,
                "razorpay_order_id": "order_demo_esc_003",
                "outcome": "failed",
            },
            {
                "action": "escalate",
                "status": "executed",
                "reason": "No retry budget left (3 retries done, 3 total actions)",
                "attempt_number": 4,
                "created_at": _ago(150),
            },
        ],
    },

    # ──────────────────────────────────────────────────────────────────
    # Scenario 4: Refund + dispute -> escalation
    # -------------------------------------------
    {
        "payment_id": f"{DEMO_PREFIX}dispute_001",
        "description": "Refund pending + dispute opened -> escalation",
        "events": [
            {
                "event_type": "PAYMENT_SUCCESS",
                "event_payload": {
                    "id": f"{DEMO_PREFIX}dispute_001",
                    "amount": 8999,
                    "currency": "INR",
                    "method": "card",
                    "status": "captured",
                    "description": "Order #1004 — Pro annual",
                },
                "created_at": _ago(1440),
            },
            {
                "event_type": "REFUND_PENDING",
                "event_payload": {
                    "id": f"{DEMO_PREFIX}dispute_001",
                    "amount": 8999,
                    "currency": "INR",
                    "status": "refund_pending",
                    "reason": "customer_complaint",
                },
                "created_at": _ago(720),
            },
            {
                "event_type": "DISPUTE_OPENED",
                "event_payload": {
                    "id": f"{DEMO_PREFIX}dispute_001",
                    "amount": 8999,
                    "currency": "INR",
                    "status": "opened",
                    "reason": "product_not_received",
                    "dispute_id": "dsp_demo_001",
                },
                "created_at": _ago(600),
            },
        ],
        "risk": {
            "risk_score": 0.65,
            "risk_label": "high",
            "reasons": [
                "Active dispute or refund (DISPUTE_OPENED)",
            ],
        },
        "recovery": [
            {
                "action": "escalate",
                "status": "executed",
                "reason": "Active dispute or chargeback requires human review",
                "attempt_number": 1,
                "created_at": _ago(590),
            },
        ],
    },

    # ──────────────────────────────────────────────────────────────────
    # Scenario 5: High-value failed payment -> critical risk -> reminder then escalation
    # ---------------------------------------------------------------------------------
    {
        "payment_id": f"{DEMO_PREFIX}critical_001",
        "description": "High-value failure -> critical risk -> reminder then escalation",
        "events": [
            {
                "event_type": "PAYMENT_FAILED",
                "event_payload": {
                    "id": f"{DEMO_PREFIX}critical_001",
                    "amount": 99999,
                    "currency": "INR",
                    "method": "netbanking",
                    "status": "failed",
                    "error_code": "insufficient_funds",
                    "error_description": "Insufficient funds in account",
                    "description": "Order #1005 — Enterprise annual",
                },
                "created_at": _ago(500),
            },
            {
                "event_type": "PAYMENT_FAILED",
                "event_payload": {
                    "id": f"{DEMO_PREFIX}critical_001",
                    "amount": 99999,
                    "currency": "INR",
                    "method": "netbanking",
                    "status": "failed",
                    "error_code": "insufficient_funds",
                    "error_description": "Insufficient funds in account",
                    "description": "Order #1005 — Enterprise annual",
                },
                "created_at": _ago(400),
            },
        ],
        "risk": {
            "risk_score": 0.85,
            "risk_label": "critical",
            "reasons": [
                "Payment failed 2 time(s) (amount: 99999)",
                "Repeated payment failures (2 total)",
                "No successful payment recorded after failure",
                "High-value payment at risk (amount: 99999)",
            ],
        },
        "recovery": [
            {
                "action": "send_reminder",
                "status": "executed",
                "reason": "Risk level is critical (score=0.85) — send recovery reminder",
                "attempt_number": 1,
                "created_at": _ago(380),
            },
            {
                "action": "escalate",
                "status": "executed",
                "reason": "Risk is critical (score=0.85) — escalate for manual intervention",
                "attempt_number": 2,
                "created_at": _ago(350),
            },
        ],
    },

    # ──────────────────────────────────────────────────────────────────
    # Scenario 6: Low-risk monitoring — minor failure, auto-resolved
    # ──────────────────────────────────────────────────────────────────
    {
        "payment_id": f"{DEMO_PREFIX}lowrisk_001",
        "description": "Low-risk - single failure resolved, monitoring",
        "events": [
            {
                "event_type": "PAYMENT_FAILED",
                "event_payload": {
                    "id": f"{DEMO_PREFIX}lowrisk_001",
                    "amount": 1999,
                    "currency": "INR",
                    "method": "wallet",
                    "status": "failed",
                    "error_code": "wallet_balance_low",
                    "error_description": "Wallet balance insufficient",
                    "description": "Order #1006 — Monthly starter",
                },
                "created_at": _ago(60),
            },
        ],
        "risk": {
            "risk_score": 0.4,
            "risk_label": "medium",
            "reasons": [
                "Payment failed 1 time(s) (amount: 1999)",
                "No successful payment recorded after failure",
            ],
        },
        "recovery": [
            {
                "action": "retry_payment",
                "status": "executed",
                "reason": "Payment failed 1 time(s) — retry (attempt 1/5)",
                "attempt_number": 1,
                "created_at": _ago(55),
            },
        ],
    },

    # ──────────────────────────────────────────────────────────────────
    # Scenario 7: Retry exhaustion -> hard escalation
    # ------------------------------------------------
    {
        "payment_id": f"{DEMO_PREFIX}exhaust_001",
        "description": "Retry exhausted -> hard escalation",
        "events": [
            {
                "event_type": "PAYMENT_FAILED",
                "event_payload": {
                    "id": f"{DEMO_PREFIX}exhaust_001",
                    "amount": 15000,
                    "currency": "INR",
                    "method": "card",
                    "status": "failed",
                    "error_code": "do_not_honor",
                    "error_description": "Card issuer declined the transaction",
                    "description": "Order #1007 — Team plan",
                },
                "created_at": _ago(1000),
            },
            {
                "event_type": "RETRY_FAILED",
                "event_payload": {
                    "id": f"{DEMO_PREFIX}exhaust_001",
                    "amount": 15000,
                    "currency": "INR",
                    "status": "failed",
                    "reason": "Card issuer declined retry",
                },
                "created_at": _ago(800),
            },
        ],
        "risk": {
            "risk_score": 0.75,
            "risk_label": "high",
            "reasons": [
                "Payment failed 1 time(s) (amount: 15000)",
                "Payment retry attempts exhausted",
                "No successful payment recorded after failure",
                "High-value payment at risk (amount: 15000)",
            ],
        },
        "recovery": [
            {
                "action": "escalate",
                "status": "executed",
                "reason": "Payment retry attempts exhausted at gateway level",
                "attempt_number": 1,
                "created_at": _ago(790),
            },
        ],
    },

    # ──────────────────────────────────────────────────────────────────
    # Scenario 8: Refund pending only -> medium risk -> send reminder
    # ---------------------------------------------------------------
    {
        "payment_id": f"{DEMO_PREFIX}refund_001",
        "description": "Refund pending -> send reminder",
        "events": [
            {
                "event_type": "PAYMENT_SUCCESS",
                "event_payload": {
                    "id": f"{DEMO_PREFIX}refund_001",
                    "amount": 3499,
                    "currency": "INR",
                    "method": "card",
                    "status": "captured",
                    "description": "Order #1008 — Business monthly",
                },
                "created_at": _ago(2000),
            },
            {
                "event_type": "REFUND_PENDING",
                "event_payload": {
                    "id": f"{DEMO_PREFIX}refund_001",
                    "amount": 3499,
                    "currency": "INR",
                    "status": "refund_pending",
                    "reason": "customer_request",
                },
                "created_at": _ago(100),
            },
        ],
        "risk": {
            "risk_score": 0.25,
            "risk_label": "low",
            "reasons": [
                "Active dispute or refund (REFUND_PENDING)",
            ],
        },
        "recovery": [
            {
                "action": "send_reminder",
                "status": "executed",
                "reason": "Refund pending — send customer reminder/confirmation",
                "attempt_number": 1,
                "created_at": _ago(90),
            },
        ],
    },
]


async def seed():
    """Seed demo data into the database."""
    url = _normalize_async_url(settings.DATABASE_URL)
    engine = create_async_engine(url, echo=False, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Check connectivity
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    print("[seed] Connected to database.")

    async with session_factory() as db:
        # ── Check what already exists ─────────────────────────────────
        existing_events = await db.execute(
            select(Event.payment_id).where(
                Event.payment_id.like(f"{DEMO_PREFIX}%")
            ).distinct()
        )
        existing_pids = {row[0] for row in existing_events.fetchall()}

        existing_risks = await db.execute(
            select(RiskAssessment.payment_id).where(
                RiskAssessment.payment_id.like(f"{DEMO_PREFIX}%")
            )
        )
        existing_risk_pids = {row[0] for row in existing_risks.fetchall()}

        existing_recoveries = await db.execute(
            select(RecoveryAttempt.payment_id).where(
                RecoveryAttempt.payment_id.like(f"{DEMO_PREFIX}%")
            ).distinct()
        )
        existing_recovery_pids = {row[0] for row in existing_recoveries.fetchall()}

        created_events = 0
        created_risks = 0
        created_recoveries = 0
        skipped = 0

        for scenario in SCENARIOS:
            pid = scenario["payment_id"]

            # ── Insert events ──────────────────────────────────────────
            if pid not in existing_pids:
                for ev in scenario["events"]:
                    event = Event(
                        payment_id=pid,
                        event_type=ev["event_type"],
                        event_payload=ev.get("event_payload"),
                        created_at=ev.get("created_at", _now()),
                    )
                    db.add(event)
                    created_events += 1
                await db.commit()
            else:
                skipped += len(scenario["events"])

            # ── Insert risk assessment ─────────────────────────────────
            risk = scenario.get("risk")
            if risk and pid not in existing_risk_pids:
                ra = RiskAssessment(
                    payment_id=pid,
                    risk_score=risk["risk_score"],
                    risk_label=risk["risk_label"],
                    reasons=risk.get("reasons", []),
                    assessed_at=_ago(5),
                )
                db.add(ra)
                await db.commit()
                created_risks += 1
            elif risk:
                skipped += 1

            # ── Insert recovery attempts ───────────────────────────────
            recoveries = scenario.get("recovery", [])
            if recoveries and pid not in existing_recovery_pids:
                for rec in recoveries:
                    ra = RecoveryAttempt(
                        payment_id=pid,
                        action=rec["action"],
                        status=rec["status"],
                        reason=rec.get("reason"),
                        attempt_number=rec["attempt_number"],
                        created_at=rec.get("created_at", _now()),
                        amount=rec.get("amount"),
                        razorpay_order_id=rec.get("razorpay_order_id"),
                        outcome=rec.get("outcome", "executed"),
                        recovered_at=rec.get("recovered_at"),
                    )
                    db.add(ra)
                    created_recoveries += 1
                await db.commit()
            elif recoveries:
                skipped += len(recoveries)

    await engine.dispose()

    # ── Summary ───────────────────────────────────────────────────────
    print(f"\n[seed] Demo data seeded successfully!")
    print(f"  Scenarios:   {len(SCENARIOS)}")
    print(f"  Events:      {created_events} created, {skipped} skipped (already exist)")
    print(f"  Risks:       {created_risks} created")
    print(f"  Recoveries:  {created_recoveries} created")
    print(f"\n  Payment IDs (all prefixed with '{DEMO_PREFIX}'):")
    for s in SCENARIOS:
        print(f"    - {s['payment_id']}: {s['description']}")


async def reset():
    """Remove all demo data from the database."""
    url = _normalize_async_url(settings.DATABASE_URL)
    engine = create_async_engine(url, echo=False, pool_pre_ping=True)

    async with engine.begin() as conn:
        # Delete in order: recovery_attempts first (FK), then risk, then events
        result = await conn.execute(
            text("DELETE FROM recovery_attempts WHERE payment_id LIKE :prefix"),
            {"prefix": f"{DEMO_PREFIX}%"},
        )
        deleted_recoveries = result.rowcount

        result = await conn.execute(
            text("DELETE FROM risk_assessments WHERE payment_id LIKE :prefix"),
            {"prefix": f"{DEMO_PREFIX}%"},
        )
        deleted_risks = result.rowcount

        result = await conn.execute(
            text("DELETE FROM events WHERE payment_id LIKE :prefix"),
            {"prefix": f"{DEMO_PREFIX}%"},
        )
        deleted_events = result.rowcount

    await engine.dispose()

    print(f"\n[reset] Demo data removed:")
    print(f"  Events:      {deleted_events} deleted")
    print(f"  Risks:       {deleted_risks} deleted")
    print(f"  Recoveries:  {deleted_recoveries} deleted")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="RevCure demo data management")
    parser.add_argument(
        "action",
        choices=["seed", "reset"],
        help="'seed' to create demo data, 'reset' to remove it",
    )
    args = parser.parse_args()

    if args.action == "seed":
        asyncio.run(seed())
    elif args.action == "reset":
        asyncio.run(reset())


if __name__ == "__main__":
    main()
