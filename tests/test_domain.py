"""Unit tests for the business rules."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app import domain
from app.domain import Application, Policy, PricingRequest, Role, Status, User
from app.errors import ConflictError, InvalidInputError, PermissionDeniedError

NOW = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
APPLICATION = Application("APP-1", "Test household", 400)
POLICY = Policy(max_discount_bps=100, min_reason_length=10)
Arthur = User("arthur", "Arthur", frozenset({Role.RELATIONSHIP_MANAGER}))
Tricia = User("tricia", "Tricia", frozenset({Role.REVIEWER}))
Cindy = User(
    "cindy", "Cindy", frozenset({Role.RELATIONSHIP_MANAGER, Role.REVIEWER})
)
REASON = "Customer has a competing offer."


def pending_request(requested_by: User = Arthur) -> PricingRequest:
    """Build a pending request owned by the given user."""
    return domain.submit(
        request_id="r1",
        application=APPLICATION,
        actor=requested_by,
        discount_bps=25,
        reason=REASON,
        policy=POLICY,
        now=NOW,
    ).request


def test_submit_creates_pending_request_and_event() -> None:
    """A valid submission is pending and recorded as a submitted event."""
    transition = domain.submit(
        request_id="r1",
        application=APPLICATION,
        actor=Arthur,
        discount_bps=25,
        reason=f"  {REASON}  ",
        policy=POLICY,
        now=NOW,
    )
    assert transition.request.status is Status.PENDING
    assert transition.request.reason == REASON
    assert transition.event.type.value == "submitted"
    assert transition.event.discount_bps == 25


def test_only_relationship_managers_can_submit() -> None:
    """Reviewers who are not also relationship managers cannot submit."""
    with pytest.raises(PermissionDeniedError):
        domain.submit(
            request_id="r1",
            application=APPLICATION,
            actor=Tricia,
            discount_bps=25,
            reason=REASON,
            policy=POLICY,
            now=NOW,
        )


@pytest.mark.parametrize("discount", [0, -5, 101, 400, 500])
def test_discount_must_be_within_policy_and_below_the_rate(
    discount: int,
) -> None:
    """Zero, negative, over-limit and rate-wiping discounts are refused."""
    with pytest.raises(InvalidInputError):
        domain.submit(
            request_id="r1",
            application=APPLICATION,
            actor=Arthur,
            discount_bps=discount,
            reason=REASON,
            policy=POLICY,
            now=NOW,
        )


def test_discount_below_the_standard_rate_is_refused_even_within_policy() -> (
    None
):
    """A discount equal to the whole rate is refused, even within policy."""
    cheap = Application("APP-2", "Low rate", 50)
    with pytest.raises(InvalidInputError):
        domain.submit(
            request_id="r1",
            application=cheap,
            actor=Arthur,
            discount_bps=50,
            reason=REASON,
            policy=POLICY,
            now=NOW,
        )


def test_reason_must_be_meaningful() -> None:
    """A reason shorter than the policy minimum is refused."""
    with pytest.raises(InvalidInputError):
        domain.submit(
            request_id="r1",
            application=APPLICATION,
            actor=Arthur,
            discount_bps=25,
            reason="   ok   ",
            policy=POLICY,
            now=NOW,
        )


def test_approval_records_who_and_when() -> None:
    """An approval stores the reviewer, the time and the note."""
    transition = domain.decide(
        pending_request(),
        actor=Tricia,
        outcome=Status.APPROVED,
        note="Fine.",
        now=NOW,
    )
    assert transition.request.status is Status.APPROVED
    assert transition.request.decided_by == "tricia"
    assert transition.request.decided_at == NOW
    assert transition.event.discount_bps == 25


def test_decline_requires_a_note() -> None:
    """Declining without a note is refused, approving without one is fine."""
    with pytest.raises(InvalidInputError):
        domain.decide(
            pending_request(),
            actor=Tricia,
            outcome=Status.DECLINED,
            note="  ",
            now=NOW,
        )
    approved = domain.decide(
        pending_request(),
        actor=Tricia,
        outcome=Status.APPROVED,
        note=None,
        now=NOW,
    )
    assert approved.request.decision_note is None


def test_invariant_reviewer_cannot_decide_own_request() -> None:
    """A user with both roles cannot decide their own request."""
    with pytest.raises(PermissionDeniedError):
        domain.decide(
            pending_request(Cindy),
            actor=Cindy,
            outcome=Status.APPROVED,
            note=None,
            now=NOW,
        )


def test_only_reviewers_can_decide() -> None:
    """A relationship manager cannot approve a colleague's request."""
    with pytest.raises(PermissionDeniedError):
        domain.decide(
            pending_request(),
            actor=Arthur,
            outcome=Status.APPROVED,
            note=None,
            now=NOW,
        )


def test_outcome_must_be_a_decision() -> None:
    """Only approved and declined are valid outcomes."""
    with pytest.raises(InvalidInputError):
        domain.decide(
            pending_request(),
            actor=Tricia,
            outcome=Status.WITHDRAWN,
            note=None,
            now=NOW,
        )


@pytest.mark.parametrize("outcome", [Status.APPROVED, Status.DECLINED])
def test_invariant_a_decided_request_is_final(outcome: Status) -> None:
    """Once decided, a request is final."""
    decided = domain.decide(
        pending_request(),
        actor=Tricia,
        outcome=outcome,
        note="Reason.",
        now=NOW,
    ).request
    with pytest.raises(ConflictError):
        domain.decide(
            decided,
            actor=Tricia,
            outcome=Status.APPROVED,
            note="Again.",
            now=NOW,
        )


def test_withdraw_is_limited_to_the_owner_and_pending_requests() -> None:
    """Only the requester can withdraw, and only while pending."""
    with pytest.raises(PermissionDeniedError):
        domain.withdraw(pending_request(), actor=Tricia, now=NOW)
    approved = domain.decide(
        pending_request(),
        actor=Tricia,
        outcome=Status.APPROVED,
        note=None,
        now=NOW,
    ).request
    with pytest.raises(ConflictError):
        domain.withdraw(approved, actor=Arthur, now=NOW)
    withdrawn = domain.withdraw(
        pending_request(), actor=Arthur, now=NOW
    ).request
    assert withdrawn.status is Status.WITHDRAWN


def test_decisions_leave_the_original_request_untouched() -> None:
    """Deciding returns a new value and never mutates the reviewed request."""
    original = pending_request()
    domain.decide(
        original, actor=Tricia, outcome=Status.APPROVED, note=None, now=NOW
    )
    assert original.status is Status.PENDING
    assert original.decided_by is None
