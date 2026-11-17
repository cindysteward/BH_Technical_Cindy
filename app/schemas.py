"""Request and response models of the HTTP API.

Input models validate shape and types, rejecting unknown fields and
non-integer discounts before any business rule runs. Output models turn
domain objects and service results into the JSON the API returns.
Business rules live in ``domain``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain import (
    EventType,
    PricingRequest,
    RequestEvent,
    Role,
    Status,
    User,
)
from app.service import ApplicationView, ApprovedDiscount, History


class CreateRequestIn(BaseModel):
    """Body for submitting a request."""

    model_config = ConfigDict(extra="forbid")

    application_id: str = Field(min_length=1, max_length=64)
    discount_bps: int = Field(strict=True, gt=0)
    reason: str = Field(min_length=1, max_length=1000)


class RevisionIn(BaseModel):
    """Body for replacing a pending request."""

    model_config = ConfigDict(extra="forbid")

    discount_bps: int = Field(strict=True, gt=0)
    reason: str = Field(min_length=1, max_length=1000)


class DecisionIn(BaseModel):
    """Body for approving or declining a request."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["approved", "declined"]
    note: str | None = Field(default=None, max_length=1000)


class RequestOut(BaseModel):
    """A pricing exception request."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    application_id: str
    requested_by: str
    discount_bps: int
    reason: str
    status: Status
    created_at: datetime
    supersedes_id: str | None
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None

    @classmethod
    def from_domain(cls, request: PricingRequest) -> RequestOut:
        """Convert a domain request.

        Args:
            request: Request to convert.

        Returns:
            The response model.
        """
        return cls.model_validate(request)


class EventOut(BaseModel):
    """One entry in a request's history."""

    model_config = ConfigDict(from_attributes=True)

    request_id: str
    type: EventType
    actor_id: str
    at: datetime
    discount_bps: int
    note: str | None

    @classmethod
    def from_domain(cls, event: RequestEvent) -> EventOut:
        """Convert a domain event.

        Args:
            event: Event to convert.

        Returns:
            The response model.
        """
        return cls.model_validate(event)


class HistoryOut(BaseModel):
    """A request with its earlier versions and events."""

    request: RequestOut
    versions: list[RequestOut]
    events: list[EventOut]

    @classmethod
    def from_domain(cls, history: History) -> HistoryOut:
        """Convert a service history.

        Args:
            history: History to convert.

        Returns:
            The response model.
        """
        return cls(
            request=RequestOut.from_domain(history.request),
            versions=[
                RequestOut.from_domain(version) for version in history.versions
            ],
            events=[EventOut.from_domain(event) for event in history.events],
        )


class ApplicationOut(BaseModel):
    """A mortgage application and the state of its open request."""

    id: str
    customer_name: str
    standard_rate_bps: int
    open_request_id: str | None
    open_request_status: Status | None

    @classmethod
    def from_domain(cls, view: ApplicationView) -> ApplicationOut:
        """Convert a service application view.

        Args:
            view: View to convert.

        Returns:
            The response model.
        """
        open_request = view.open_request
        return cls(
            id=view.application.id,
            customer_name=view.application.customer_name,
            standard_rate_bps=view.application.standard_rate_bps,
            open_request_id=open_request.id if open_request else None,
            open_request_status=open_request.status if open_request else None,
        )


class ApprovedDiscountOut(BaseModel):
    """The approved discount, as the mortgage process needs it."""

    application_id: str
    request_id: str
    discount_bps: int
    standard_rate_bps: int
    final_rate_bps: int
    approved_by: str
    approved_at: datetime

    @classmethod
    def from_domain(cls, approved: ApprovedDiscount) -> ApprovedDiscountOut:
        """Convert a service result.

        Args:
            approved: Approved discount to convert.

        Returns:
            The response model.
        """
        request = approved.request
        return cls(
            application_id=approved.application.id,
            request_id=request.id,
            discount_bps=request.discount_bps,
            standard_rate_bps=approved.application.standard_rate_bps,
            final_rate_bps=approved.application.final_rate_bps(
                request.discount_bps
            ),
            approved_by=approved.approved_by,
            approved_at=approved.approved_at,
        )


class UserOut(BaseModel):
    """A user of the service."""

    id: str
    name: str
    roles: list[Role]

    @classmethod
    def from_domain(cls, user: User) -> UserOut:
        """Convert a domain user.

        Args:
            user: User to convert.

        Returns:
            The response model, with roles in a stable order.
        """
        return cls(id=user.id, name=user.name, roles=sorted(user.roles))
