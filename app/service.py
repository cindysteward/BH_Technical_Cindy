"""Use cases of the pricing exception service.

``PricingExceptionService`` offers one method per action a user can take:
submitting, revising, deciding and withdrawing requests, plus the reads
for requests, history and approved discounts. Each method runs in a
single transaction. It loads what the rules need, calls the domain and
saves the outcome together with its event.

Rules that need storage live here, such as allowing one open request per
application. Operations that create requests take an idempotency key,
stored with a fingerprint of the operation and payload in the same
transaction as the request. Repeating a key with the same payload returns
the original result, and reusing it with a different payload is rejected.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from app import domain
from app.domain import (
    Application,
    IdempotencyRecord,
    Policy,
    PricingRequest,
    RequestEvent,
    Role,
    Status,
    User,
)
from app.errors import (
    ConflictError,
    IdempotencyKeyReusedError,
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
    UnauthenticatedError,
)
from app.repository import Database, Store

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]

MAX_KEY_LENGTH = 128


@dataclass(frozen=True, slots=True)
class Submission:
    """Result of creating or revising a request.

    Attributes:
        request: The request that was created, or the original one on a replay.
        replayed: True if the idempotency key had been used before.
    """

    request: PricingRequest
    replayed: bool


@dataclass(frozen=True, slots=True)
class History:
    """A request with its earlier versions and all their events.

    Attributes:
        request: The request that was asked for.
        versions: The request and the requests it replaced, oldest first.
        events: Events of all versions in the order they happened.
    """

    request: PricingRequest
    versions: list[PricingRequest]
    events: list[RequestEvent]


@dataclass(frozen=True, slots=True)
class ApplicationView:
    """An application and its open request, if any.

    Attributes:
        application: The mortgage application.
        open_request: Pending or approved request of the application.
    """

    application: Application
    open_request: PricingRequest | None


@dataclass(frozen=True, slots=True)
class ApprovedDiscount:
    """The approved discount the mortgage application process asks for.

    Attributes:
        application: The mortgage application.
        request: The approved request.
        approved_by: Id of the reviewer who approved it.
        approved_at: When it was approved.
    """

    application: Application
    request: PricingRequest
    approved_by: str
    approved_at: datetime


def utc_now() -> datetime:
    """Get the current time.

    Returns:
        Timezone-aware current time in UTC.
    """
    return datetime.now(UTC)


def new_request_id() -> str:
    """Create a unique request identifier.

    Returns:
        A random UUID as a string.
    """
    return str(uuid4())


class PricingExceptionService:
    """Creates, reviews and reports on pricing exception requests."""

    def __init__(
        self,
        db: Database,
        *,
        policy: Policy,
        clock: Clock = utc_now,
        id_factory: IdFactory = new_request_id,
    ) -> None:
        """Set up the service.

        Args:
            db: Storage used for every use case.
            policy: Limits applied to new requests.
            clock: Source of the current time. Replace it in tests.
            id_factory: Source of request ids. Replace it in tests.
        """
        self._db = db
        self._policy = policy
        self._clock = clock
        self._new_id = id_factory

    def authenticate(self, user_id: str | None) -> User:
        """Look up the user making a call.

        Args:
            user_id: Value of the ``X-User-Id`` header.

        Returns:
            The matching user.

        Raises:
            UnauthenticatedError: If the id is missing or unknown.
        """
        if not user_id:
            raise UnauthenticatedError(
                "Send your user id in the X-User-Id header."
            )
        with self._db.read() as store:
            user = store.get_user(user_id)
        if user is None:
            raise UnauthenticatedError(f"Unknown user '{user_id}'.")
        return user

    def list_users(self) -> list[User]:
        """List all users, so a client can offer a user picker.

        Returns:
            All users.
        """
        with self._db.read() as store:
            return store.list_users()

    def list_applications(self) -> list[ApplicationView]:
        """List applications together with their open request.

        Returns:
            One view per application.
        """
        with self._db.read() as store:
            open_by_application = {
                open_request.application_id: open_request
                for open_request in store.list_open_requests()
            }
            return [
                ApplicationView(
                    application, open_by_application.get(application.id)
                )
                for application in store.list_applications()
            ]

    def get_application(self, application_id: str) -> ApplicationView:
        """Get one application with its open request.

        Args:
            application_id: Application to look up.

        Returns:
            The application view.

        Raises:
            NotFoundError: If the application does not exist.
        """
        with self._db.read() as store:
            application = _require_application(store, application_id)
            return ApplicationView(
                application, store.find_open_request(application_id)
            )

    def create_request(
        self,
        actor: User,
        *,
        idempotency_key: str | None,
        application_id: str,
        discount_bps: int,
        reason: str,
    ) -> Submission:
        """Submit a new pricing exception request.

        Args:
            actor: Relationship manager submitting the request.
            idempotency_key: Client-chosen key that makes retries safe.
            application_id: Application the discount is for.
            discount_bps: Requested discount in basis points.
            reason: Justification for the discount.

        Returns:
            The new request, or the original one if the key was replayed.

        Raises:
            InvalidInputError: If the key is missing, or the request breaks
                the policy.
            IdempotencyKeyReusedError: If the key was used for a different
                payload.
            NotFoundError: If the application does not exist.
            ConflictError: If the application already has an open request.
            PermissionDeniedError: If the actor is not a relationship manager.
        """
        key = _validate_key(idempotency_key)
        fingerprint = _fingerprint(
            "create", application_id, discount_bps, reason.strip()
        )
        with self._db.write() as store:
            replay = _find_replay(store, actor, key, fingerprint)
            if replay is not None:
                return Submission(replay, replayed=True)
            application = _require_application(store, application_id)
            _ensure_no_open_request(store, application_id)
            transition = domain.submit(
                request_id=self._new_id(),
                application=application,
                actor=actor,
                discount_bps=discount_bps,
                reason=reason,
                policy=self._policy,
                now=self._clock(),
            )
            store.insert_request(transition.request)
            store.add_event(transition.event)
            store.save_idempotency_record(
                IdempotencyRecord(
                    actor.id, key, fingerprint, transition.request.id
                )
            )
        logger.info(
            "request_submitted id=%s application=%s by=%s",
            transition.request.id,
            application_id,
            actor.id,
        )
        return Submission(transition.request, replayed=False)

    def revise_request(
        self,
        actor: User,
        request_id: str,
        *,
        idempotency_key: str | None,
        discount_bps: int,
        reason: str,
    ) -> Submission:
        """Replace a pending request with a new one for a new discount.

        The old request is withdrawn and the new one links back to it, in one
        transaction. A reviewer can therefore never approve a request that was
        changed after they opened it.

        Args:
            actor: Relationship manager who owns the pending request.
            request_id: Pending request to replace.
            idempotency_key: Client-chosen key that makes retries safe.
            discount_bps: New requested discount in basis points.
            reason: Justification for the new discount.

        Returns:
            The new request, or the original revision if the key was replayed.

        Raises:
            InvalidInputError: If the key is missing, or the request breaks
                the policy.
            IdempotencyKeyReusedError: If the key was used for a different
                payload.
            NotFoundError: If the request does not exist.
            PermissionDeniedError: If the actor does not own the request.
            ConflictError: If the request is no longer pending.
        """
        key = _validate_key(idempotency_key)
        fingerprint = _fingerprint(
            "revise", request_id, discount_bps, reason.strip()
        )
        with self._db.write() as store:
            replay = _find_replay(store, actor, key, fingerprint)
            if replay is not None:
                return Submission(replay, replayed=True)
            current = _require_request(store, request_id)
            application = _require_application(store, current.application_id)
            now = self._clock()
            replacement_id = self._new_id()
            withdrawn = domain.withdraw(
                current,
                actor=actor,
                now=now,
                note=f"Replaced by request {replacement_id}",
            )
            store.update_request(
                withdrawn.request, expected_status=Status.PENDING
            )
            store.add_event(withdrawn.event)
            submitted = domain.submit(
                request_id=replacement_id,
                application=application,
                actor=actor,
                discount_bps=discount_bps,
                reason=reason,
                policy=self._policy,
                now=now,
                supersedes_id=current.id,
            )
            store.insert_request(submitted.request)
            store.add_event(submitted.event)
            store.save_idempotency_record(
                IdempotencyRecord(actor.id, key, fingerprint, replacement_id)
            )
        logger.info(
            "request_revised old=%s new=%s by=%s",
            request_id,
            replacement_id,
            actor.id,
        )
        return Submission(submitted.request, replayed=False)

    def decide(
        self,
        actor: User,
        request_id: str,
        *,
        outcome: Status,
        note: str | None,
    ) -> PricingRequest:
        """Approve or decline a pending request.

        Sending the same decision again from the same reviewer is safe and
        returns the request unchanged.

        Args:
            actor: Reviewer making the decision.
            request_id: Request to decide.
            outcome: ``Status.APPROVED`` or ``Status.DECLINED``.
            note: Explanation. Required when declining.

        Returns:
            The decided request.

        Raises:
            NotFoundError: If the request does not exist.
            InvalidInputError: If the outcome is invalid, or a decline has
                no note.
            PermissionDeniedError: If the actor may not decide this request.
            ConflictError: If the request is no longer pending.
        """
        with self._db.write() as store:
            request = _require_request(store, request_id)
            if request.status is outcome and request.decided_by == actor.id:
                return request
            transition = domain.decide(
                request,
                actor=actor,
                outcome=outcome,
                note=note,
                now=self._clock(),
            )
            store.update_request(
                transition.request, expected_status=Status.PENDING
            )
            store.add_event(transition.event)
        logger.info(
            "request_%s id=%s by=%s", outcome.value, request_id, actor.id
        )
        return transition.request

    def withdraw(self, actor: User, request_id: str) -> PricingRequest:
        """Withdraw a pending request.

        Withdrawing twice is safe and returns the request unchanged.

        Args:
            actor: Relationship manager who owns the request.
            request_id: Request to withdraw.

        Returns:
            The withdrawn request.

        Raises:
            NotFoundError: If the request does not exist.
            PermissionDeniedError: If the actor does not own the request.
            ConflictError: If the request has already been decided.
        """
        with self._db.write() as store:
            request = _require_request(store, request_id)
            if (
                request.status is Status.WITHDRAWN
                and request.requested_by == actor.id
            ):
                return request
            transition = domain.withdraw(
                request, actor=actor, now=self._clock()
            )
            store.update_request(
                transition.request, expected_status=Status.PENDING
            )
            store.add_event(transition.event)
        logger.info("request_withdrawn id=%s by=%s", request_id, actor.id)
        return transition.request

    def get_request(self, request_id: str) -> PricingRequest:
        """Get one request.

        Args:
            request_id: Request to look up.

        Returns:
            The request.

        Raises:
            NotFoundError: If the request does not exist.
        """
        with self._db.read() as store:
            return _require_request(store, request_id)

    def list_requests(
        self,
        *,
        status: Status | None = None,
        application_id: str | None = None,
    ) -> list[PricingRequest]:
        """List requests, newest first.

        Args:
            status: Only return requests with this status.
            application_id: Only return requests for this application.

        Returns:
            Matching requests.
        """
        with self._db.read() as store:
            return store.list_requests(
                status=status, application_id=application_id
            )

    def get_history(self, request_id: str) -> History:
        """Get a request with the requests it replaced and all their events.

        Args:
            request_id: Request to look up.

        Returns:
            The request, its versions oldest first, and the combined events.

        Raises:
            NotFoundError: If the request does not exist.
        """
        with self._db.read() as store:
            request = _require_request(store, request_id)
            versions = [request]
            while versions[0].supersedes_id is not None:
                versions.insert(
                    0, _require_request(store, versions[0].supersedes_id)
                )
            events = store.list_events([version.id for version in versions])
        return History(request, versions, events)

    def get_approved_discount(
        self, actor: User, application_id: str
    ) -> ApprovedDiscount:
        """Get the approved discount for an application.

        This is what the mortgage application process calls.

        Args:
            actor: Caller. Must have the system role.
            application_id: Application to look up.

        Returns:
            The application and its approved request.

        Raises:
            PermissionDeniedError: If the caller is not the mortgage
                application process.
            NotFoundError: If the application does not exist, or has no
                approved request.
        """
        if not actor.has_role(Role.SYSTEM):
            raise PermissionDeniedError(
                "Only the mortgage process can read approved discounts."
            )
        with self._db.read() as store:
            application = _require_application(store, application_id)
            request = store.find_open_request(application_id)
        if (
            request is None
            or request.status is not Status.APPROVED
            or request.decided_by is None
            or request.decided_at is None
        ):
            raise NotFoundError(
                f"No approved discount for application {application_id}."
            )
        return ApprovedDiscount(
            application, request, request.decided_by, request.decided_at
        )


def _validate_key(key: str | None) -> str:
    """Check that an idempotency key is present and a sensible size.

    Args:
        key: Value of the ``Idempotency-Key`` header.

    Returns:
        The key without surrounding whitespace.

    Raises:
        InvalidInputError: If the key is missing or too long.
    """
    cleaned = (key or "").strip()
    if not cleaned:
        raise InvalidInputError(
            "Send an Idempotency-Key header so retries are safe."
        )
    if len(cleaned) > MAX_KEY_LENGTH:
        raise InvalidInputError(
            f"Idempotency-Key must be at most {MAX_KEY_LENGTH} characters."
        )
    return cleaned


def _fingerprint(*parts: object) -> str:
    """Hash an operation and its payload.

    Args:
        *parts: Values that identify the operation and what it was asked to do.

    Returns:
        Hex digest that is equal for equal inputs.
    """
    payload = json.dumps(parts, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _find_replay(
    store: Store, actor: User, key: str, fingerprint: str
) -> PricingRequest | None:
    """Return the request an earlier call with the same key produced.

    Args:
        store: Store for the current transaction.
        actor: User sending the key.
        key: The idempotency key.
        fingerprint: Hash of the current operation and payload.

    Returns:
        The original request, or None if the key has not been used.

    Raises:
        IdempotencyKeyReusedError: If the key was used with a different
            payload.
    """
    record = store.find_idempotency_record(actor.id, key)
    if record is None:
        return None
    if record.fingerprint != fingerprint:
        raise IdempotencyKeyReusedError(
            "This Idempotency-Key was already used for a different request."
        )
    return _require_request(store, record.request_id)


def _require_request(store: Store, request_id: str) -> PricingRequest:
    """Load a request or fail.

    Args:
        store: Store for the current transaction.
        request_id: Request to load.

    Returns:
        The request.

    Raises:
        NotFoundError: If the request does not exist.
    """
    request = store.get_request(request_id)
    if request is None:
        raise NotFoundError(f"Request {request_id} not found.")
    return request


def _require_application(store: Store, application_id: str) -> Application:
    """Load an application or fail.

    Args:
        store: Store for the current transaction.
        application_id: Application to load.

    Returns:
        The application.

    Raises:
        NotFoundError: If the application does not exist.
    """
    application = store.get_application(application_id)
    if application is None:
        raise NotFoundError(f"Application {application_id} not found.")
    return application


def _ensure_no_open_request(store: Store, application_id: str) -> None:
    """Make sure an application has no pending or approved request.

    Args:
        store: Store for the current transaction.
        application_id: Application to check.

    Raises:
        ConflictError: If an open request exists.
    """
    open_request = store.find_open_request(application_id)
    if open_request is not None:
        raise ConflictError(
            f"Application {application_id} already has a "
            f"{open_request.status.value} "
            f"request ({open_request.id})."
        )
