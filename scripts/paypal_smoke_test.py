#!/usr/bin/env python3
"""EscrowPilot - PayPal sandbox smoke test.

Standalone (standard library only, no imports from the app) so it can be run
before anything else is installed. It exercises the exact money path the app
will use, in order:

    access token -> create order -> (you approve in the browser) -> capture
                 -> payout -> payout batch status

Usage
-----
    python scripts/paypal_smoke_test.py --check-config
    python scripts/paypal_smoke_test.py --amount 5.00
    python scripts/paypal_smoke_test.py --receiver sb-freelancer@business.example.com --amount 5.00
    python scripts/paypal_smoke_test.py --amount 5.00 --skip-payout
    python scripts/paypal_smoke_test.py --amount 5.00 --payout-amount 1.00

Never wrap a value in angle brackets ("<email>"): PowerShell treats "<" as a
redirection operator and the command fails before Python even starts.

Credentials come from the environment or from .env at the repo root:
    PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, PAYPAL_BASE_URL,
    PAYPAL_SANDBOX_RECEIVER_EMAIL, FRONTEND_URL, MAX_TASK_AMOUNT

Safety: this script refuses to run unless PAYPAL_BASE_URL is the PayPal
sandbox host. Live mode is blocked on purpose.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SANDBOX_HOST = "api-m.sandbox.paypal.com"
DEFAULT_BASE_URL = f"https://{SANDBOX_HOST}"
CURRENCY = "USD"
TERMINAL_BATCH_STATES = {"SUCCESS", "FAILED", "DENIED", "RETURNED", "CANCELED", "CANCELLED"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class PayPalError(RuntimeError):
    """A non-2xx response from PayPal, with PayPal's debug_id preserved."""

    def __init__(self, method: str, path: str, status: int, body: dict | str):
        self.method = method
        self.path = path
        self.status = status
        self.body = body
        if isinstance(body, dict):
            # OAuth errors use error/error_description; REST errors use name/message.
            name = body.get("name") or body.get("error") or "ERROR"
            message = (
                body.get("message")
                or body.get("error_description")
                or "no message"
            )
            debug_id = body.get("debug_id")
            issues = [
                d.get("issue", "")
                for d in body.get("details") or []
                if isinstance(d, dict) and d.get("issue")
            ]
        else:
            name, message, debug_id, issues = "ERROR", str(body)[:300], None, []
        parts = [f"{method} {path} -> HTTP {status} {name}: {message}"]
        if issues:
            parts.append(f"issues={','.join(issues)}")
        if debug_id:
            parts.append(f"debug_id={debug_id}")
        super().__init__(" | ".join(parts))


class ConfigError(RuntimeError):
    pass


def load_env_file(path: Path) -> None:
    """Minimal .env reader that never overrides already-set variables."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def env(name: str, default: str | None = None) -> str | None:
    value = (os.environ.get(name) or "").strip()
    return value or default


def mask(value: str) -> str:
    if not value:
        return "(unset)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


class Config:
    def __init__(self) -> None:
        self.base_url = (env("PAYPAL_BASE_URL", DEFAULT_BASE_URL) or "").rstrip("/")
        self.client_id = env("PAYPAL_CLIENT_ID")
        self.client_secret = env("PAYPAL_CLIENT_SECRET")
        self.frontend_url = (env("FRONTEND_URL", "http://localhost:5173") or "").rstrip("/")
        self.max_amount = env("MAX_TASK_AMOUNT", "100.00")
        self.default_receiver = env("PAYPAL_SANDBOX_RECEIVER_EMAIL")

    def require_credentials(self) -> None:
        missing = [
            name
            for name, value in (
                ("PAYPAL_CLIENT_ID", self.client_id),
                ("PAYPAL_CLIENT_SECRET", self.client_secret),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "Missing " + ", ".join(missing) + " - put them in .env (see .env.example)."
            )

    def require_sandbox(self) -> None:
        host = urllib.parse.urlparse(self.base_url).hostname or ""
        if host != SANDBOX_HOST:
            raise ConfigError(
                f"PAYPAL_BASE_URL is '{self.base_url}'. This project is sandbox-only; "
                f"set PAYPAL_BASE_URL={DEFAULT_BASE_URL} and use sandbox credentials."
            )


def parse_amount(value: str, label: str) -> str:
    """Validate a decimal amount and normalise it to two decimal places."""
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        raise ConfigError(f"{label} must be a decimal like 20.00 (got {value!r}).") from None
    if not number > 0:
        raise ConfigError(f"{label} must be greater than zero.")
    return f"{number:.2f}"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    json_body: dict | None = None,
    form_body: dict | None = None,
    headers: dict | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict]:
    base = (env("PAYPAL_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")
    url = base + path
    data: bytes | None = None
    req = urllib.request.Request(url, method=method)

    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    elif form_body is not None:
        data = urllib.parse.urlencode(form_body).encode("utf-8")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

    req.data = data
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for key, value in (headers or {}).items():
        req.add_header(key, value)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body: dict | str = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            body = raw
        raise PayPalError(method, path, exc.code, body) from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error calling {url}: {exc.reason}") from None


# ---------------------------------------------------------------------------
# PayPal operations (same calls the backend will make)
# ---------------------------------------------------------------------------


def get_access_token(config: Config) -> str:
    """POST /v1/oauth2/token - client_credentials, Basic auth."""
    credentials = f"{config.client_id}:{config.client_secret}".encode("utf-8")
    basic = base64.b64encode(credentials).decode("ascii")
    _, body = request(
        "POST",
        "/v1/oauth2/token",
        form_body={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {basic}"},
    )
    token = body.get("access_token")
    if not token:
        raise RuntimeError("PayPal did not return an access_token.")
    return token


def create_order(config: Config, token: str, amount: str, reference_id: str) -> dict:
    """POST /v2/checkout/orders - intent CAPTURE, return/cancel URLs."""
    payload = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": reference_id,
                "description": "EscrowPilot smoke test task (sandbox)",
                "amount": {"currency_code": CURRENCY, "value": amount},
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
                    "return_url": f"{config.frontend_url}/?paypal=return",
                    "cancel_url": f"{config.frontend_url}/?paypal=cancel",
                }
            }
        },
    }
    _, body = request(
        "POST",
        "/v2/checkout/orders",
        token=token,
        json_body=payload,
        headers={
            "PayPal-Request-Id": str(uuid.uuid4()),
            "Prefer": "return=representation",
        },
    )
    return body


def approval_link(order: dict) -> str | None:
    """Sandbox returns rel='payer-action'; live (blocked here) uses 'approve'."""
    links = order.get("links") or []
    for wanted in ("payer-action", "approve", "checkout"):
        for link in links:
            if link.get("rel") == wanted and link.get("href"):
                return link["href"]
    return None


def capture_order(token: str, order_id: str) -> dict:
    """POST /v2/checkout/orders/{id}/capture"""
    _, body = request(
        "POST",
        f"/v2/checkout/orders/{order_id}/capture",
        token=token,
        json_body={},
        headers={"PayPal-Request-Id": str(uuid.uuid4())},
    )
    return body


def extract_capture(captured: dict) -> tuple[str | None, str | None, str | None]:
    for unit in captured.get("purchase_units") or []:
        for capture in ((unit.get("payments") or {}).get("captures") or []):
            return capture.get("id"), capture.get("status"), capture.get("amount", {}).get("value")
    return None, None, None


def create_payout(
    token: str, receiver: str, amount: str, sender_batch_id: str
) -> dict:
    """POST /v1/payments/payouts - idempotent via sender_batch_id + Request-Id."""
    payload = {
        "sender_batch_header": {
            "sender_batch_id": sender_batch_id,
            "email_subject": "You have a payment from EscrowPilot (sandbox)",
            "email_message": (
                "This is a PayPal sandbox payout from the EscrowPilot smoke test. "
                "No real money moved."
            ),
        },
        "items": [
            {
                "recipient_type": "EMAIL",
                "amount": {"value": amount, "currency": CURRENCY},
                "receiver": receiver,
                "note": "EscrowPilot sandbox smoke-test payout",
                "sender_item_id": f"{sender_batch_id}_1",
            }
        ],
    }
    _, body = request(
        "POST",
        "/v1/payments/payouts",
        token=token,
        json_body=payload,
        headers={"PayPal-Request-Id": sender_batch_id},
    )
    return body


def payout_status(token: str, batch_id: str) -> dict:
    """GET /v1/payments/payouts/{batch_id}"""
    _, body = request("GET", f"/v1/payments/payouts/{batch_id}", token=token)
    return body


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def step(number: int, message: str) -> None:
    print(f"\n[{number}] {message}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="EscrowPilot PayPal sandbox smoke test (token -> order -> capture -> payout).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/paypal_smoke_test.py --check-config\n"
            "  python scripts/paypal_smoke_test.py --amount 5.00\n"
            "  python scripts/paypal_smoke_test.py --receiver escrowpilot-freelancer@example.com\n"
            "  python scripts/paypal_smoke_test.py --amount 5.00 --skip-payout\n"
            "\n"
            "Type real values, never placeholders in angle brackets: PowerShell treats\n"
            "'<' as a redirection operator and fails before Python runs.\n"
        ),
    )
    parser.add_argument(
        "--receiver",
        default=None,
        help="Freelancer email that receives the payout (default: PAYPAL_SANDBOX_RECEIVER_EMAIL).",
    )
    parser.add_argument("--amount", default="5.00", help="Order amount in USD (default 5.00).")
    parser.add_argument(
        "--payout-amount",
        default=None,
        help=(
            "Amount to pay the freelancer (default: the captured amount). Lower it if PayPal "
            "returns INSUFFICIENT_FUNDS because of sandbox fees."
        ),
    )
    parser.add_argument(
        "--skip-payout",
        action="store_true",
        help="Stop after capture; do not call the Payouts API.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Do not prompt before capture/payout (for scripted runs; approve the order first).",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=30,
        help="How long to poll the payout batch for a final status (default 30).",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Print the resolved configuration (secrets masked) and exit without calling PayPal.",
    )
    return parser


def print_config(config: Config, amount: str | None, receiver: str | None) -> None:
    sandbox_ok = urllib.parse.urlparse(config.base_url).hostname == SANDBOX_HOST
    print("EscrowPilot smoke-test configuration")
    print(f"  PAYPAL_BASE_URL          {config.base_url}  ({'sandbox OK' if sandbox_ok else 'NOT sandbox'})")
    print(f"  PAYPAL_CLIENT_ID         {mask(config.client_id or '')}")
    print(f"  PAYPAL_CLIENT_SECRET     {'set (' + str(len(config.client_secret or '')) + ' chars)' if config.client_secret else '(unset)'}")
    print(f"  receiver                 {receiver or config.default_receiver or '(unset - pass --receiver)'}")
    print(f"  amount                   {amount or '5.00'} {CURRENCY}")
    print(f"  MAX_TASK_AMOUNT          {config.max_amount} {CURRENCY}")
    print(f"  FRONTEND_URL             {config.frontend_url}")
    print(f"  .env loaded              {'yes' if (REPO_ROOT / '.env').is_file() else 'no (using environment only)'}")


def run(args: argparse.Namespace) -> int:
    config = Config()
    config.require_sandbox()

    amount = parse_amount(args.amount, "--amount")
    max_amount = parse_amount(config.max_amount, "MAX_TASK_AMOUNT")
    if float(amount) > float(max_amount):
        raise ConfigError(f"--amount {amount} exceeds MAX_TASK_AMOUNT {max_amount}.")

    receiver = (args.receiver or config.default_receiver or "").strip()
    payout_amount = (
        parse_amount(args.payout_amount, "--payout-amount") if args.payout_amount else None
    )

    if args.check_config:
        print_config(config, amount, receiver)
        if not receiver:
            print("\nNote: no receiver set (use --receiver or PAYPAL_SANDBOX_RECEIVER_EMAIL).")
        return 0

    config.require_credentials()
    if not receiver:
        raise ConfigError(
            "No payout receiver. Pass --receiver <sandbox email> or set "
            "PAYPAL_SANDBOX_RECEIVER_EMAIL."
        )
    if not EMAIL_RE.match(receiver):
        raise ConfigError(f"Receiver is not a valid email: {receiver!r}")

    print_config(config, amount, receiver)
    run_id = uuid.uuid4().hex[:12]

    step(1, "Access token (POST /v1/oauth2/token)")
    token = get_access_token(config)
    print("    ok - bearer token received")

    step(2, f"Create order for {amount} {CURRENCY} (POST /v2/checkout/orders)")
    order = create_order(config, token, amount, f"escrowpilot_smoke_{run_id}")
    order_id = order.get("id")
    link = approval_link(order)
    print(f"    order id : {order_id}")
    print(f"    status   : {order.get('status')}")
    if not link:
        raise RuntimeError(f"No approval link in the order response: {json.dumps(order)[:500]}")
    print("\n    Approve it in a browser (sandbox login, USA personal buyer account):")
    print(f"    {link}")
    print("\n    Tip: sandbox account passwords live in Dashboard > Testing Tools > Sandbox Accounts.")

    if not args.yes:
        input("\n    Press Enter after you have approved the payment... ")
    else:
        print("\n    --yes: assuming the order is already approved, continuing immediately.")

    step(3, f"Capture the order (POST /v2/checkout/orders/{order_id}/capture)")
    try:
        captured = capture_order(token, order_id)
    except PayPalError as exc:
        if "ORDER_NOT_APPROVED" in str(exc):
            raise PayPalError(
                exc.method,
                exc.path,
                exc.status,
                {
                    **(exc.body if isinstance(exc.body, dict) else {}),
                    "message": "The buyer has not approved this order yet - open the link above, "
                    "log in with the sandbox buyer account and complete checkout.",
                },
            ) from None
        raise
    capture_id, capture_status, captured_value = extract_capture(captured)
    print(f"    capture id     : {capture_id}")
    print(f"    capture status : {capture_status}")
    print(f"    captured value : {captured_value} {CURRENCY}")

    if args.skip_payout:
        print("\nDone (--skip-payout). Order + capture verified.")
        return 0

    step(4, f"Payout to {receiver} (POST /v1/payments/payouts)")
    pay_value = payout_amount or captured_value or amount
    sender_batch_id = f"escrowpilot_smoke_{run_id}"
    batch = create_payout(token, receiver, pay_value, sender_batch_id)
    header = batch.get("batch_header") or {}
    batch_id = header.get("payout_batch_id")
    print(f"    sender_batch_id : {sender_batch_id}  (idempotency key)")
    print(f"    payout_batch_id : {batch_id}")
    print(f"    batch_status    : {header.get('batch_status')}")
    if not batch_id:
        raise RuntimeError(f"No payout_batch_id in response: {json.dumps(batch)[:500]}")

    step(5, f"Poll payout status (GET /v1/payments/payouts/{batch_id})")
    deadline = time.time() + max(args.poll_seconds, 0)
    status_doc: dict = {}
    while True:
        status_doc = payout_status(token, batch_id).get("batch_header") or {}
        batch_status = status_doc.get("batch_status")
        items = status_doc.get("items") or []
        item_states = ", ".join(
            f"{item.get('transaction_id') or item.get('payout_item_id')}={item.get('transaction_status')}"
            for item in items
        )
        print(f"    batch_status={batch_status}" + (f"  items: {item_states}" if item_states else ""))
        if batch_status in TERMINAL_BATCH_STATES or time.time() >= deadline:
            break
        time.sleep(5)

    final = status_doc.get("batch_status")
    print("\n--------------------------------------------------------------")
    print(f"Result: order {order_id} captured, payout batch {batch_id} is {final}.")
    if final in {"PENDING", "PROCESSING", "UNCLAIMED"}:
        print(
            "This is normal in sandbox: PENDING/PROCESSING means PayPal is still settling, and\n"
            "UNCLAIMED means the receiver email has no PayPal account yet. Paying a sandbox\n"
            "Personal account email you created avoids UNCLAIMED.\n"
            "Re-check later with:\n"
            f"    GET {config.base_url}/v1/payments/payouts/{batch_id}"
        )
    print("--------------------------------------------------------------")
    return 0 if final in {"SUCCESS", "PENDING", "PROCESSING", "UNCLAIMED", None} else 1


def main(argv: list[str] | None = None) -> int:
    # Keep stdout/stderr interleaved in order even when the output is piped.
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
    load_env_file(REPO_ROOT / ".env")
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except ConfigError as exc:
        print(f"\nConfig error: {exc}", file=sys.stderr)
        return 2
    except PayPalError as exc:
        print(f"\nPayPal error: {exc}", file=sys.stderr)
        print(
            "  Sandbox troubleshooting: check the client id/secret are from Apps & Credentials >\n"
            "  Sandbox (not Live), and give PayPal the debug_id above if you contact support.",
            file=sys.stderr,
        )
        return 1
    except RuntimeError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
