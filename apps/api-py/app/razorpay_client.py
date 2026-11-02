"""The one place REEP talks to Razorpay.

Three things and nothing else:

    create_order(amount_paise, receipt)          POST /v1/orders
    checkout_signature_is_valid(...)              the checkout's reply
    webhook_signature_is_valid(body, signature)   Razorpay's own call

THE KEY SECRET NEVER LEAVES THIS PROCESS. It signs the Basic-auth header and
the HMAC check; it is never logged, never put in a URL and never returned. A
failure is re-raised as `RazorpayError` naming the status code only, `from
None`, so a traceback cannot carry the request.

Signatures are compared with `hmac.compare_digest`, never `==`: a plain string
comparison returns early at the first wrong character, which leaks how much of
a forged signature was right.

RULE 1: an order carries an amount, the currency and the charge's own id as
`receipt`. No name, USN or email is sent to Razorpay from here.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Final

import httpx

from .config import settings

API_BASE: Final = "https://api.razorpay.com/v1"
CURRENCY: Final = "INR"
TIMEOUT_SECONDS: Final = 10.0


class RazorpayError(RuntimeError):
    """The provider could not be reached or refused the request."""


def create_order(*, amount_paise: int, receipt: str) -> str:
    """Open an order and return its id (order_XXXX)."""
    key_id = settings.razorpay_key_id.strip()
    key_secret = settings.razorpay_key_secret.strip()
    if not (key_id and key_secret):
        raise RazorpayError("Razorpay is not configured.")
    try:
        response = httpx.post(
            f"{API_BASE}/orders",
            auth=(key_id, key_secret),
            json={"amount": amount_paise, "currency": CURRENCY, "receipt": receipt[:40]},
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise RazorpayError(f"Razorpay unreachable ({type(exc).__name__}).") from None
    if response.status_code >= 400:
        raise RazorpayError(f"Razorpay refused the order (HTTP {response.status_code}).")
    try:
        order_id = response.json()["id"]
    except (ValueError, KeyError, TypeError):
        raise RazorpayError("Razorpay answered without an order id.") from None
    if not isinstance(order_id, str) or not order_id:
        raise RazorpayError("Razorpay answered without an order id.")
    return order_id


def _hex_hmac(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def checkout_signature_is_valid(*, order_id: str, payment_id: str, signature: str) -> bool:
    """Razorpay signs `order_id|payment_id` with the KEY SECRET."""
    secret = settings.razorpay_key_secret.strip()
    if not secret or not signature:
        return False
    expected = _hex_hmac(secret, f"{order_id}|{payment_id}".encode())
    return hmac.compare_digest(expected, signature.strip())


def webhook_signature_is_valid(body: bytes, signature: str | None) -> bool:
    """Razorpay signs the RAW request body with the WEBHOOK SECRET. The raw
    bytes, not re-serialised JSON: any re-encoding changes the signature."""
    secret = settings.razorpay_webhook_secret.strip()
    if not secret or not signature:
        return False
    return hmac.compare_digest(_hex_hmac(secret, body), signature.strip())
