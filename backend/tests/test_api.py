"""API tests for main.py: no network, no PayPal account, no API key.

The PayPal client, the reviewer and the repository are replaced through the
three module-level helpers, so every test exercises the real FastAPI routes,
the real state machine and the real models.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import List, Optional

import pytest
from fastapi.testclient import TestClient

import main
from db import InMemoryRepository
from models import AIVerdict
from paypal import PayPalError

TASK_BODY = {
    "title": "Palindrome checker",
    "requirements": "Write is_palindrome(s) ignoring case and punctuation, with 3 tests.",
    "amount": "20.00",
    "currency": "USD",
    "client_name": "Ada Client",
    "freelancer_email": "freelancer@example.com",
}

SUBMISSION = "def is_palindrome(s): ...\n# three example tests below\n"

PASSING = AIVerdict(passed=True, score=92, reason="Meets every requirement.", missing=[])


class FakePayPal:
    """Records what the API asked PayPal to do and replays scripted answers."""

    def __init__(self) -> None:
        self.created: List[dict] = []
        self.captured: List[str] = []
        self.payouts: List[dict] = []
        self.polls: List[str] = []
        self.capture_override: Optional[dict] = None
        self.payout_errors: List[Exception] = []
        self.amount: str = ""
        self.currency: str = ""

    def create_order(
        self,
        *,
        amount: str,
        currency: str,
        task_id: str,
        return_url: str,
        cancel_url: str,
    ) -> dict:
        self.amount, self.currency = amount, currency
        self.created.append(
            {
                "amount": amount,
                "currency": currency,
                "task_id": task_id,
                "return_url": return_url,
                "cancel_url": cancel_url,
            }
        )
        return {
            "order_id": f"ORDER-{task_id[:8]}",
            "approve_url": f"https://www.sandbox.paypal.com/checkoutnow?token={task_id[:8]}",
        }

    def capture_order(self, order_id: str) -> dict:
        self.captured.append(order_id)
        if self.capture_override is not None:
            return dict(self.capture_override)
        return {
            "status": "COMPLETED",
            "capture_id": "CAPTURE-1",
            "amount": self.amount,
            "currency": self.currency,
        }

    def send_payout(
        self, *, task_id: str, receiver_email: str, amount: str, currency: str
    ) -> dict:
        self.payouts.append(
            {
                "task_id": task_id,
                "receiver_email": receiver_email,
                "amount": amount,
                "currency": currency,
            }
        )
        if self.payout_errors:
            raise self.payout_errors.pop(0)
        return {"batch_id": "BATCH-1", "batch_status": "PENDING"}

    def get_payout_status(self, batch_id: str) -> dict:
        self.polls.append(batch_id)
        return {"batch_status": "SUCCESS", "item_status": "SUCCESS"}


@pytest.fixture
def api(monkeypatch):
    """TestClient wired to an in-memory repo, fake PayPal and a scripted verdict."""
    paypal = FakePayPal()
    repo = InMemoryRepository()
    verdicts = [PASSING]

    def reviewer(requirements: str, submission: str) -> AIVerdict:
        return verdicts[0]

    monkeypatch.setattr(main, "get_task_repository", lambda: repo)
    monkeypatch.setattr(main, "get_paypal_client", lambda: paypal)
    monkeypatch.setattr(main, "get_reviewer", lambda: reviewer)

    with TestClient(main.app) as client:
        yield SimpleNamespace(client=client, paypal=paypal, repo=repo, verdicts=verdicts)


def _create(api, **overrides) -> dict:
    """POST /tasks and return the created body."""
    response = api.client.post("/tasks", json={**TASK_BODY, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def _funded(api) -> dict:
    """Create a task and capture its payment."""
    task = _create(api)
    response = api.client.post(f"/tasks/{task['id']}/capture")
    assert response.status_code == 200, response.text
    return response.json()


def _reviewed(api) -> dict:
    """Create, fund and submit a task."""
    task = _funded(api)
    response = api.client.post(f"/tasks/{task['id']}/submit", json={"submission": SUBMISSION})
    assert response.status_code == 200, response.text
    return response.json()


# --- basics ----------------------------------------------------------------


def test_health(api):
    response = api.client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_task_creates_the_paypal_order(api):
    task = _create(api)

    assert task["status"] == "CREATED"
    assert task["approve_url"].startswith("https://www.sandbox.paypal.com/")
    assert task["paypal_order_id"] == f"ORDER-{task['id'][:8]}"
    assert task["capture_id"] is None
    assert task["ai_verdict"] is None
    assert task["payout_batch_id"] is None
    assert task["audit_log"][0]["event"] == "TASK_CREATED"

    order = api.paypal.created[0]
    assert order["amount"] == "20.00"
    assert order["currency"] == "USD"
    assert order["task_id"] == task["id"]
    assert order["return_url"].endswith(f"/return?task_id={task['id']}")
    assert order["cancel_url"].endswith(f"/cancel?task_id={task['id']}")


def test_client_cannot_set_status_ids_or_verdict(api):
    task = _create(
        api,
        status="PAID",
        paypal_order_id="CLIENT-SUPPLIED",
        payout_batch_id="CLIENT-BATCH",
        ai_verdict={"pass": True, "score": 100, "reason": "self-approved", "missing": []},
    )

    assert task["status"] == "CREATED"
    assert task["paypal_order_id"] == f"ORDER-{task['id'][:8]}"
    assert task["payout_batch_id"] is None
    assert task["ai_verdict"] is None


def test_list_and_get_tasks(api):
    task = _create(api)

    assert [item["id"] for item in api.client.get("/tasks").json()] == [task["id"]]
    assert api.client.get(f"/tasks/{task['id']}").json()["id"] == task["id"]


# --- happy path ------------------------------------------------------------


def test_full_happy_path_reaches_paid(api):
    task = _create(api)
    task_id = task["id"]

    funded = api.client.post(f"/tasks/{task_id}/capture").json()
    assert funded["status"] == "FUNDED"
    assert funded["capture_id"] == "CAPTURE-1"

    reviewed = api.client.post(f"/tasks/{task_id}/submit", json={"submission": SUBMISSION}).json()
    assert reviewed["status"] == "REVIEWED"
    assert reviewed["ai_verdict"]["pass"] is True
    assert reviewed["ai_verdict"]["score"] == 92

    paid = api.client.post(f"/tasks/{task_id}/approve").json()
    assert paid["status"] == "PAID"
    assert paid["payout_batch_id"] == "BATCH-1"
    assert paid["payout_status"] == "PENDING"

    assert [entry["event"] for entry in paid["audit_log"]] == [
        "TASK_CREATED",
        "FUNDED",
        "SUBMISSION_RECEIVED",
        "REVIEW_COMPLETED",
        "APPROVED",
        "PAID",
    ]
    assert len(api.paypal.payouts) == 1
    assert api.paypal.payouts[0]["receiver_email"] == "freelancer@example.com"
    assert api.paypal.payouts[0]["amount"] == "20.00"


def test_payout_status_is_polled_and_cached(api):
    task = _reviewed(api)
    assert api.client.get(f"/tasks/{task['id']}/payout-status").status_code == 409

    api.client.post(f"/tasks/{task['id']}/approve")
    body = api.client.get(f"/tasks/{task['id']}/payout-status").json()

    assert body == {
        "task_id": task["id"],
        "status": "PAID",
        "payout_batch_id": "BATCH-1",
        "batch_status": "SUCCESS",
        "item_status": "SUCCESS",
    }
    assert api.client.get(f"/tasks/{task['id']}").json()["payout_status"] == "SUCCESS"


# --- the AI can never pay --------------------------------------------------


def test_approve_before_review_is_rejected_and_pays_nothing(api):
    task = _funded(api)

    response = api.client.post(f"/tasks/{task['id']}/approve")

    assert response.status_code == 409
    assert "REVIEWED" in response.json()["detail"]
    assert api.paypal.payouts == []
    assert api.client.get(f"/tasks/{task['id']}").json()["status"] == "FUNDED"


def test_approve_on_a_rejected_task_is_rejected(api):
    task = _reviewed(api)
    assert api.client.post(f"/tasks/{task['id']}/reject").json()["status"] == "REJECTED"

    response = api.client.post(f"/tasks/{task['id']}/approve")

    assert response.status_code == 409
    assert api.paypal.payouts == []


def test_approve_twice_pays_only_once(api):
    task = _reviewed(api)
    first = api.client.post(f"/tasks/{task['id']}/approve")
    second = api.client.post(f"/tasks/{task['id']}/approve")

    assert first.status_code == 200
    assert first.json()["status"] == "PAID"
    assert second.status_code == 409
    assert len(api.paypal.payouts) == 1


def test_fallback_verdict_does_not_pay_and_the_human_can_reject(api):
    api.verdicts[0] = AIVerdict(
        passed=False,
        score=0,
        reason="Automated review failed. Manual review needed.",
        missing=[],
    )
    task = _reviewed(api)

    assert task["status"] == "REVIEWED"
    assert task["ai_verdict"]["pass"] is False
    assert task["ai_verdict"]["score"] == 0
    assert api.paypal.payouts == []

    rejected = api.client.post(f"/tasks/{task['id']}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"
    assert "Refund not automated" in rejected.json()["audit_log"][-1]["detail"]


# --- funding verification --------------------------------------------------


def test_capture_with_a_mismatched_amount_stays_created(api):
    task = _create(api)
    api.paypal.capture_override = {
        "status": "COMPLETED",
        "capture_id": "CAPTURE-1",
        "amount": "0.01",
        "currency": "USD",
    }

    response = api.client.post(f"/tasks/{task['id']}/capture")

    assert response.status_code == 409
    assert "capture not accepted" in response.json()["detail"]
    stored = api.client.get(f"/tasks/{task['id']}").json()
    assert stored["status"] == "CREATED"
    assert stored["capture_id"] is None
    assert stored["audit_log"][-1]["event"] == "CAPTURE_REJECTED"


def test_capture_with_a_mismatched_currency_stays_created(api):
    task = _create(api)
    api.paypal.capture_override = {
        "status": "COMPLETED",
        "capture_id": "CAPTURE-1",
        "amount": "20.00",
        "currency": "EUR",
    }

    response = api.client.post(f"/tasks/{task['id']}/capture")

    assert response.status_code == 409
    assert "EUR" in response.json()["detail"]
    assert api.client.get(f"/tasks/{task['id']}").json()["status"] == "CREATED"


def test_capture_accepts_equivalent_amount_formatting(api):
    """PayPal's '20.0' funds a task stored as '20.00': equal money, not equal strings."""
    task = _create(api)
    api.paypal.capture_override = {
        "status": "COMPLETED",
        "capture_id": "CAPTURE-1",
        "amount": "20.0",
        "currency": "USD",
    }

    response = api.client.post(f"/tasks/{task['id']}/capture")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "FUNDED"
    assert response.json()["capture_id"] == "CAPTURE-1"


def test_capture_rejects_a_missing_amount(api):
    """An unreadable amount must never fund a task - it fails closed."""
    task = _create(api)
    api.paypal.capture_override = {
        "status": "COMPLETED",
        "capture_id": "CAPTURE-1",
        "amount": "",
        "currency": "USD",
    }

    response = api.client.post(f"/tasks/{task['id']}/capture")

    assert response.status_code == 409
    assert "unknown" in response.json()["detail"]
    assert api.client.get(f"/tasks/{task['id']}").json()["status"] == "CREATED"


def test_capture_that_is_not_completed_does_not_fund(api):
    task = _create(api)
    api.paypal.capture_override = {
        "status": "PENDING",
        "capture_id": "CAPTURE-1",
        "amount": "20.00",
        "currency": "USD",
    }

    response = api.client.post(f"/tasks/{task['id']}/capture")

    assert response.status_code == 409
    assert "PENDING" in response.json()["detail"]
    assert api.client.get(f"/tasks/{task['id']}").json()["status"] == "CREATED"


def test_capture_twice_is_rejected(api):
    task = _funded(api)

    response = api.client.post(f"/tasks/{task['id']}/capture")

    assert response.status_code == 409
    assert len(api.paypal.captured) == 1


def test_submit_on_an_unfunded_task_is_rejected(api):
    task = _create(api)

    response = api.client.post(f"/tasks/{task['id']}/submit", json={"submission": SUBMISSION})

    assert response.status_code == 409
    assert api.client.get(f"/tasks/{task['id']}").json()["status"] == "CREATED"


# --- payout failures -------------------------------------------------------


def test_payout_failure_keeps_approved_and_retry_finishes(api):
    task = _reviewed(api)
    api.paypal.payout_errors = [PayPalError(500, "INTERNAL_SERVER_ERROR: no response", "dbg-1")]

    failed = api.client.post(f"/tasks/{task['id']}/approve")

    assert failed.status_code == 502
    assert failed.json() == {
        "message": "INTERNAL_SERVER_ERROR: no response",
        "debug_id": "dbg-1",
    }
    stored = api.client.get(f"/tasks/{task['id']}").json()
    assert stored["status"] == "APPROVED"
    assert stored["payout_batch_id"] is None
    assert stored["audit_log"][-1]["event"] == "PAYOUT_FAILED"
    assert "dbg-1" in stored["audit_log"][-1]["detail"]

    retried = api.client.post(f"/tasks/{task['id']}/retry-payout")

    assert retried.status_code == 200
    assert retried.json()["status"] == "PAID"
    assert retried.json()["payout_batch_id"] == "BATCH-1"
    assert len(api.paypal.payouts) == 2


def test_retry_payout_outside_approved_is_rejected(api):
    task = _reviewed(api)

    response = api.client.post(f"/tasks/{task['id']}/retry-payout")

    assert response.status_code == 409
    assert api.paypal.payouts == []


def test_duplicate_payout_batch_is_reported_not_retried(api):
    task = _reviewed(api)
    api.paypal.payout_errors = [
        PayPalError(500, "no response", "dbg-1"),
        PayPalError(400, "sender_batch_id already exists", "dbg-2"),
    ]
    api.client.post(f"/tasks/{task['id']}/approve")

    response = api.client.post(f"/tasks/{task['id']}/retry-payout")

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]
    stored = api.client.get(f"/tasks/{task['id']}").json()
    assert stored["status"] == "APPROVED"
    assert stored["audit_log"][-1]["event"] == "PAYOUT_DUPLICATE"


def test_paypal_error_never_leaks_secrets(api):
    task = _reviewed(api)
    api.paypal.payout_errors = [
        PayPalError(401, "invalid_client: authentication failed", "debug-xyz")
    ]

    response = api.client.post(f"/tasks/{task['id']}/approve")

    assert response.status_code == 502
    assert set(response.json()) == {"message", "debug_id"}
    assert response.json()["debug_id"] == "debug-xyz"
    text = response.text.lower()
    assert "client_secret" not in text
    assert "bearer" not in text


# --- validation and 404 ----------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/tasks/does-not-exist",
        "/tasks/does-not-exist/capture",
        "/tasks/does-not-exist/submit",
        "/tasks/does-not-exist/approve",
        "/tasks/does-not-exist/retry-payout",
        "/tasks/does-not-exist/reject",
        "/tasks/does-not-exist/payout-status",
    ],
)
def test_unknown_task_id_is_404(api, path):
    if path.endswith(("/submit",)):
        response = api.client.post(path, json={"submission": SUBMISSION})
    else:
        response = api.client.post(path) if path.split("/")[-1] in {
            "capture",
            "approve",
            "retry-payout",
            "reject",
        } else api.client.get(path)

    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {**TASK_BODY, "amount": 20.0},
        {**TASK_BODY, "amount": "0.00"},
        {**TASK_BODY, "amount": "150.00"},
        {**TASK_BODY, "requirements": "too short"},
        {**TASK_BODY, "freelancer_email": "not-an-email"},
        {key: value for key, value in TASK_BODY.items() if key != "title"},
    ],
)
def test_invalid_task_body_is_422(api, body):
    assert api.client.post("/tasks", json=body).status_code == 422


@pytest.mark.parametrize("submission", ["", "   ", "x" * 20001])
def test_invalid_submission_is_422(api, submission):
    task = _funded(api)

    response = api.client.post(f"/tasks/{task['id']}/submit", json={"submission": submission})

    assert response.status_code == 422
    assert api.client.get(f"/tasks/{task['id']}").json()["status"] == "FUNDED"


# --- CORS ------------------------------------------------------------------


def test_cors_allows_only_the_frontend_origin(api):
    allowed = api.client.get("/health", headers={"Origin": main.get_frontend_url()})
    foreign = api.client.get("/health", headers={"Origin": "https://evil.example"})

    assert allowed.headers.get("access-control-allow-origin") == main.get_frontend_url()
    assert foreign.headers.get("access-control-allow-origin") is None
