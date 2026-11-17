"""HTTP layer: routes, caller identification and error translation.

Exposes the service under ``/api``. Each route identifies the caller from
the ``X-User-Id`` header, calls one service method and shapes the
response. Errors raised by the service are translated into a uniform JSON
body with a matching status code.

Handlers are plain ``def`` functions. SQLite calls block, so FastAPI runs
them in its thread pool and the event loop stays free.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.domain import Status, User
from app.errors import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
    PricingError,
    UnauthenticatedError,
)
from app.schemas import (
    ApplicationOut,
    ApprovedDiscountOut,
    CreateRequestIn,
    DecisionIn,
    HistoryOut,
    RequestOut,
    RevisionIn,
    UserOut,
)
from app.service import PricingExceptionService

REPLAY_HEADER = "Idempotent-Replayed"

STATUS_BY_ERROR: dict[type, int] = {
    InvalidInputError: 422,
    UnauthenticatedError: 401,
    PermissionDeniedError: 403,
    NotFoundError: 404,
    ConflictError: 409,
}

router = APIRouter(prefix="/api")


def get_service(request: Request) -> PricingExceptionService:
    """Fetch the service the app was built with.

    Args:
        request: Incoming request.

    Returns:
        The shared service instance.
    """
    service: PricingExceptionService = request.app.state.service
    return service


ServiceDep = Annotated[PricingExceptionService, Depends(get_service)]


def current_user(
    service: ServiceDep,
    x_user_id: Annotated[str | None, Header()] = None,
) -> User:
    """Identify the caller from the ``X-User-Id`` header.

    Args:
        service: The shared service instance.
        x_user_id: Value of the ``X-User-Id`` header.

    Returns:
        The calling user.
    """
    return service.authenticate(x_user_id)


ActorDep = Annotated[User, Depends(current_user)]
KeyHeader = Annotated[str | None, Header()]


@router.get("/health")
def health() -> dict[str, str]:
    """Report that the service is up.

    Returns:
        A static status payload.
    """
    return {"status": "ok"}


@router.get("/users", response_model=list[UserOut])
def list_users(service: ServiceDep) -> list[UserOut]:
    """List users so a client can offer a user picker.

    Args:
        service: The shared service instance.

    Returns:
        All users.
    """
    return [UserOut.from_domain(user) for user in service.list_users()]


@router.get("/applications", response_model=list[ApplicationOut])
def list_applications(
    service: ServiceDep, _: ActorDep
) -> list[ApplicationOut]:
    """List applications with the state of their open request.

    Args:
        service: The shared service instance.
        _: Calling user, required for access.

    Returns:
        All applications.
    """
    return [
        ApplicationOut.from_domain(view)
        for view in service.list_applications()
    ]


@router.get("/applications/{application_id}", response_model=ApplicationOut)
def get_application(
    application_id: str, service: ServiceDep, _: ActorDep
) -> ApplicationOut:
    """Get one application.

    Args:
        application_id: Application to look up.
        service: The shared service instance.
        _: Calling user, required for access.

    Returns:
        The application.
    """
    return ApplicationOut.from_domain(service.get_application(application_id))


@router.get(
    "/applications/{application_id}/approved-discount",
    response_model=ApprovedDiscountOut,
)
def get_approved_discount(
    application_id: str, service: ServiceDep, actor: ActorDep
) -> ApprovedDiscountOut:
    """Get the approved discount, for the mortgage application process.

    Args:
        application_id: Application to look up.
        service: The shared service instance.
        actor: Calling user. Must have the system role.

    Returns:
        The approved discount and resulting rate.
    """
    return ApprovedDiscountOut.from_domain(
        service.get_approved_discount(actor, application_id)
    )


@router.get("/requests", response_model=list[RequestOut])
def list_requests(
    service: ServiceDep,
    _: ActorDep,
    status: Status | None = None,
    application_id: str | None = None,
) -> list[RequestOut]:
    """List requests, newest first.

    Args:
        service: The shared service instance.
        _: Calling user, required for access.
        status: Only return requests with this status.
        application_id: Only return requests for this application.

    Returns:
        Matching requests.
    """
    requests = service.list_requests(
        status=status, application_id=application_id
    )
    return [RequestOut.from_domain(request) for request in requests]


@router.post("/requests", response_model=RequestOut, status_code=201)
def create_request(
    new_request: CreateRequestIn,
    response: Response,
    service: ServiceDep,
    actor: ActorDep,
    idempotency_key: KeyHeader = None,
) -> RequestOut:
    """Submit a pricing exception request.

    Args:
        new_request: Application, discount and reason.
        response: Response, used to mark a replay.
        service: The shared service instance.
        actor: Calling user.
        idempotency_key: Key that makes retries safe.

    Returns:
        The created request. A replayed key returns the original request
        with status 200.
    """
    submission = service.create_request(
        actor,
        idempotency_key=idempotency_key,
        application_id=new_request.application_id,
        discount_bps=new_request.discount_bps,
        reason=new_request.reason,
    )
    _mark_replay(response, submission.replayed)
    return RequestOut.from_domain(submission.request)


@router.get("/requests/{request_id}", response_model=RequestOut)
def get_request(
    request_id: str, service: ServiceDep, _: ActorDep
) -> RequestOut:
    """Get one request.

    Args:
        request_id: Request to look up.
        service: The shared service instance.
        _: Calling user, required for access.

    Returns:
        The request.
    """
    return RequestOut.from_domain(service.get_request(request_id))


@router.get("/requests/{request_id}/history", response_model=HistoryOut)
def get_history(
    request_id: str, service: ServiceDep, _: ActorDep
) -> HistoryOut:
    """Get a request with its earlier versions and events.

    Args:
        request_id: Request to look up.
        service: The shared service instance.
        _: Calling user, required for access.

    Returns:
        The request, its versions and its events.
    """
    return HistoryOut.from_domain(service.get_history(request_id))


@router.post("/requests/{request_id}/decision", response_model=RequestOut)
def decide_request(
    request_id: str,
    decision: DecisionIn,
    service: ServiceDep,
    actor: ActorDep,
) -> RequestOut:
    """Approve or decline a request.

    Args:
        request_id: Request to decide.
        decision: Outcome and note.
        service: The shared service instance.
        actor: Calling reviewer.

    Returns:
        The decided request.
    """
    request = service.decide(
        actor, request_id, outcome=Status(decision.outcome), note=decision.note
    )
    return RequestOut.from_domain(request)


@router.post("/requests/{request_id}/withdraw", response_model=RequestOut)
def withdraw_request(
    request_id: str, service: ServiceDep, actor: ActorDep
) -> RequestOut:
    """Withdraw a pending request.

    Args:
        request_id: Request to withdraw.
        service: The shared service instance.
        actor: Calling relationship manager.

    Returns:
        The withdrawn request.
    """
    return RequestOut.from_domain(service.withdraw(actor, request_id))


@router.post(
    "/requests/{request_id}/revisions",
    response_model=RequestOut,
    status_code=201,
)
def revise_request(
    request_id: str,
    revision: RevisionIn,
    response: Response,
    service: ServiceDep,
    actor: ActorDep,
    idempotency_key: KeyHeader = None,
) -> RequestOut:
    """Replace a pending request with a new one.

    Args:
        request_id: Pending request to replace.
        revision: New discount and reason.
        response: Response, used to mark a replay.
        service: The shared service instance.
        actor: Calling relationship manager.
        idempotency_key: Key that makes retries safe.

    Returns:
        The new request. A replayed key returns the original request
        with status 200.
    """
    submission = service.revise_request(
        actor,
        request_id,
        idempotency_key=idempotency_key,
        discount_bps=revision.discount_bps,
        reason=revision.reason,
    )
    _mark_replay(response, submission.replayed)
    return RequestOut.from_domain(submission.request)


def register_error_handlers(app: FastAPI) -> None:
    """Make every error use the same JSON shape.

    Args:
        app: Application to configure.
    """

    @app.exception_handler(PricingError)
    async def handle_pricing_error(
        _: Request, exc: PricingError
    ) -> JSONResponse:
        """Translate a service error into a response.

        Args:
            _: Incoming request.
            exc: The error that was raised.

        Returns:
            JSON response with the mapped status code.
        """
        return _error_response(_status_for(exc), exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Translate a malformed payload into a response.

        Args:
            _: Incoming request.
            exc: The validation error raised by FastAPI.

        Returns:
            JSON response with status 422.
        """
        first = exc.errors()[0]
        location = ".".join(
            str(part) for part in first["loc"] if part != "body"
        )
        return _error_response(
            422, InvalidInputError.code, f"{location}: {first['msg']}"
        )


def _mark_replay(response: Response, replayed: bool) -> None:
    """Flag a replayed call so clients can tell it apart.

    Args:
        response: Response to adjust.
        replayed: Whether the idempotency key had been used before.
    """
    if replayed:
        response.status_code = 200
        response.headers[REPLAY_HEADER] = "true"


def _status_for(exc: PricingError) -> int:
    """Pick the HTTP status for an error.

    Args:
        exc: The error that was raised.

    Returns:
        The status of the closest matching error class, or 400.
    """
    for error_class in type(exc).__mro__:
        if error_class in STATUS_BY_ERROR:
            return STATUS_BY_ERROR[error_class]
    return 400


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Build the shared error body.

    Args:
        status_code: HTTP status to send.
        code: Short machine-readable error code.
        message: Human-readable explanation.

    Returns:
        JSON response shaped as ``{"error": {"code": ..., "message": ...}}``.
    """
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status_code
    )
