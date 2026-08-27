"""
Minimal Razorpay API client for payment recovery.

Uses the Razorpay Orders API to create a new order for a failed payment,
which generates a payment link/order_id the customer can use to retry.
"""

import base64
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger("revcure.razorpay")

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"


@dataclass
class RazorpayOrderResult:
    success: bool
    order_id: Optional[str] = None
    amount: Optional[int] = None
    currency: Optional[str] = None
    status: Optional[str] = None
    error: Optional[str] = None
    raw_response: Optional[dict] = None


def _auth_header() -> str:
    """Build Basic auth header from RAZORPAY_KEY_ID:RAZORPAY_KEY_SECRET."""
    credentials = f"{settings.RAZORPAY_KEY_ID}:{settings.RAZORPAY_KEY_SECRET}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return f"Basic {encoded}"


def _sanitize_razorpay_error(body: dict, status_code: int) -> str:
    """Extract a user-safe error string from a Razorpay API error body.

    Never returns the full response — only the error code and a generic
    description.  Falls back to the HTTP status code when the body is
    malformed.
    """
    error_obj = body.get("error") if isinstance(body, dict) else {}
    code = error_obj.get("code") or f"HTTP_{status_code}"
    # Provide a generic description — never forward the raw error description
    # which may contain internal Razorpay details.
    _safe_descriptions = {
        "BAD_REQUEST_ERROR": "Invalid request parameters",
        "AUTHENTICATION_ERROR": "Payment gateway authentication failed",
        "AUTHORIZATION_ERROR": "Payment gateway authorization denied",
        "RATE_LIMIT_EXCEEDED": "Too many requests — please retry later",
        "SERVER_ERROR": "Payment gateway server error",
    }
    desc = _safe_descriptions.get(code, f"Payment gateway error ({code})")
    return desc


async def create_retry_order(
    payment_id: str,
    amount: int,
    currency: str = "INR",
    receipt: Optional[str] = None,
) -> RazorpayOrderResult:
    """
    Create a Razorpay Order for a failed payment to enable retry.

    This calls POST /v1/orders with the original payment amount.
    The returned order_id can be used to collect a new payment attempt.

    Args:
        payment_id: The original failed Razorpay payment ID (pay_xxx).
        amount:     Amount in paise (e.g. 50000 = ₹500).
        currency:   Currency code (default INR).
        receipt:    Optional receipt identifier (defaults to payment_id).

    Returns:
        RazorpayOrderResult with order details or error information.
    """
    if not settings.razorpay_configured:
        logger.warning("Razorpay credentials not configured — skipping API call")
        return RazorpayOrderResult(
            success=False,
            error="Payment gateway not configured",
        )

    url = f"{RAZORPAY_API_BASE}/orders"
    headers = {
        "Authorization": _auth_header(),
        "Content-Type": "application/json",
    }
    payload = {
        "amount": amount,
        "currency": currency,
        "receipt": receipt or payment_id,
        "notes": {
            "original_payment_id": payment_id,
            "retry_reason": "revenue_recovery",
        },
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            body = response.json()

        if response.status_code in (200, 201):
            logger.info(
                "Razorpay order created: %s for payment %s (amount=%d)",
                body.get("id"), payment_id, amount,
            )
            return RazorpayOrderResult(
                success=True,
                order_id=body.get("id"),
                amount=body.get("amount"),
                currency=body.get("currency"),
                status=body.get("status"),
                raw_response=body,
            )
        else:
            safe_error = _sanitize_razorpay_error(body, response.status_code)
            logger.error(
                "Razorpay order creation failed: status=%d error=%s payment=%s",
                response.status_code, safe_error, payment_id,
            )
            return RazorpayOrderResult(
                success=False,
                error=safe_error,
                raw_response=body,
            )

    except httpx.TimeoutException:
        logger.error("Razorpay API timeout for payment %s", payment_id)
        return RazorpayOrderResult(success=False, error="Payment gateway request timed out")
    except httpx.RequestError as exc:
        logger.error("Razorpay API request failed for payment %s: %s", payment_id, exc)
        return RazorpayOrderResult(success=False, error="Payment gateway unreachable")
    except Exception as exc:
        logger.error("Unexpected error calling Razorpay for payment %s: %s", payment_id, exc)
        return RazorpayOrderResult(success=False, error="Unexpected payment gateway error")
