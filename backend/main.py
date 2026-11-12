"""EscrowPilot HTTP API - the only surface that can move money.

Run locally:

    cd backend
    uvicorn main:app --reload --port 8000

Four safety rules hold on every endpoint in this file:

1. The AI verdict never triggers a payout. Only POST /tasks/{id}/approve does,
   and only from REVIEWED, i.e. after a human has seen the verdict.
2. Funding is verified against the real PayPal capture: status COMPLETED and an
   exact amount/currency match. A mismatch funds nothing.
3. The state machine in state.py is enforced server-side on every endpoint; a
   client can never set status, ids or verdicts, and TaskCreate has no such
   fields in the first place.
4. Responses and logs never contain credentials or raw upstream bodies - PayPal
   failures surface as {message, debug_id} only.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field  # noqa: E402

from db import TaskRepository, get_repository  # noqa: E402
from llm import review_submission  # noqa: E402
from models import AIVerdict, AuditEntry, Status, Task, TaskCreate, new_task, utc_now_iso  # noqa: E402
from paypal import PayPalClient, PayPalConfigError, PayPalError  # noqa: E402
from state import InvalidTransition, transition  # noqa: E402

logger = logging.getLogger("escrowpilot.api")

DEFAULT_FRONTEND_URL = "http://localhost:5173"
COMPLETED = "COMPLETED"

ReviewFn = Callable[[str, str], AIVerdict]


def _money(value: object) -> Optional[Decimal]:
    """Parse a PayPal or task money string exactly; None when it is not a decimal.

    Comparing the strings directly would refuse a real payment whenever PayPal
    formats the value differently ("1.00" vs "1.0"); comparing Decimal values
    compares the money, not the spelling.
    """
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def get_frontend_url() -> str:
    """Where PayPal sends the buyer back; the only origin CORS allows."""
    return (os.environ.get("FRONTEND_URL") or DEFAULT_FRONTEND_URL).strip().rstrip("/")


# ---------------------------------------------------------------------------
# Process-wide collaborators. Created once, and looked up on every request so
# tests can monkeypatch these three names with fakes.
# ---------------------------------------------------------------------------

_repository: Optional[TaskRepository] = None
_paypal_client: Optional[PayPalClient] = None


def get_task_repository() -> TaskRepository:
    """The task store, chosen once by db.get_repository()."""
    global _repository
    if _repository is None:
        _repository = get_repository()
    return _repository


def get_paypal_client() -> PayPalClient:
    """The PayPal sandbox client, created on first use and reused afterwards."""
    global _paypal_client
    if _paypal_client is None:
        _paypal_client = PayPalClient()
    return _paypal_client


def get_reviewer() -> ReviewFn:
    """The AI reviewer. Phase 3 guarantees it returns a verdict instead of raising."""
    return review_submission


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Warm the store and report PayPal config once, then release the HTTP pool."""
    get_task_repository()
    try:
        get_paypal_client()
    except PayPalConfigError as exc:
        logger.warning("PayPal is not configured (%s); funding and payout will return 502.", exc)
    yield
    if _paypal_client is not None:
        _paypal_client.close()


app = FastAPI(
    title="EscrowPilot",
    version="0.4.0",
    description=(
        "AI-reviewed micro-task escrow on PayPal. The AI only advises; "
        "a human approval is always required before any payout."
    ),
    lifespan=lifespan,
)

# Rule 4 applies to CORS too: only the configured frontend may call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_frontend_url()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request bodies and shared helpers
# ---------------------------------------------------------------------------


class SubmissionRequest(BaseModel):
    """A freelancer's submitted work. Length is bounded so prompts stay cheap."""

    model_config = ConfigDict(str_strip_whitespace=True)

    submission: str = Field(min_length=1, max_length=20000)


def _load(task_id: str) -> Task:
    """Fetch a task or raise 404."""
    task = get_task_repository().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return task


def _save(task: Task) -> Task:
    """Persist a task and return it, so handlers can `return _save(task)`."""
    return get_task_repository().update(task)


def _require(task: Task, expected: Status, action: str) -> None:
    """409 unless the task is in the state `action` needs, naming both states."""
    if task.status is not expected:
        raise HTTPException(
            status_code=409,
            detail=(
                f"cannot {action} while the task is {task.status.value} "
                f"(expected {expected.value})"
            ),
        )


def _payout_failure_detail(exc: PayPalError) -> str:
    """Audit text for a failed payout. No payloads, no credentials."""
    return (
        f"send_payout failed (debug_id={exc.debug_id or 'none'}); "
        "task stays APPROVED so retry-payout is safe. Refund not automated in demo."
    )


def _is_duplicate_batch(exc: PayPalError) -> bool:
    """True when PayPal is saying this sender_batch_id was already used."""
    text = str(exc).lower()
    return "already exists" in text or "duplicate" in text


# ---------------------------------------------------------------------------
# Error mapping: unknown id -> 404, bad state -> 409, PayPal -> 502, bad body -> 422
# ---------------------------------------------------------------------------


@app.exception_handler(InvalidTransition)
async def _invalid_transition(_: Request, exc: InvalidTransition) -> JSONResponse:
    """The state machine refused a move; nothing changed."""
    return JSONResponse(
        status_code=409,
        content={
            "message": str(exc),
            "current": exc.current.value,
            "target": exc.target.value,
        },
    )


@app.exception_handler(PayPalError)
async def _paypal_error(_: Request, exc: PayPalError) -> JSONResponse:
    """Rule 4: hand back PayPal's message and debug_id, never a token or payload."""
    logger.warning("PayPal call failed: HTTP %s (debug_id=%s)", exc.status_code, exc.debug_id or "-")
    return JSONResponse(
        status_code=502,
        content={"message": exc.message, "debug_id": exc.debug_id},
    )


@app.exception_handler(PayPalConfigError)
async def _paypal_config_error(_: Request, __: PayPalConfigError) -> JSONResponse:
    """Server-side configuration problem, not the caller's fault."""
    logger.error("PayPal configuration is missing or unsafe; see .env.example.")
    return JSONResponse(
        status_code=502,
        content={
            "message": "PayPal is not configured on this server; see backend logs.",
            "debug_id": None,
        },
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> Dict[str, str]:
    """Liveness probe used by the walkthrough before anything else."""
    return {"status": "ok"}


@app.post("/tasks", status_code=201, response_model=Task)
def create_task(payload: TaskCreate) -> Task:
    """Create the escrow task and its PayPal order.

    TaskCreate carries no status, id, verdict or PayPal reference, so a client
    cannot pre-fund, pre-approve or pre-review anything.
    """
    task = new_task(payload)  # CREATED + the TASK_CREATED audit entry
    links = get_paypal_client().create_order(
        amount=task.amount,
        currency=task.currency,
        task_id=task.id,
        return_url=f"{get_frontend_url()}/return?task_id={task.id}",
        cancel_url=f"{get_frontend_url()}/cancel?task_id={task.id}",
    )
    task.paypal_order_id = links["order_id"]
    task.approve_url = links["approve_url"]
    task.updated_at = utc_now_iso()
    logger.info("Created task %s for %s %s", task.id, task.amount, task.currency)
    return get_task_repository().create(task)


@app.get("/tasks", response_model=List[Task])
def list_tasks() -> List[Task]:
    """All tasks, newest first."""
    return get_task_repository().list()


@app.get("/tasks/{task_id}", response_model=Task)
def get_task(task_id: str) -> Task:
    """One task, including its audit log."""
    return _load(task_id)


@app.post("/tasks/{task_id}/capture", response_model=Task)
def capture_task(task_id: str) -> Task:
    """Capture the buyer's payment and fund the task only if PayPal agrees.

    Rule 2: the task becomes FUNDED only when PayPal reports COMPLETED *and* the
    captured amount and currency are exactly the task's. Anything else is
    recorded and refused.
    """
    task = _load(task_id)
    _require(task, Status.CREATED, "capture a payment")
    if not task.paypal_order_id:
        raise HTTPException(status_code=409, detail="task has no PayPal order to capture")

    result = get_paypal_client().capture_order(task.paypal_order_id)

    problems: List[str] = []
    if result.get("status") != COMPLETED:
        problems.append(f"PayPal capture status was {result.get('status') or 'unknown'}")
    captured_amount = _money(result.get("amount"))
    expected_amount = _money(task.amount)
    if captured_amount is None or expected_amount is None or captured_amount != expected_amount:
        problems.append(f"captured amount {result.get('amount') or 'unknown'} != {task.amount}")
    if str(result.get("currency") or "").upper() != task.currency:
        problems.append(f"captured currency {result.get('currency') or 'unknown'} != {task.currency}")

    if problems:
        detail = "; ".join(problems)
        task.audit_log.append(AuditEntry(event="CAPTURE_REJECTED", detail=detail))
        task.updated_at = utc_now_iso()
        _save(task)
        raise HTTPException(status_code=409, detail=f"capture not accepted: {detail}")

    task.capture_id = str(result.get("capture_id") or "")
    transition(
        task,
        Status.FUNDED,
        "FUNDED",
        detail=(
            f"PayPal capture {task.capture_id} verified for {task.amount} "
            f"{task.currency} (status COMPLETED)"
        ),
    )
    logger.info("Task %s funded by capture %s", task.id, task.capture_id)
    return _save(task)


@app.post("/tasks/{task_id}/submit", response_model=Task)
def submit_work(task_id: str, body: SubmissionRequest) -> Task:
    """Store the submission, run the AI review, and move to REVIEWED.

    The verdict is advisory: review_submission never raises, and REVIEWED is
    where a human decides. No path from here reaches PAID without /approve.
    """
    task = _load(task_id)
    _require(task, Status.FUNDED, "accept a submission")

    task.submission = body.submission
    transition(
        task,
        Status.SUBMITTED,
        "SUBMISSION_RECEIVED",
        detail=f"{len(body.submission)} characters submitted",
    )
    _save(task)  # record the submission before the (slow) review call

    verdict = get_reviewer()(task.requirements, body.submission)
    task.ai_verdict = verdict
    transition(
        task,
        Status.REVIEWED,
        "REVIEW_COMPLETED",
        detail=(
            f"AI score {verdict.score}/100, "
            f"{'passed' if verdict.passed else 'failed'}: {verdict.reason}"
        )[:500],
    )
    logger.info("Task %s reviewed (score %s, passed=%s)", task.id, verdict.score, verdict.passed)
    return _save(task)


@app.post("/tasks/{task_id}/approve", response_model=Task)
def approve_task(task_id: str) -> Task:
    """Human approval: the only code path that can start a payout.

    Rule 1 + 3: only reachable from REVIEWED. The APPROVED state is persisted
    before PayPal is called, so a failed payout degrades to "retry me" rather
    than to a second payment.
    """
    task = _load(task_id)
    _require(task, Status.REVIEWED, "approve a reviewed task")

    score = task.ai_verdict.score if task.ai_verdict else "n/a"
    transition(
        task,
        Status.APPROVED,
        "APPROVED",
        detail=f"Approved by a human reviewer (AI score {score}); payout requested",
    )
    _save(task)

    try:
        payout = get_paypal_client().send_payout(
            task_id=task.id,
            receiver_email=task.freelancer_email,
            amount=task.amount,
            currency=task.currency,
        )
    except PayPalError as exc:
        task.audit_log.append(AuditEntry(event="PAYOUT_FAILED", detail=_payout_failure_detail(exc)))
        task.updated_at = utc_now_iso()
        _save(task)  # stays APPROVED
        raise

    task.payout_batch_id = payout["batch_id"]
    task.payout_status = payout["batch_status"]
    transition(
        task,
        Status.PAID,
        "PAID",
        detail=f"PayPal payout batch {task.payout_batch_id} ({task.payout_status or 'queued'})",
    )
    logger.info("Task %s paid via batch %s", task.id, task.payout_batch_id)
    return _save(task)


@app.post("/tasks/{task_id}/retry-payout", response_model=Task)
def retry_payout(task_id: str) -> Task:
    """Re-attempt a failed payout from APPROVED.

    send_payout reuses sender_batch_id escrowpilot_<task_id>, so PayPal rejects a
    duplicate instead of paying twice; that case is reported, not retried.
    """
    task = _load(task_id)
    _require(task, Status.APPROVED, "retry a payout")

    try:
        payout = get_paypal_client().send_payout(
            task_id=task.id,
            receiver_email=task.freelancer_email,
            amount=task.amount,
            currency=task.currency,
        )
    except PayPalError as exc:
        if _is_duplicate_batch(exc):
            task.audit_log.append(
                AuditEntry(
                    event="PAYOUT_DUPLICATE",
                    detail=(
                        f"PayPal reports batch escrowpilot_{task.id} already exists "
                        f"(debug_id={exc.debug_id or 'none'}); the deterministic "
                        "sender_batch_id prevented a second payment."
                    ),
                )
            )
            task.updated_at = utc_now_iso()
            _save(task)
            raise HTTPException(
                status_code=409,
                detail=(
                    "PayPal reports this payout batch already exists, so it was not sent "
                    "again (sender_batch_id escrowpilot_<task_id> is deterministic). "
                    f"Check GET /tasks/{task.id}/payout-status for the real status."
                ),
            ) from None
        task.audit_log.append(AuditEntry(event="PAYOUT_FAILED", detail=_payout_failure_detail(exc)))
        task.updated_at = utc_now_iso()
        _save(task)
        raise

    task.payout_batch_id = payout["batch_id"]
    task.payout_status = payout["batch_status"]
    transition(
        task,
        Status.PAID,
        "PAID",
        detail=f"PayPal payout batch {task.payout_batch_id} sent on retry",
    )
    return _save(task)


@app.post("/tasks/{task_id}/reject", response_model=Task)
def reject_task(task_id: str) -> Task:
    """Human rejection from REVIEWED. Terminal; refunds are out of scope here."""
    task = _load(task_id)
    _require(task, Status.REVIEWED, "reject a reviewed task")

    transition(
        task,
        Status.REJECTED,
        "REJECTED",
        detail="Rejected by a human reviewer. Refund not automated in demo.",
    )
    logger.info("Task %s rejected", task.id)
    return _save(task)


@app.get("/tasks/{task_id}/payout-status")
def payout_status(task_id: str) -> Dict[str, Optional[str]]:
    """Poll PayPal for the payout batch and cache what it reports.

    Never changes the task status: only /approve and /retry-payout can reach PAID.
    """
    task = _load(task_id)
    if not task.payout_batch_id:
        raise HTTPException(status_code=409, detail="no payout has been sent for this task")

    result = get_paypal_client().get_payout_status(task.payout_batch_id)
    if result.get("batch_status"):
        task.payout_status = result["batch_status"]
        task.updated_at = utc_now_iso()
        _save(task)

    return {
        "task_id": task.id,
        "status": task.status.value,
        "payout_batch_id": task.payout_batch_id,
        "batch_status": result.get("batch_status") or "",
        "item_status": result.get("item_status") or "",
    }
