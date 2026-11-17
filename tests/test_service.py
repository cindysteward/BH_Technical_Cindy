"""Use case tests against a real SQLite file, including concurrency."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any

import pytest

from app.domain import Status, User
from app.errors import (
    ConflictError,
    IdempotencyKeyReusedError,
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
)
from app.service import PricingExceptionService

REASON = "Customer has a competing offer."


def approved_discount(
    service: PricingExceptionService, users: dict[str, User]
) -> Any:
    """Read APP-1001's approved discount as the mortgage process."""
    return service.get_approved_discount(users["mortgage-engine"], "APP-1001")


def submit_request(
    service: PricingExceptionService,
    actor: User,
    *,
    key: str = "k1",
    application_id: str = "APP-1001",
    discount_bps: int = 25,
) -> Any:
    """Submit a request with sensible defaults."""
    return service.create_request(
        actor,
        idempotency_key=key,
        application_id=application_id,
        discount_bps=discount_bps,
        reason=REASON,
    )


def run_in_parallel(calls: Sequence[Callable[[], Any]]) -> list[Any]:
    """Run calls on separate threads and collect each result or exception."""

    def run_and_capture(call: Callable[[], Any]) -> Any:
        """Run one call and return its result or the exception it raised."""
        try:
            return call()
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(run_and_capture, calls))


def test_invariant_retry_with_same_key_returns_the_original(
    service: PricingExceptionService, users: dict[str, User]
) -> None:
    """Replaying a create returns the first request, creating nothing."""
    first = submit_request(service, users["arthur"])
    second = submit_request(service, users["arthur"])
    assert not first.replayed
    assert second.replayed
    assert second.request.id == first.request.id
    assert len(service.list_requests()) == 1
    assert len(service.get_history(first.request.id).events) == 1


def test_reusing_a_key_for_a_different_payload_is_refused(
    service: PricingExceptionService, users: dict[str, User]
) -> None:
    """The same key with another discount is an error, not a silent replay."""
    submit_request(service, users["arthur"])
    with pytest.raises(IdempotencyKeyReusedError):
        submit_request(service, users["arthur"], discount_bps=30)


def test_idempotency_keys_are_scoped_per_user(
    service: PricingExceptionService, users: dict[str, User]
) -> None:
    """Two users can use the same key for different applications."""
    first = submit_request(service, users["arthur"], application_id="APP-1001")
    second = submit_request(service, users["ford"], application_id="APP-1002")
    assert first.request.id != second.request.id


def test_a_missing_or_oversized_key_is_refused(
    service: PricingExceptionService, users: dict[str, User]
) -> None:
    """Creating without a usable idempotency key is refused."""
    with pytest.raises(InvalidInputError):
        service.create_request(
            users["arthur"],
            idempotency_key=None,
            application_id="APP-1001",
            discount_bps=25,
            reason=REASON,
        )
    with pytest.raises(InvalidInputError):
        submit_request(service, users["arthur"], key="x" * 200)


def test_invariant_one_open_request_per_application(
    service: PricingExceptionService, users: dict[str, User]
) -> None:
    """A second request is refused until the first one is closed."""
    first = submit_request(service, users["arthur"])
    with pytest.raises(ConflictError):
        submit_request(service, users["ford"], key="k2")
    service.decide(
        users["tricia"], first.request.id, outcome=Status.APPROVED, note=None
    )
    with pytest.raises(ConflictError):
        submit_request(service, users["ford"], key="k3")

    other = submit_request(
        service, users["arthur"], key="k4", application_id="APP-1002"
    )
    service.decide(
        users["tricia"],
        other.request.id,
        outcome=Status.DECLINED,
        note="Not justified.",
    )
    again = submit_request(
        service, users["arthur"], key="k5", application_id="APP-1002"
    )
    assert again.request.status is Status.PENDING


def test_unknown_application_is_not_found(
    service: PricingExceptionService, users: dict[str, User]
) -> None:
    """Requests for applications that do not exist are refused."""
    with pytest.raises(NotFoundError):
        submit_request(service, users["arthur"], application_id="APP-9999")


def test_invariant_revision_replaces_the_request_and_links_back(
    service: PricingExceptionService, users: dict[str, User]
) -> None:
    """Changing a discount withdraws the old request and links a new one."""
    first = submit_request(service, users["arthur"]).request
    revised = service.revise_request(
        users["arthur"],
        first.id,
        idempotency_key="rev1",
        discount_bps=35,
        reason=REASON,
    ).request

    assert service.get_request(first.id).status is Status.WITHDRAWN
    assert service.get_request(first.id).discount_bps == 25
    assert revised.status is Status.PENDING
    assert revised.discount_bps == 35
    assert revised.supersedes_id == first.id
    open_requests = service.list_requests(
        application_id="APP-1001", status=Status.PENDING
    )
    assert [request.id for request in open_requests] == [revised.id]


def test_invariant_an_approval_applies_to_the_request_that_was_reviewed(
    service: PricingExceptionService, users: dict[str, User]
) -> None:
    """An approval can only attach to the exact version that was shown."""
    first = submit_request(service, users["arthur"]).request
    revised = service.revise_request(
        users["arthur"],
        first.id,
        idempotency_key="rev1",
        discount_bps=35,
        reason=REASON,
    ).request

    with pytest.raises(ConflictError):
        service.decide(
            users["tricia"], first.id, outcome=Status.APPROVED, note=None
        )

    approved = service.decide(
        users["tricia"], revised.id, outcome=Status.APPROVED, note=None
    )
    discount = approved_discount(service, users)
    assert discount.request.id == approved.id
    assert discount.request.discount_bps == 35
    approval_events = [
        event
        for event in service.get_history(approved.id).events
        if event.type.value == "approved"
    ]
    assert [event.discount_bps for event in approval_events] == [35]


def test_a_failed_revision_changes_nothing(
    service: PricingExceptionService, users: dict[str, User]
) -> None:
    """An invalid replacement leaves the original pending and untouched."""
    first = submit_request(service, users["arthur"]).request
    with pytest.raises(InvalidInputError):
        service.revise_request(
            users["arthur"],
            first.id,
            idempotency_key="rev1",
            discount_bps=999,
            reason=REASON,
        )
    assert service.get_request(first.id).status is Status.PENDING
    assert len(service.get_history(first.id).events) == 1


def test_revision_retry_returns_the_same_replacement(
    service: PricingExceptionService, users: dict[str, User]
) -> None:
    """Replaying a revision does not withdraw or create anything twice."""
    first = submit_request(service, users["arthur"]).request
    one = service.revise_request(
        users["arthur"],
        first.id,
        idempotency_key="rev1",
        discount_bps=35,
        reason=REASON,
    )
    two = service.revise_request(
        users["arthur"],
        first.id,
        idempotency_key="rev1",
        discount_bps=35,
        reason=REASON,
    )
    assert two.replayed
    assert two.request.id == one.request.id
    assert len(service.list_requests()) == 2


def test_only_the_owner_can_revise_or_withdraw(
    service: PricingExceptionService, users: dict[str, User]
) -> None:
    """Another relationship manager cannot change someone else's request."""
    first = submit_request(service, users["arthur"]).request
    with pytest.raises(PermissionDeniedError):
        service.revise_request(
            users["ford"],
            first.id,
            idempotency_key="r",
            discount_bps=30,
            reason=REASON,
        )
    with pytest.raises(PermissionDeniedError):
        service.withdraw(users["ford"], first.id)


def test_decision_and_withdrawal_retries_are_safe(
    service: PricingExceptionService, users: dict[str, User]
) -> None:
    """Repeating a decision or withdrawal adds no second event."""
    one = submit_request(service, users["arthur"]).request
    service.decide(users["tricia"], one.id, outcome=Status.APPROVED, note=None)
    service.decide(users["tricia"], one.id, outcome=Status.APPROVED, note=None)
    assert [
        event.type.value for event in service.get_history(one.id).events
    ] == [
        "submitted",
        "approved",
    ]

    two = submit_request(
        service, users["arthur"], key="k2", application_id="APP-1002"
    ).request
    service.withdraw(users["arthur"], two.id)
    service.withdraw(users["arthur"], two.id)
    assert [
        event.type.value for event in service.get_history(two.id).events
    ] == [
        "submitted",
        "withdrawn",
    ]


def test_another_reviewer_cannot_overturn_a_decision(
    service: PricingExceptionService, users: dict[str, User]
) -> None:
    """A decided request is final for everyone, including other reviewers."""
    request = submit_request(service, users["arthur"]).request
    service.decide(
        users["tricia"], request.id, outcome=Status.DECLINED, note="No."
    )
    with pytest.raises(ConflictError):
        service.decide(
            users["fenchurch"], request.id, outcome=Status.APPROVED, note=None
        )


def test_approved_discount_is_only_available_once_approved(
    service: PricingExceptionService, users: dict[str, User]
) -> None:
    """Pending and declined requests expose nothing to the mortgage process."""
    request = submit_request(service, users["arthur"]).request
    with pytest.raises(NotFoundError):
        approved_discount(service, users)
    service.decide(
        users["tricia"], request.id, outcome=Status.DECLINED, note="No."
    )
    with pytest.raises(NotFoundError):
        approved_discount(service, users)


def test_approved_discount_reports_decision_details(
    service: PricingExceptionService, users: dict[str, User]
) -> None:
    """The mortgage process learns the discount, who approved it and when."""
    request = submit_request(service, users["arthur"]).request
    service.decide(
        users["tricia"], request.id, outcome=Status.APPROVED, note="Fine."
    )
    approved = approved_discount(service, users)
    assert approved.request.discount_bps == 25
    assert approved.approved_by == "tricia"
    assert approved.application.final_rate_bps(25) == 421 - 25


def test_only_the_mortgage_process_can_read_approved_discounts(
    service: PricingExceptionService, users: dict[str, User]
) -> None:
    """Managers and reviewers get a permission error, not the discount."""
    request = submit_request(service, users["arthur"]).request
    service.decide(
        users["tricia"], request.id, outcome=Status.APPROVED, note="Fine."
    )
    for name in ("arthur", "tricia"):
        with pytest.raises(PermissionDeniedError):
            service.get_approved_discount(users[name], "APP-1001")


def test_invariant_history_records_every_change(
    service: PricingExceptionService, users: dict[str, User]
) -> None:
    """History lists every version and every event in order."""
    first = submit_request(service, users["arthur"]).request
    second = service.revise_request(
        users["arthur"],
        first.id,
        idempotency_key="r1",
        discount_bps=30,
        reason=REASON,
    ).request
    third = service.revise_request(
        users["arthur"],
        second.id,
        idempotency_key="r2",
        discount_bps=20,
        reason=REASON,
    ).request
    service.decide(
        users["tricia"], third.id, outcome=Status.APPROVED, note="Good."
    )

    history = service.get_history(third.id)
    assert [version.id for version in history.versions] == [
        first.id,
        second.id,
        third.id,
    ]
    assert [
        (event.request_id, event.type.value) for event in history.events
    ] == [
        (first.id, "submitted"),
        (first.id, "withdrawn"),
        (second.id, "submitted"),
        (second.id, "withdrawn"),
        (third.id, "submitted"),
        (third.id, "approved"),
    ]


def test_authenticate_rejects_missing_and_unknown_users(
    service: PricingExceptionService,
) -> None:
    """Callers must name a user that exists."""
    from app.errors import UnauthenticatedError

    with pytest.raises(UnauthenticatedError):
        service.authenticate(None)
    with pytest.raises(UnauthenticatedError):
        service.authenticate("nobody")


def test_concurrent_retries_create_exactly_one_request(
    service: PricingExceptionService, users: dict[str, User]
) -> None:
    """Eight parallel calls with one key give one request, seven replays."""
    results = run_in_parallel(
        [lambda: submit_request(service, users["arthur"]) for _ in range(8)]
    )
    assert all(not isinstance(outcome, Exception) for outcome in results)
    assert len({outcome.request.id for outcome in results}) == 1
    assert sum(outcome.replayed for outcome in results) == 7
    assert len(service.list_requests()) == 1


def test_concurrent_requests_for_one_application_allow_only_one(
    service: PricingExceptionService, users: dict[str, User]
) -> None:
    """Eight keys racing for one application: one wins, seven conflict."""
    calls = [
        partial(submit_request, service, users["arthur"], key=f"k{attempt}")
        for attempt in range(8)
    ]
    results = run_in_parallel(calls)
    created = [
        outcome for outcome in results if not isinstance(outcome, Exception)
    ]
    conflicts = [
        outcome for outcome in results if isinstance(outcome, ConflictError)
    ]
    assert len(created) == 1
    assert len(conflicts) == 7
    assert len(service.list_requests()) == 1


def test_concurrent_decisions_have_a_single_winner(
    service: PricingExceptionService, users: dict[str, User]
) -> None:
    """A racing approval and decline leave exactly one decision."""
    request = submit_request(service, users["arthur"]).request
    results = run_in_parallel(
        [
            lambda: service.decide(
                users["tricia"], request.id, outcome=Status.APPROVED, note=None
            ),
            lambda: service.decide(
                users["fenchurch"],
                request.id,
                outcome=Status.DECLINED,
                note="No.",
            ),
        ]
    )
    assert sum(isinstance(outcome, ConflictError) for outcome in results) == 1
    decisions = [
        event
        for event in service.get_history(request.id).events
        if event.type.value != "submitted"
    ]
    assert len(decisions) == 1
