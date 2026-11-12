"""State machine, model validation and repository tests. No network calls."""

from __future__ import annotations

from typing import Dict, List

import pytest
from pydantic import ValidationError

from db import InMemoryRepository
from models import Status, Task, TaskCreate, new_task
from state import ALLOWED, InvalidTransition, require_status, transition

# Legal walk from CREATED to each status, used to set up test fixtures.
LEGAL_PATH: Dict[Status, List[Status]] = {
    Status.CREATED: [],
    Status.FUNDED: [Status.FUNDED],
    Status.SUBMITTED: [Status.FUNDED, Status.SUBMITTED],
    Status.REVIEWED: [Status.FUNDED, Status.SUBMITTED, Status.REVIEWED],
    Status.APPROVED: [Status.FUNDED, Status.SUBMITTED, Status.REVIEWED, Status.APPROVED],
    Status.PAID: [Status.FUNDED, Status.SUBMITTED, Status.REVIEWED, Status.APPROVED, Status.PAID],
    Status.REJECTED: [Status.FUNDED, Status.SUBMITTED, Status.REVIEWED, Status.REJECTED],
}


def make_task(**overrides: object) -> Task:
    """A valid CREATED task, with any TaskCreate field overridable."""
    payload: Dict[str, object] = {
        "title": "Translate 200 words to Spanish",
        "requirements": "Translate the product blurb, keep the tone informal and the layout.",
        "amount": "20.00",
        "client_name": "Acme Studio",
        "freelancer_email": "freelancer@example.com",
    }
    payload.update(overrides)
    return new_task(TaskCreate(**payload))


def task_in(status: Status) -> Task:
    """A task sitting in `status`, reached only through legal transitions."""
    task = make_task()
    for step in LEGAL_PATH[status]:
        transition(task, step, event=f"TEST_{step.value}")
    return task


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_full_happy_path_created_to_paid() -> None:
    task = make_task()
    assert task.status is Status.CREATED

    for expected in (
        Status.FUNDED,
        Status.SUBMITTED,
        Status.REVIEWED,
        Status.APPROVED,
        Status.PAID,
    ):
        transition(task, expected, event=f"TEST_{expected.value}", detail="unit test")
        assert task.status is expected

    assert task.status is Status.PAID


def test_reviewed_can_be_rejected() -> None:
    task = task_in(Status.REVIEWED)
    transition(task, Status.REJECTED, event="TEST_REJECTED", detail="requirements not met")
    assert task.status is Status.REJECTED


# ---------------------------------------------------------------------------
# Illegal transitions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (Status.CREATED, Status.PAID),
        (Status.FUNDED, Status.APPROVED),
        (Status.SUBMITTED, Status.PAID),
        (Status.REVIEWED, Status.PAID),
        (Status.PAID, Status.REVIEWED),
        (Status.REJECTED, Status.APPROVED),
        (Status.CREATED, Status.SUBMITTED),
        (Status.APPROVED, Status.REJECTED),
    ],
)
def test_illegal_transitions_raise(current: Status, target: Status) -> None:
    task = task_in(current)
    log_length = len(task.audit_log)
    updated_at = task.updated_at

    with pytest.raises(InvalidTransition) as excinfo:
        transition(task, target, event="TEST_ILLEGAL")

    assert excinfo.value.current is current
    assert excinfo.value.target is target
    # A rejected transition changes nothing at all.
    assert task.status is current
    assert len(task.audit_log) == log_length
    assert task.updated_at == updated_at


def test_payout_can_only_be_started_from_approved() -> None:
    """No edge leads straight to PAID except APPROVED -> PAID."""
    assert {status for status in Status if Status.PAID in ALLOWED[status]} == {Status.APPROVED}
    assert ALLOWED[Status.REVIEWED] == frozenset({Status.APPROVED, Status.REJECTED})
    assert ALLOWED[Status.PAID] == frozenset()
    assert ALLOWED[Status.REJECTED] == frozenset()

    for status in Status:
        if status is Status.APPROVED:
            continue
        with pytest.raises(InvalidTransition):
            transition(task_in(status), Status.PAID, event="TEST_PAYOUT_TOO_EARLY")


def test_every_status_has_an_entry_in_allowed() -> None:
    assert set(ALLOWED) == set(Status)


def test_require_status() -> None:
    task = task_in(Status.REVIEWED)
    require_status(task, Status.REVIEWED)  # no raise

    with pytest.raises(InvalidTransition) as excinfo:
        require_status(task, Status.FUNDED)
    assert excinfo.value.current is Status.REVIEWED
    assert excinfo.value.target is Status.FUNDED


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def test_audit_log_grows_by_one_entry_per_transition() -> None:
    task = make_task()
    assert len(task.audit_log) == 1  # TASK_CREATED
    assert task.audit_log[0].event == "TASK_CREATED"

    for target in (Status.FUNDED, Status.SUBMITTED, Status.REVIEWED, Status.APPROVED, Status.PAID):
        before = len(task.audit_log)
        transition(task, target, event=f"TEST_{target.value}", detail="unit test")
        assert len(task.audit_log) == before + 1
        assert task.audit_log[-1].event == f"TEST_{target.value}"
        assert task.audit_log[-1].detail == "unit test"
        assert task.audit_log[-1].ts.endswith("Z")


def test_failed_transition_adds_no_audit_entry() -> None:
    task = task_in(Status.SUBMITTED)
    before = len(task.audit_log)

    with pytest.raises(InvalidTransition):
        transition(task, Status.APPROVED, event="TEST_ILLEGAL")

    assert len(task.audit_log) == before
    assert task.audit_log[-1].event != "TEST_ILLEGAL"


# ---------------------------------------------------------------------------
# TaskCreate validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("amount", ["0", "0.00", "-5", "abc", "", "   ", "100.001", "150.00", "1e-3"])
def test_task_create_rejects_bad_amounts(amount: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_TASK_AMOUNT", "100.00")
    with pytest.raises(ValidationError):
        make_task(amount=amount)


@pytest.mark.parametrize("email", ["not-an-email", "freelancer@", "@example.com", "a b@example.com"])
def test_task_create_rejects_bad_email(email: str) -> None:
    with pytest.raises(ValidationError):
        make_task(freelancer_email=email)


@pytest.mark.parametrize(
    ("amount", "expected"),
    [("20", "20.00"), ("5.5", "5.50"), ("100", "100.00"), ("0.01", "0.01"), (" 20.00 ", "20.00")],
)
def test_task_create_normalises_valid_amounts(
    amount: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_TASK_AMOUNT", "100.00")
    assert make_task(amount=amount).amount == expected


def test_task_create_rejects_non_string_amount() -> None:
    with pytest.raises(ValidationError):
        make_task(amount=20.0)


def test_task_create_enforces_env_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_TASK_AMOUNT", "10.00")
    with pytest.raises(ValidationError):
        make_task(amount="10.01")
    assert make_task(amount="10.00").amount == "10.00"


def test_task_create_rejects_empty_title_and_short_requirements() -> None:
    with pytest.raises(ValidationError):
        make_task(title="   ")
    with pytest.raises(ValidationError):
        make_task(requirements="too short")
    with pytest.raises(ValidationError):
        make_task(currency="EUR")


# ---------------------------------------------------------------------------
# InMemoryRepository
# ---------------------------------------------------------------------------


def test_inmemory_repository_round_trip() -> None:
    repo = InMemoryRepository()
    first = make_task()
    second = make_task(title="Second task")

    assert repo.create(first) is first
    repo.create(second)

    fetched = repo.get(first.id)
    assert fetched is not None
    assert fetched.id == first.id
    assert fetched.title == first.title
    assert fetched.amount == "20.00"

    listed = repo.list()
    assert {task.id for task in listed} == {first.id, second.id}
    assert [task.created_at for task in listed] == sorted(
        (task.created_at for task in listed), reverse=True
    )

    assert repo.get("does-not-exist") is None


def test_inmemory_repository_update() -> None:
    repo = InMemoryRepository()
    task = make_task()
    repo.create(task)

    transition(task, Status.FUNDED, event="TEST_FUNDED")
    task.capture_id = "CAPTURE-123"
    repo.update(task)

    stored = repo.get(task.id)
    assert stored is not None
    assert stored.status is Status.FUNDED
    assert stored.capture_id == "CAPTURE-123"
    assert len(stored.audit_log) == 2


def test_inmemory_repository_returns_copies() -> None:
    repo = InMemoryRepository()
    task = make_task()
    repo.create(task)

    fetched = repo.get(task.id)
    assert fetched is not None
    fetched.title = "mutated in memory only"
    fetched.status = Status.PAID

    again = repo.get(task.id)
    assert again is not None
    assert again.title == task.title
    assert again.status is Status.CREATED


def test_inmemory_repository_guards_duplicates_and_missing_updates() -> None:
    repo = InMemoryRepository()
    task = make_task()
    repo.create(task)

    with pytest.raises(ValueError):
        repo.create(task)

    orphan = make_task()
    with pytest.raises(KeyError):
        repo.update(orphan)
