"""Business rules for pricing exception requests.

Defines the vocabulary of the service (roles, statuses and events), the
immutable values built from it, and the three operations that change a
request: ``submit``, ``decide`` and ``withdraw``. Each operation checks
its rules and returns a ``Transition``, which is the new request together
with the event that describes the change.

The module has no knowledge of HTTP or storage, so every rule can be
tested directly. A submitted request keeps its discount and reason.
Asking for a different discount creates a new request that links back to
the one it replaces.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from app.errors import ConflictError, InvalidInputError, PermissionDeniedError


class Role(StrEnum):
    """What a user is allowed to do."""

    RELATIONSHIP_MANAGER = "relationship_manager"
    REVIEWER = "reviewer"
    SYSTEM = "system"


class Status(StrEnum):
    """Where a request is in its life."""

    PENDING = "pending"
    APPROVED = "approved"
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"


class EventType(StrEnum):
    """Things that can happen to a request."""

    SUBMITTED = "submitted"
    APPROVED = "approved"
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"


OPEN_STATUSES = frozenset({Status.PENDING, Status.APPROVED})


@dataclass(frozen=True, slots=True)
class User:
    """A person or system that calls the service.

    Attributes:
        id: Stable identifier, also used in the ``X-User-Id`` header.
        name: Display name.
        roles: Everything this user may do.
    """

    id: str
    name: str
    roles: frozenset[Role]

    def has_role(self, role: Role) -> bool:
        """Check whether the user holds a role.

        Args:
            role: Role to look for.

        Returns:
            True if the user has the role.
        """
        return role in self.roles


@dataclass(frozen=True, slots=True)
class Application:
    """A mortgage application a discount can be requested for.

    Attributes:
        id: Application identifier, for example ``APP-1001``.
        customer_name: Name shown to internal users.
        standard_rate_bps: Standard interest rate in basis points, so 421
            means 4.21%.
    """

    id: str
    customer_name: str
    standard_rate_bps: int

    def final_rate_bps(self, discount_bps: int) -> int:
        """Work out the rate after a discount.

        Args:
            discount_bps: Discount in basis points.

        Returns:
            The standard rate minus the discount, in basis points.
        """
        return self.standard_rate_bps - discount_bps


@dataclass(frozen=True, slots=True)
class Policy:
    """Limits that decide which requests are acceptable.

    Attributes:
        max_discount_bps: Largest discount a request may ask for.
        min_reason_length: Shortest reason that is still meaningful.
    """

    max_discount_bps: int = 100
    min_reason_length: int = 10


@dataclass(frozen=True, slots=True)
class PricingRequest:
    """A request for a rate discount on one application.

    Attributes:
        id: Unique request identifier.
        application_id: Application the discount is for.
        requested_by: Id of the relationship manager who asked.
        discount_bps: Requested discount in basis points.
        reason: Why the discount is justified.
        status: Current status.
        created_at: When the request was submitted.
        supersedes_id: Withdrawn request this one replaces, if any.
        decided_by: Id of the reviewer who decided, once decided.
        decided_at: When the decision was made.
        decision_note: Reviewer's explanation.
    """

    id: str
    application_id: str
    requested_by: str
    discount_bps: int
    reason: str
    status: Status
    created_at: datetime
    supersedes_id: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None


@dataclass(frozen=True, slots=True)
class RequestEvent:
    """One entry in a request's history.

    Attributes:
        request_id: Request the event belongs to.
        type: What happened.
        actor_id: Who did it.
        at: When it happened.
        discount_bps: Discount of the request at that moment.
        note: Reason or explanation given with the event.
    """

    request_id: str
    type: EventType
    actor_id: str
    at: datetime
    discount_bps: int
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Transition:
    """The result of a rule: the new request and the event that records it.

    Attributes:
        request: Request after the change.
        event: History entry describing the change.
    """

    request: PricingRequest
    event: RequestEvent


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """Remembers which request an idempotency key produced.

    Attributes:
        user_id: User who sent the key. Keys are scoped per user.
        key: The client's idempotency key.
        fingerprint: Hash of the operation and payload, used to spot reuse.
        request_id: Request the first call created.
    """

    user_id: str
    key: str
    fingerprint: str
    request_id: str


def submit(
    *,
    request_id: str,
    application: Application,
    actor: User,
    discount_bps: int,
    reason: str,
    policy: Policy,
    now: datetime,
    supersedes_id: str | None = None,
) -> Transition:
    """Create a new pending request.

    Args:
        request_id: Identifier for the new request.
        application: Application the discount is for.
        actor: User submitting the request.
        discount_bps: Requested discount in basis points.
        reason: Justification for the discount.
        policy: Limits the request must respect.
        now: Current time.
        supersedes_id: Withdrawn request this one replaces, if any.

    Returns:
        The pending request and its ``submitted`` event.

    Raises:
        PermissionDeniedError: If the actor is not a relationship manager.
        InvalidInputError: If the discount or reason breaks the policy.
    """
    if not actor.has_role(Role.RELATIONSHIP_MANAGER):
        raise PermissionDeniedError(
            "Only relationship managers can submit pricing exception requests."
        )
    reason = reason.strip()
    _validate_discount(discount_bps, application, policy)
    if len(reason) < policy.min_reason_length:
        raise InvalidInputError(
            f"Reason must be at least {policy.min_reason_length} characters."
        )
    request = PricingRequest(
        id=request_id,
        application_id=application.id,
        requested_by=actor.id,
        discount_bps=discount_bps,
        reason=reason,
        status=Status.PENDING,
        created_at=now,
        supersedes_id=supersedes_id,
    )
    event = RequestEvent(
        request.id, EventType.SUBMITTED, actor.id, now, discount_bps, reason
    )
    return Transition(request, event)


def decide(
    request: PricingRequest,
    *,
    actor: User,
    outcome: Status,
    note: str | None,
    now: datetime,
) -> Transition:
    """Approve or decline a pending request.

    Args:
        request: Request being decided.
        actor: Reviewer making the decision.
        outcome: ``Status.APPROVED`` or ``Status.DECLINED``.
        note: Explanation. Required when declining.
        now: Current time.

    Returns:
        The decided request and its event.

    Raises:
        InvalidInputError: If the outcome is not a decision, or a decline
            has no note.
        PermissionDeniedError: If the actor is not a reviewer, or is the
            requester.
        ConflictError: If the request is no longer pending.
    """
    if outcome not in (Status.APPROVED, Status.DECLINED):
        raise InvalidInputError("Outcome must be approved or declined.")
    if not actor.has_role(Role.REVIEWER):
        raise PermissionDeniedError(
            "Only reviewers can approve or decline requests."
        )
    if actor.id == request.requested_by:
        raise PermissionDeniedError(
            "Reviewers cannot decide their own requests."
        )
    _require_pending(request)
    cleaned_note = (note or "").strip() or None
    if outcome is Status.DECLINED and cleaned_note is None:
        raise InvalidInputError(
            "Add a note explaining why the request is declined."
        )
    decided = replace(
        request,
        status=outcome,
        decided_by=actor.id,
        decided_at=now,
        decision_note=cleaned_note,
    )
    event_type = (
        EventType.APPROVED
        if outcome is Status.APPROVED
        else EventType.DECLINED
    )
    event = RequestEvent(
        request.id,
        event_type,
        actor.id,
        now,
        request.discount_bps,
        cleaned_note,
    )
    return Transition(decided, event)


def withdraw(
    request: PricingRequest,
    *,
    actor: User,
    now: datetime,
    note: str | None = None,
) -> Transition:
    """Withdraw a pending request.

    Args:
        request: Request being withdrawn.
        actor: User withdrawing it. Must be the requester.
        now: Current time.
        note: Optional explanation, for example which request replaces
            this one.

    Returns:
        The withdrawn request and its event.

    Raises:
        PermissionDeniedError: If the actor is not the requester.
        ConflictError: If the request is no longer pending.
    """
    if actor.id != request.requested_by:
        raise PermissionDeniedError(
            "Only the requester can withdraw a request."
        )
    _require_pending(request)
    withdrawn = replace(request, status=Status.WITHDRAWN)
    event = RequestEvent(
        request.id,
        EventType.WITHDRAWN,
        actor.id,
        now,
        request.discount_bps,
        note,
    )
    return Transition(withdrawn, event)


def _validate_discount(
    discount_bps: int, application: Application, policy: Policy
) -> None:
    """Check a discount against the policy and the application's rate.

    Args:
        discount_bps: Requested discount in basis points.
        application: Application the discount is for.
        policy: Limits the discount must respect.

    Raises:
        InvalidInputError: If the discount is out of range.
    """
    if not 0 < discount_bps <= policy.max_discount_bps:
        raise InvalidInputError(
            "Discount must be between 1 and "
            f"{policy.max_discount_bps} basis points."
        )
    if discount_bps >= application.standard_rate_bps:
        raise InvalidInputError(
            "Discount must be smaller than the standard rate."
        )


def _require_pending(request: PricingRequest) -> None:
    """Make sure a request can still change.

    Args:
        request: Request to check.

    Raises:
        ConflictError: If the request is not pending.
    """
    if request.status is not Status.PENDING:
        raise ConflictError(f"Request is already {request.status.value}.")
