"""Storage guarantees that must hold (even with bugs)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain import PricingRequest, Status
from app.errors import ConflictError
from app.repository import SqliteDatabase

NOW = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


def make_request(request_id: str, status: Status) -> PricingRequest:
    """Build a request for APP-1001 with the given id and status."""
    return PricingRequest(
        id=request_id,
        application_id="APP-1001",
        requested_by="arthur",
        discount_bps=25,
        reason="Customer has a competing offer.",
        status=status,
        created_at=NOW,
    )


def test_invariant_database_refuses_two_open_requests_for_one_application(
    db: SqliteDatabase,
) -> None:
    """The unique index blocks a second open request on its own."""
    with db.write() as store:
        store.insert_request(make_request("r1", Status.PENDING))
    with pytest.raises(ConflictError), db.write() as store:
        store.insert_request(make_request("r2", Status.APPROVED))


def test_closed_requests_do_not_block_new_ones(db: SqliteDatabase) -> None:
    """Withdrawn and declined requests do not block a new request."""
    with db.write() as store:
        store.insert_request(make_request("r1", Status.WITHDRAWN))
        store.insert_request(make_request("r2", Status.DECLINED))
        store.insert_request(make_request("r3", Status.PENDING))
        assert store.find_open_request("APP-1001") is not None


def test_a_failed_transaction_rolls_back(db: SqliteDatabase) -> None:
    """Nothing is stored when the block raises."""
    with pytest.raises(RuntimeError), db.write() as store:
        store.insert_request(make_request("r1", Status.PENDING))
        raise RuntimeError("boom")
    with db.read() as store:
        assert store.get_request("r1") is None


def test_update_detects_a_stale_status(db: SqliteDatabase) -> None:
    """An update that expects the wrong status is refused."""
    with db.write() as store:
        store.insert_request(make_request("r1", Status.PENDING))
    with pytest.raises(ConflictError), db.write() as store:
        store.update_request(
            make_request("r1", Status.APPROVED),
            expected_status=Status.DECLINED,
        )
