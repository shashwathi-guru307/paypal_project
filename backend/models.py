"""Domain models for EscrowPilot.

A task moves through `Status`; the amount is validated with `Decimal` and then
carried around as a plain string like "20.00" so it never picks up float error,
in JSON, in Firestore, or in an API response.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

try:  # keep MAX_TASK_AMOUNT in sync with the repo-root .env when run from backend/
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # python-dotenv is optional for the pure-model path
    pass

DEFAULT_MAX_TASK_AMOUNT = "100.00"
CURRENCY = "USD"


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string, e.g. '2026-11-09T17:20:31Z'."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def max_task_amount() -> Decimal:
    """Server-side per-task cap, from MAX_TASK_AMOUNT (default 100.00 USD)."""
    raw = (os.environ.get("MAX_TASK_AMOUNT") or DEFAULT_MAX_TASK_AMOUNT).strip()
    try:
        cap = Decimal(raw)
    except InvalidOperation:
        return Decimal(DEFAULT_MAX_TASK_AMOUNT)
    if not cap.is_finite() or cap <= 0:
        return Decimal(DEFAULT_MAX_TASK_AMOUNT)
    return cap.quantize(Decimal("0.01"))


class Status(str, Enum):
    """Task lifecycle. Transitions are enforced in state.py."""

    CREATED = "CREATED"
    FUNDED = "FUNDED"
    SUBMITTED = "SUBMITTED"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    PAID = "PAID"
    REJECTED = "REJECTED"


class TaskCreate(BaseModel):
    """Payload for creating a task. Amount arrives as a string, never a float."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=120)
    requirements: str = Field(min_length=10, max_length=2000)
    amount: str
    currency: Literal["USD"] = CURRENCY
    client_name: str = Field(min_length=1, max_length=80)
    freelancer_email: EmailStr

    @field_validator("amount", mode="before")
    @classmethod
    def _validate_amount(cls, value: object) -> str:
        """Require a positive decimal with <=2 places, and enforce the USD cap."""
        if not isinstance(value, str):
            raise ValueError("amount must be a string such as '20.00'")

        raw = value.strip()
        if not raw:
            raise ValueError("amount is required")

        try:
            amount = Decimal(raw)
        except InvalidOperation:
            raise ValueError("amount must be a decimal number such as '20.00'") from None

        if not amount.is_finite():
            raise ValueError("amount must be a finite decimal number")
        if amount <= 0:
            raise ValueError("amount must be greater than zero")
        if -amount.as_tuple().exponent > 2:
            raise ValueError("amount must have at most two decimal places")

        amount = amount.quantize(Decimal("0.01"))
        cap = max_task_amount()
        if amount > cap:
            raise ValueError(f"amount must not exceed the cap of {cap} {CURRENCY}")
        return format(amount, "f")


class AIVerdict(BaseModel):
    """Structured LLM review. Advisory only: it can never move money."""

    model_config = ConfigDict(populate_by_name=True)

    passed: bool = Field(alias="pass")
    score: int = Field(ge=0, le=100)
    reason: str
    missing: List[str] = Field(default_factory=list)


class AuditEntry(BaseModel):
    """One line of the append-only audit log shown in the UI."""

    ts: str = Field(default_factory=utc_now_iso)
    event: str
    detail: str = ""


class Task(BaseModel):
    """A persisted escrow task, including its PayPal references and audit trail."""

    id: str
    title: str
    requirements: str
    amount: str
    currency: Literal["USD"] = CURRENCY
    client_name: str
    freelancer_email: EmailStr
    status: Status = Status.CREATED

    paypal_order_id: Optional[str] = None
    approve_url: Optional[str] = None
    capture_id: Optional[str] = None
    submission: Optional[str] = None
    ai_verdict: Optional[AIVerdict] = None
    payout_batch_id: Optional[str] = None
    payout_status: Optional[str] = None

    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    audit_log: List[AuditEntry] = Field(default_factory=list)


def new_task(payload: TaskCreate) -> Task:
    """Build a CREATED task with a uuid4-hex id and its first audit entry."""
    now = utc_now_iso()
    return Task(
        id=uuid.uuid4().hex,
        title=payload.title,
        requirements=payload.requirements,
        amount=payload.amount,
        currency=payload.currency,
        client_name=payload.client_name,
        freelancer_email=payload.freelancer_email,
        status=Status.CREATED,
        created_at=now,
        updated_at=now,
        audit_log=[
            AuditEntry(ts=now, event="TASK_CREATED", detail=f"Task created for {payload.amount} USD")
        ],
    )
