"""PayPal REST client - sandbox only.

Mirrors the verified calls in scripts/paypal_smoke_test.py, with a cached
client_credentials token, idempotency keys on every money-moving request, and
PayPal's debug_id preserved on errors. Secrets never reach a log or an
exception message.

Guardrail: the client refuses to initialise unless PAYPAL_BASE_URL contains
"sandbox", so a live URL cannot be used by accident.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Dict, Optional

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger("escrowpilot.paypal")

TOKEN_SAFETY_MARGIN_SECONDS = 60.0


class PayPalConfigError(RuntimeError):
    """Missing or unsafe PayPal configuration (not an HTTP failure)."""


class PayPalError(Exception):
    """A failed PayPal call. The message never contains credentials."""

    def __init__(self, status_code: int, message: str, debug_id: Optional[str] = None) -> None:
        self.status_code = status_code
        self.message = message
        self.debug_id = debug_id
        suffix = f" (PayPal debug_id={debug_id})" if debug_id else ""
        super().__init__(f"PayPal HTTP {status_code}: {message}{suffix}")


def _approval_url(body: dict) -> Optional[str]:
    """Sandbox returns rel='payer-action'; 'approve' is the other documented rel."""
    links = body.get("links") or []
    for relation in ("payer-action", "approve"):
        for link in links:
            if isinstance(link, dict) and link.get("rel") == relation and link.get("href"):
                return str(link["href"])
    return None


class PayPalClient:
    """Thin wrapper over the four PayPal endpoints EscrowPilot needs."""

    def __init__(self, *, timeout: float = 30.0) -> None:
        base_url = (os.environ.get("PAYPAL_BASE_URL") or "").strip().rstrip("/")
        if "sandbox" not in base_url.lower():
            raise PayPalConfigError(
                "PAYPAL_BASE_URL must point at the PayPal sandbox "
                "(https://api-m.sandbox.paypal.com). Live mode is blocked by design."
            )

        self._client_id = (os.environ.get("PAYPAL_CLIENT_ID") or "").strip()
        self._client_secret = (os.environ.get("PAYPAL_CLIENT_SECRET") or "").strip()
        if not self._client_id or not self._client_secret:
            raise PayPalConfigError(
                "PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET must be set (see .env.example)."
            )

        self._base_url = base_url
        self._http = httpx.Client(base_url=base_url, timeout=timeout)
        self._token: Optional[str] = None
        self._token_expires_at = 0.0

    # -- plumbing ---------------------------------------------------------

    def close(self) -> None:
        """Release the underlying connection pool."""
        self._http.close()

    def _get_token(self) -> str:
        """OAuth client_credentials token, cached until 60s before expiry."""
        if self._token and time.time() < self._token_expires_at:
            return self._token

        response = self._http.post(
            "/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
            headers={"Accept": "application/json"},
        )
        body = self._checked(response, "token request")

        token = body.get("access_token")
        if not token:
            raise PayPalError(response.status_code, "no access_token in the token response")

        expires_in = float(body.get("expires_in") or 0) or 3600.0
        self._token = str(token)
        self._token_expires_at = time.time() + max(expires_in - TOKEN_SAFETY_MARGIN_SECONDS, 0.0)
        return self._token

    def _headers(self, request_id: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Prefer": "return=representation",
            "PayPal-Request-Id": request_id,
        }

    @staticmethod
    def _checked(response: httpx.Response, action: str) -> dict:
        """Return the JSON body, or raise PayPalError carrying PayPal's debug_id."""
        try:
            body = response.json() if response.content else {}
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if response.is_success:
            return body

        # OAuth errors use error/error_description, REST errors use name/message.
        name = body.get("name") or body.get("error") or "ERROR"
        message = body.get("message") or body.get("error_description") or "no message"
        issues = [
            str(detail["issue"])
            for detail in body.get("details") or []
            if isinstance(detail, dict) and detail.get("issue")
        ]
        detail = f"{name}: {message}"
        if issues:
            detail += f" [{', '.join(issues)}]"

        debug_id = body.get("debug_id") or response.headers.get("PayPal-Debug-Id")
        logger.warning(
            "PayPal %s failed: HTTP %s %s (debug_id=%s)",
            action,
            response.status_code,
            detail,
            debug_id or "-",
        )
        raise PayPalError(response.status_code, detail, debug_id)

    # -- escrow -----------------------------------------------------------

    def create_order(
        self,
        amount: str,
        currency: str,
        task_id: str,
        return_url: str,
        cancel_url: str,
    ) -> Dict[str, str]:
        """Create a CAPTURE order; returns {'order_id', 'approve_url'}."""
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": f"escrowpilot_{task_id}",
                    "description": f"EscrowPilot task {task_id}",
                    "amount": {"currency_code": currency, "value": amount},
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "brand_name": "EscrowPilot",
                        "locale": "en-US",
                        "landing_page": "LOGIN",
                        "shipping_preference": "NO_SHIPPING",
                        "user_action": "PAY_NOW",
                        "payment_method_preference": "IMMEDIATE_PAYMENT_REQUIRED",
                        "return_url": return_url,
                        "cancel_url": cancel_url,
                    }
                }
            },
        }
        response = self._http.post(
            "/v2/checkout/orders",
            json=payload,
            headers=self._headers(f"escrowpilot-order-{task_id}"),
        )
        body = self._checked(response, "create order")

        order_id = str(body.get("id") or "")
        approve_url = _approval_url(body)
        if not order_id or not approve_url:
            raise PayPalError(
                response.status_code,
                "order response is missing an id or an approval link",
            )
        return {"order_id": order_id, "approve_url": approve_url}

    def capture_order(self, order_id: str) -> Dict[str, str]:
        """Capture an approved order; returns {'status', 'capture_id', 'amount', 'currency'}.

        `status` is the capture status (COMPLETED), falling back to the order
        status if PayPal omits it. `amount`/`currency` are what PayPal actually
        captured, so callers can verify the escrow was funded as agreed.
        """
        response = self._http.post(
            f"/v2/checkout/orders/{order_id}/capture",
            json={},
            headers=self._headers(f"escrowpilot-capture-{order_id}"),
        )
        body = self._checked(response, "capture order")

        capture_id = ""
        capture_status = ""
        capture_amount = ""
        capture_currency = ""
        for unit in body.get("purchase_units") or []:
            for capture in (unit.get("payments") or {}).get("captures") or []:
                capture_id = str(capture.get("id") or "")
                capture_status = str(capture.get("status") or "")
                amount = capture.get("amount") or {}
                capture_amount = str(amount.get("value") or "")
                capture_currency = str(amount.get("currency_code") or "")
                break
            if capture_id:
                break

        if not capture_id:
            raise PayPalError(response.status_code, "capture response contained no capture id")
        return {
            "status": capture_status or str(body.get("status") or ""),
            "capture_id": capture_id,
            "amount": capture_amount,
            "currency": capture_currency,
        }

    # -- payout -----------------------------------------------------------

    def send_payout(
        self,
        task_id: str,
        receiver_email: str,
        amount: str,
        currency: str,
    ) -> Dict[str, str]:
        """Pay the freelancer; returns {'batch_id', 'batch_status'}.

        `sender_batch_id` is derived from the task id and reused as the
        PayPal-Request-Id, so a retried payout cannot pay twice.
        """
        sender_batch_id = f"escrowpilot_{task_id}"
        payload = {
            "sender_batch_header": {
                "sender_batch_id": sender_batch_id,
                "email_subject": "You have a payment from EscrowPilot",
                "email_message": "EscrowPilot released the payment for your completed task.",
            },
            "items": [
                {
                    "recipient_type": "EMAIL",
                    # Payout amounts use "currency", unlike orders' "currency_code".
                    "amount": {"value": amount, "currency": currency},
                    "receiver": receiver_email,
                    "note": f"EscrowPilot task {task_id}",
                    "sender_item_id": f"{sender_batch_id}_1",
                }
            ],
        }
        response = self._http.post(
            "/v1/payments/payouts",
            json=payload,
            headers=self._headers(sender_batch_id),
        )
        body = self._checked(response, "send payout")

        header = body.get("batch_header") or {}
        batch_id = str(header.get("payout_batch_id") or "")
        if not batch_id:
            raise PayPalError(response.status_code, "payout response contained no payout_batch_id")
        return {"batch_id": batch_id, "batch_status": str(header.get("batch_status") or "")}

    def get_payout_status(self, batch_id: str) -> Dict[str, str]:
        """Poll a payout batch; returns {'batch_status', 'item_status'}."""
        response = self._http.get(
            f"/v1/payments/payouts/{batch_id}",
            headers={
                "Authorization": f"Bearer {self._get_token()}",
                "Accept": "application/json",
            },
        )
        body = self._checked(response, "payout status")

        header = body.get("batch_header") or {}
        items = header.get("items") or body.get("items") or []
        item_status = ""
        if items and isinstance(items[0], dict):
            item_status = str(items[0].get("transaction_status") or "")
        return {
            "batch_status": str(header.get("batch_status") or ""),
            "item_status": item_status,
        }
