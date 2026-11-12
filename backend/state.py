"""Task state machine - the guardrail for every cent that moves.

SAFETY GUARDRAIL: money can only leave escrow by walking

    REVIEWED -> APPROVED -> PAID

There is deliberately no REVIEWED -> PAID edge, so no code path can start a
PayPal payout without a human approval recorded one step earlier, and PAID and
REJECTED are terminal (no outgoing edges at all). Every accepted transition
appends exactly one audit entry; a rejected one changes nothing.
"""

from __future__ import annotations

from typing import Dict, FrozenSet

from models import AuditEntry, Status, Task, utc_now_iso

ALLOWED: Dict[Status, FrozenSet[Status]] = {
    Status.CREATED: frozenset({Status.FUNDED}),
    Status.FUNDED: frozenset({Status.SUBMITTED}),
    Status.SUBMITTED: frozenset({Status.REVIEWED}),
    Status.REVIEWED: frozenset({Status.APPROVED, Status.REJECTED}),
    Status.APPROVED: frozenset({Status.PAID}),
    Status.PAID: frozenset(),
    Status.REJECTED: frozenset(),
}


class InvalidTransition(Exception):
    """A status change the state machine does not allow."""

    def __init__(self, current: Status, target: Status) -> None:
        self.current = current
        self.target = target
        super().__init__(f"cannot move task from {current.value} to {target.value}")


def transition(task: Task, new_status: Status, event: str, detail: str = "") -> Task:
    """Move `task` to `new_status`, or raise InvalidTransition.

    On success the task is mutated in place (status, updated_at, one audit
    entry) and returned. The caller persists it.
    """
    current = task.status
    if new_status not in ALLOWED[current]:
        raise InvalidTransition(current, new_status)

    task.status = new_status
    task.updated_at = utc_now_iso()
    task.audit_log.append(AuditEntry(event=event, detail=detail))
    return task


def require_status(task: Task, expected: Status) -> None:
    """Raise InvalidTransition unless the task is currently in `expected`."""
    if task.status is not expected:
        raise InvalidTransition(task.status, expected)
